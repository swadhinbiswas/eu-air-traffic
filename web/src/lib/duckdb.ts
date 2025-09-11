/**
 * Lazy DuckDB-WASM engine for the in-browser SQL explorer.
 *
 * Registers the static bundle datasets as tables so the SQL page works with no
 * backend at all (pure static hosting), while the live API remains an option.
 */
import * as duckdb from "@duckdb/duckdb-wasm";
import { API_BASE, loadBundle, type Analytics, type Airport, type Metar, type Position } from "./bundle";

export type SqlRow = Record<string, unknown>;
export type SqlEngineKind = "wasm" | "live";

export interface SqlResult {
  rows: SqlRow[];
  columns: string[];
  ms: number;
}

// ── Native backend path (FastAPI + DuckDB) ──────────────────────────────────
export async function liveTables(): Promise<string[]> {
  const res = await fetch(`${API_BASE.replace(/\/$/, "")}/warehouse/tables`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = (await res.json()) as Record<string, number>;
  return Object.keys(data);
}

export async function runSqlLive(sql: string): Promise<SqlResult> {
  const started = performance.now();
  const res = await fetch(`${API_BASE.replace(/\/$/, "")}/warehouse/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sql }),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      detail = ((await res.json()) as { detail?: string }).detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  const rows = (await res.json()) as SqlRow[];
  const columns = rows.length ? Object.keys(rows[0]) : [];
  return { rows, columns, ms: Math.round(performance.now() - started) };
}

// ── In-browser DuckDB-WASM path ─────────────────────────────────────────────
let connPromise: Promise<duckdb.AsyncDuckDBConnection> | null = null;

async function setup(): Promise<duckdb.AsyncDuckDBConnection> {
  // Load the WASM engine from the jsDelivr CDN so the static bundle stays
  // small and sidesteps per-file size limits on static hosts.
  const bundles = duckdb.getJsDelivrBundles();
  const bundle = await duckdb.selectBundle(bundles);
  const worker = await duckdb.createWorker(bundle.mainWorker as string);
  const logger = new duckdb.VoidLogger();
  const db = new duckdb.AsyncDuckDB(logger, worker);
  await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
  const conn = await db.connect();

  const [airports, positions, metars, analytics] = await Promise.all([
    loadBundle<Airport[]>("airports.json"),
    loadBundle<Position[]>("positions.json"),
    loadBundle<Metar[]>("metars.json"),
    loadBundle<Analytics>("analytics.json"),
  ]);

  async function registerJson(name: string, data: unknown, table: string) {
    if (!Array.isArray(data) || data.length === 0) return;
    const file = `${name}.json`;
    await db.registerFileText(file, JSON.stringify(data));
    await conn!.insertJSONFromPath(file, { name: table, create: true });
  }

  await registerJson("dim_airport", airports, "dim_airport");
  await registerJson("live_positions", positions, "live_positions");
  await registerJson("latest_metar", metars, "latest_metar");
  await registerJson("gold_airport_metrics", analytics.gold_airport_metrics, "gold_airport_metrics");
  await registerJson("gold_airline_rankings", analytics.gold_airline_rankings, "gold_airline_rankings");
  await registerJson("gold_delay_analysis", analytics.gold_delay_analysis, "gold_delay_analysis");
  await registerJson("gold_weather_impact", analytics.gold_weather_impact, "gold_weather_impact");
  await registerJson("gold_seasonal_trends", analytics.gold_seasonal_trends, "gold_seasonal_trends");
  await registerJson("gold_fuel_price_series", analytics.gold_fuel_price_series, "gold_fuel_price_series");
  await registerJson("dim_route", analytics.routes, "dim_route");
  await registerJson("fact_emissions", analytics.emissions, "fact_emissions");
  await registerJson("dim_aircraft", analytics.fleet, "dim_aircraft");
  await registerJson("status_mix", analytics.status_mix, "status_mix");

  return conn;
}

export function getConnection(): Promise<duckdb.AsyncDuckDBConnection> {
  if (!connPromise) {
    connPromise = setup().catch(() => {
      connPromise = null;
      throw new Error("Failed to initialise the in-browser DuckDB engine.");
    });
  }
  return connPromise;
}

/** Drop the cached connection so a retry starts a fresh worker. */
export function resetEngine(): void {
  connPromise = null;
}

function sanitize(value: unknown): unknown {
  if (typeof value === "bigint") return Number(value);
  if (value instanceof Date) return value.toISOString();
  if (Array.isArray(value)) return value.map(sanitize);
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value)) out[k] = sanitize(v);
    return out;
  }
  return value;
}

export async function runSql(sql: string): Promise<SqlResult> {
  const conn = await getConnection();
  const started = performance.now();
  const table = (await conn.query(sql)) as {
    schema: { fields: Array<{ name: string }> };
    toArray: () => Array<{ toJSON?: () => unknown }>;
  };
  const columns = table.schema.fields.map((f) => f.name);
  const rows = table.toArray().map((r) => sanitize(r.toJSON ? r.toJSON() : r) as SqlRow);
  return { rows, columns, ms: Math.round(performance.now() - started) };
}

export async function listTables(): Promise<string[]> {
  const conn = await getConnection();
  const result = (await conn.query("SHOW TABLES")) as {
    schema: { fields: Array<{ name: string }> };
    toArray: () => Array<{ toJSON?: () => unknown }>;
  };
  const field = result.schema.fields[0]?.name ?? "name";
  return result.toArray().map((r) => String((r.toJSON?.() as Record<string, unknown>)?.[field] ?? "")).filter(Boolean);
}

export function engineAvailable(): boolean {
  return typeof Worker !== "undefined" && typeof WebAssembly !== "undefined";
}

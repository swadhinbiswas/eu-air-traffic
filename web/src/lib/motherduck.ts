/**
 * Direct MotherDuck reader for the site (Gold layer).
 *
 * The Gold marts live in MotherDuck and the site queries them straight from
 * the browser via the official MotherDuck WASM client — no warehouse API, no
 * static bundle. Live aircraft/weather still come from the VPS snapshot.
 *
 * SECURITY: only a MotherDuck **read-only** access token may be shipped here
 * (`VITE_MOTHERDUCK_TOKEN`). Never put the read-write pipeline token in `VITE_*`.
 */
import { MDConnection, terminateDuckDB } from "@motherduck/wasm-client";

export type MotherDuckRow = Record<string, unknown>;

const TOKEN = (import.meta.env.VITE_MOTHERDUCK_TOKEN as string | undefined) || "";
const DATABASE = (import.meta.env.VITE_MOTHERDUCK_DATABASE as string | undefined) || "my_db";

let conn: MDConnection | null = null;
let dbReady: Promise<void> | null = null;

export function motherduckConfigured(): boolean {
  return Boolean(TOKEN);
}

function getConnection(): MDConnection {
  if (!conn) {
    conn = MDConnection.create({
      mdToken: TOKEN,
      accessMode: "read_only",
      attachMode: "single",
    } as never);
  }
  return conn;
}

function sanitizeValue(value: unknown): unknown {
  if (typeof value === "bigint") return Number(value);
  if (value instanceof Date) return value.toISOString();
  if (Array.isArray(value)) return value.map(sanitizeValue);
  if (value && typeof value === "object") {
    // DuckDB value objects expose toJSON; plain objects pass through.
    try {
      const asJson = (value as { toJSON?: () => unknown }).toJSON;
      if (typeof asJson === "function") return sanitizeValue(asJson.call(value));
    } catch {
      /* fall through */
    }
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) out[k] = sanitizeValue(v);
    return out;
  }
  return value;
}

async function ensureDatabase(): Promise<void> {
  if (!dbReady) {
    dbReady = (async () => {
      const c = getConnection();
      await c.isInitialized();
      await c.evaluateQuery(`USE "${DATABASE.replace(/"/g, "")}"`);
    })().catch((err: unknown) => {
      dbReady = null;
      throw err;
    });
  }
  await dbReady;
}

export interface MotherDuckResult {
  rows: MotherDuckRow[];
  columns: string[];
  ms: number;
}

export async function runMotherDuck(rawSql: string): Promise<MotherDuckResult> {
  if (!TOKEN) throw new Error("MotherDuck is not configured (VITE_MOTHERDUCK_TOKEN).");
  const sql = rawSql.trim().replace(/;\s*$/, "");
  if (!sql) throw new Error("Empty query.");
  const started = performance.now();
  await ensureDatabase();
  const result = await getConnection().evaluateQuery(sql);
  const data = result.data;
  const columns = [...data.columnNames()];
  const rows = data.toRows().map((row) => {
    const out: MotherDuckRow = {};
    for (const [k, v] of Object.entries(row as Record<string, unknown>)) out[k] = sanitizeValue(v);
    return out;
  });
  return { rows, columns, ms: Math.round(performance.now() - started) };
}

export async function listMotherDuckTables(): Promise<string[]> {
  const { rows } = await runMotherDuck(
    "SELECT table_schema || '.' || table_name AS name " +
      "FROM information_schema.tables " +
      "WHERE table_schema IN ('main','staging','marts','reports') " +
      "ORDER BY 1"
  );
  return rows.map((r) => String(r.name ?? "")).filter(Boolean);
}

export async function resetMotherDuck(): Promise<void> {
  conn = null;
  dbReady = null;
  try {
    await terminateDuckDB();
  } catch {
    /* fresh start anyway */
  }
}

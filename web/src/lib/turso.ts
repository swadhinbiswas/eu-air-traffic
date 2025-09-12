/**
 * Turso (libSQL) client for the dashboard.
 *
 * Gold analytics live in Turso and are queried straight from the browser with a
 * **read-only** token — Turso supports scoped read-only tokens, so the page can
 * hold one safely. MotherDuck stays the warehouse; Turso is a derived copy
 * published by GitHub Actions.
 *
 *   VITE_TURSO_URL    e.g. libsql://eu-air-traffic-org.turso.io
 *   VITE_TURSO_TOKEN  a read-only Turso token
 */
import { createClient, type Client, type Row } from "@libsql/client/web";

export type TursoRow = Row;

const URL_ = (import.meta.env.VITE_TURSO_URL as string | undefined) || "";
const TOKEN = (import.meta.env.VITE_TURSO_TOKEN as string | undefined) || "";

let client: Client | null = null;

export function tursoConfigured(): boolean {
  return Boolean(URL_ && TOKEN);
}

export function tursoUrl(): string {
  return URL_;
}

function getClient(): Client {
  if (!client) {
    client = createClient({ url: URL_, authToken: TOKEN });
  }
  return client;
}

function sanitize(value: unknown): unknown {
  if (typeof value === "bigint") return Number(value);
  if (value instanceof Date) return value.toISOString();
  if (Array.isArray(value)) return value.map(sanitize);
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) out[k] = sanitize(v);
    return out;
  }
  return value;
}

export interface TursoResult {
  rows: Record<string, unknown>[];
  columns: string[];
  ms: number;
}

/** Run a SQL statement. Read-only token means writes are rejected server-side. */
export async function tursoQuery(sql: string, args: unknown[] = []): Promise<TursoResult> {
  if (!tursoConfigured()) {
    throw new Error("Turso is not configured (VITE_TURSO_URL / VITE_TURSO_TOKEN).");
  }
  const started = performance.now();
  const result = await getClient().execute({ sql, args: args as never[] });
  const rows = result.rows.map((row) => {
    const out: Record<string, unknown> = {};
    for (const key of result.columns) out[key] = sanitize(row[key]);
    return out;
  });
  return { rows, columns: [...result.columns], ms: Math.round(performance.now() - started) };
}

export async function tursoTables(): Promise<string[]> {
  const { rows } = await tursoQuery(
    "SELECT name FROM sqlite_master WHERE type IN ('table','view') " +
      "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '\\_%' ESCAPE '\\' ORDER BY name"
  );
  return rows.map((r) => String(r.name)).filter(Boolean);
}

/** Run several statements in one round trip (used for per-table row counts). */
export async function tursoBatch(sql: string[]): Promise<TursoResult[]> {
  if (!tursoConfigured()) {
    throw new Error("Turso is not configured (VITE_TURSO_URL / VITE_TURSO_TOKEN).");
  }
  const started = performance.now();
  const results = await getClient().batch(sql as never, "read");
  const elapsed = Math.round(performance.now() - started);
  return results.map((result, index) => ({
    rows: result.rows.map((row) => {
      const out: Record<string, unknown> = {};
      for (const key of result.columns) out[key] = sanitize(row[key]);
      return out;
    }),
    columns: [...result.columns],
    ms: index === 0 ? elapsed : 0,
  }));
}

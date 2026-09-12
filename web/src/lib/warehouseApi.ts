/**
 * Optional FastAPI warehouse API.
 *
 * Not required by the deployed site — Turso serves analytics — but useful when
 * a full warehouse API is running (local development, self-hosted). Calls fail
 * silently when it is not configured, so callers fall back.
 */
import { API_BASE } from "./bundle";

export type SqlRow = Record<string, unknown>;

export interface SqlResult {
  rows: SqlRow[];
  columns: string[];
  ms: number;
}

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

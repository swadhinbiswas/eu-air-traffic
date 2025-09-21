/**
 * Turso (libSQL) client for the dashboard — a multi-target router with failover.
 *
 * Gold analytics live in one or more independent Turso databases and are
 * queried straight from the browser with **read-only** tokens. Free-tier
 * accounts have their own row budgets, so `VITE_TURSO_TARGETS` spreads the
 * serving copy over them. A table listed under several targets is mirrored:
 * the router prefers the first copy, fails over to the next when an account is
 * down or out of quota, and keeps using the fallback while the failed target
 * cools down. Every copy is an independent database — no Turso replication.
 *
 *   VITE_TURSO_TARGETS  JSON array of {name,url,token,tables}; `tables` may be
 *                       omitted or "*" for a target holding every serving table
 *   VITE_TURSO_URL +
 *   VITE_TURSO_TOKEN    legacy single-database fallback
 *
 * The tokens are inlined into the bundle, so they MUST be read-only.
 */
import { createClient, type Client, type Row } from "@libsql/client/web";

export type TursoRow = Row;

interface TursoTarget {
  name: string;
  url: string;
  token: string;
  /** Serving tables this target holds; null means every serving table. */
  tables: Set<string> | null;
}

const TABLES_SQL =
  "SELECT name FROM sqlite_master WHERE type IN ('table','view') " +
  "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '\\_%' ESCAPE '\\' ORDER BY name";

/** How long a failed target is deprioritised before it is probed again. */
const FAILOVER_COOLDOWN_MS = 60_000;

function parseTargets(): TursoTarget[] {
  const raw = (import.meta.env.VITE_TURSO_TARGETS as string | undefined)?.trim();
  if (raw) {
    try {
      const data: unknown = JSON.parse(raw);
      if (Array.isArray(data)) {
        const targets: TursoTarget[] = [];
        for (const entry of data) {
          if (!entry || typeof entry !== "object") continue;
          const record = entry as Record<string, unknown>;
          const name = String(record.name ?? "").trim();
          const url = String(record.url ?? "").trim();
          const token = String(record.token ?? "").trim();
          if (!name || !url || !token) {
            console.warn(`[turso] ignoring target ${name || "(unnamed)"}: needs name, url, token`);
            continue;
          }
          if (targets.some((target) => target.name === name)) {
            console.warn(`[turso] ignoring duplicate target ${name}`);
            continue;
          }
          const listed = Array.isArray(record.tables)
            ? record.tables.map((table) => String(table).trim()).filter(Boolean)
            : typeof record.tables === "string"
              ? record.tables
                  .split(",")
                  .map((table) => table.trim())
                  .filter(Boolean)
              : [];
          const wildcard = listed.length === 0 || listed.includes("*");
          targets.push({ name, url, token, tables: wildcard ? null : new Set(listed) });
        }
        if (targets.length) return targets;
        console.warn("[turso] no usable target in VITE_TURSO_TARGETS; trying VITE_TURSO_URL");
      } else {
        console.warn("[turso] VITE_TURSO_TARGETS is not a JSON array; trying VITE_TURSO_URL");
      }
    } catch (err) {
      console.warn("[turso] VITE_TURSO_TARGETS is not valid JSON; trying VITE_TURSO_URL", err);
    }
  }

  const url = (import.meta.env.VITE_TURSO_URL as string | undefined)?.trim() || "";
  const token = (import.meta.env.VITE_TURSO_TOKEN as string | undefined)?.trim() || "";
  if (!url || !token) return [];
  return [{ name: "primary", url, token, tables: null }];
}

const TARGETS = parseTargets();
const BY_NAME = new Map(TARGETS.map((target) => [target.name, target]));
/** Table name → the targets that hold it, in preference order. */
const TABLE_TARGETS = new Map<string, string[]>();
for (const target of TARGETS) {
  if (target.tables === null) continue;
  for (const table of target.tables) {
    const holders = TABLE_TARGETS.get(table) ?? [];
    holders.push(target.name);
    TABLE_TARGETS.set(table, holders);
  }
}

const clients = new Map<string, Client>();
const failedUntil = new Map<string, number>();

function clientFor(target: TursoTarget): Client {
  let client = clients.get(target.name);
  if (!client) {
    client = createClient({ url: target.url, authToken: target.token });
    clients.set(target.name, client);
  }
  return client;
}

function healthy(name: string): boolean {
  return (failedUntil.get(name) ?? 0) <= Date.now();
}

function markFailed(name: string): void {
  failedUntil.set(name, Date.now() + FAILOVER_COOLDOWN_MS);
}

/**
 * Whether an error means the target itself is unhealthy.
 *
 * SQL errors (a typo, a missing table in a stale copy) are worth retrying on
 * another copy once, but must not sideline an entire account for the cooldown:
 * transport, auth, server and quota errors do.
 */
function shouldCooldown(err: unknown): boolean {
  const code = String((err as { code?: unknown } | null)?.code ?? "").toUpperCase();
  const message = (err instanceof Error ? err.message : String(err)).toLowerCase();
  if (/quota|rate.?limit|too many requests|resource limit|billing|payment/.test(message)) {
    return true;
  }
  if (code.startsWith("SQLITE_") || code.startsWith("SQL_")) return false;
  return true;
}

function covers(target: TursoTarget, table: string): boolean {
  return target.tables === null || target.tables.has(table);
}

/**
 * Table names referenced by a statement. Used only for routing: an unknown name
 * (a CTE, `sqlite_master`, a table that has not been published) imposes no
 * constraint, so it never blocks a query from reaching a database.
 */
function tablesInSql(sql: string): string[] {
  const references =
    /\b(?:FROM|JOIN|INTO|UPDATE|TABLE|PRAGMA\s+table_info)\s*\(?\s*["`[]?([A-Za-z_][A-Za-z0-9_]*)["`\]]?/gi;
  const found = new Set<string>();
  for (const match of sql.matchAll(references)) found.add(match[1]);
  return [...found];
}

/**
 * Targets that can run a statement, in preference order.
 *
 * A statement touching several known tables needs one database holding all of
 * them: joins cannot span independent Turso databases. Mirrored copies make
 * that possible; a config that partitions a join group raises instead.
 */
function candidatesFor(sql: string): TursoTarget[] {
  const referenced = tablesInSql(sql).filter((table) => TABLE_TARGETS.has(table));
  if (!referenced.length) return TARGETS;

  const covering = TARGETS.filter((target) =>
    referenced.every((table) => covers(target, table))
  );
  if (!covering.length) {
    throw new Error(
      `This query joins tables that live in different Turso databases (${referenced.join(", ")}). ` +
        "Mirror them onto one target or split the query."
    );
  }
  return covering;
}

function preferred(candidates: TursoTarget[]): TursoTarget {
  return candidates.find((target) => healthy(target.name)) ?? candidates[0];
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

function resultOf(result: { rows: Row[]; columns: string[] }, ms: number): TursoResult {
  return {
    rows: result.rows.map((row) => {
      const out: Record<string, unknown> = {};
      for (const key of result.columns) out[key] = sanitize(row[key]);
      return out;
    }),
    columns: [...result.columns],
    ms,
  };
}

async function executeOn(
  target: TursoTarget,
  sql: string,
  args: unknown[] = []
): Promise<TursoResult> {
  const started = performance.now();
  const result = await clientFor(target).execute({ sql, args: args as never[] });
  return resultOf(result, Math.round(performance.now() - started));
}

/** Run a statement against the first target that answers, failing over on error. */
async function route<T>(sql: string, run: (target: TursoTarget) => Promise<T>): Promise<T> {
  const candidates = candidatesFor(sql);
  // Healthy targets first (stable sort keeps the configured preference order),
  // so a failed account is skipped until its cooldown expires.
  const ordered = [...candidates].sort(
    (a, b) => Number(healthy(b.name)) - Number(healthy(a.name))
  );
  let lastError: unknown;
  for (const target of ordered) {
    try {
      return await run(target);
    } catch (err) {
      lastError = err;
      if (shouldCooldown(err)) {
        markFailed(target.name);
        console.warn(`[turso] target ${target.name} failed; trying another copy`, err);
      }
    }
  }
  throw lastError instanceof Error ? lastError : new Error("Turso is unavailable on every target");
}

export function tursoConfigured(): boolean {
  return TARGETS.length > 0;
}

/** URL of the preferred target; kept for callers that just need a label. */
export function tursoUrl(): string {
  return TARGETS[0]?.url ?? "";
}

/** Run a SQL statement. Read-only tokens mean writes are rejected server-side. */
export async function tursoQuery(sql: string, args: unknown[] = []): Promise<TursoResult> {
  if (!tursoConfigured()) {
    throw new Error("Turso is not configured (VITE_TURSO_TARGETS / VITE_TURSO_URL).");
  }
  return route(sql, (target) => executeOn(target, sql, args));
}

/**
 * Union of the tables across every reachable target. A failed target is
 * skipped (its mirror still lists the shared tables), but if nothing is
 * reachable the error surfaces.
 */
export async function tursoTables(): Promise<string[]> {
  if (!tursoConfigured()) {
    throw new Error("Turso is not configured (VITE_TURSO_TARGETS / VITE_TURSO_URL).");
  }
  const names = new Set<string>();
  const failures: unknown[] = [];
  await Promise.all(
    TARGETS.map(async (target) => {
      try {
        const result = await clientFor(target).execute(TABLES_SQL);
        for (const row of result.rows) {
          const name = String(row.name ?? "");
          if (name) names.add(name);
        }
      } catch (err) {
        failures.push(err);
        markFailed(target.name);
        console.warn(`[turso] could not list tables on ${target.name}`, err);
      }
    })
  );
  if (!names.size && failures.length) throw failures[0];
  return [...names].sort();
}

/**
 * Run several statements, one round trip per target in the common case.
 * If a target rejects the batch, its statements are retried through the normal
 * routing path, which fails over to another copy.
 */
export async function tursoBatch(sql: string[]): Promise<TursoResult[]> {
  if (!tursoConfigured()) {
    throw new Error("Turso is not configured (VITE_TURSO_TARGETS / VITE_TURSO_URL).");
  }
  const results: Array<TursoResult | undefined> = new Array(sql.length);
  const groups = new Map<string, Array<{ statement: string; index: number }>>();
  for (const [index, statement] of sql.entries()) {
    const target = preferred(candidatesFor(statement));
    const group = groups.get(target.name) ?? [];
    group.push({ statement, index });
    groups.set(target.name, group);
  }

  for (const [name, items] of groups) {
    const target = BY_NAME.get(name);
    if (!target) continue;
    const started = performance.now();
    try {
      const batch = await clientFor(target).batch(
        items.map((item) => item.statement) as never,
        "read"
      );
      items.forEach((item, position) => {
        results[item.index] = resultOf(
          batch[position],
          position === 0 ? Math.round(performance.now() - started) : 0
        );
      });
    } catch (err) {
      if (shouldCooldown(err)) markFailed(target.name);
      console.warn(`[turso] batch on ${target.name} failed; retrying individually`, err);
      await Promise.all(
        items.map(async (item) => {
          results[item.index] = await route(item.statement, (fallback) =>
            executeOn(fallback, item.statement)
          );
        })
      );
    }
  }

  return results.map((result) => result ?? { rows: [], columns: [], ms: 0 });
}

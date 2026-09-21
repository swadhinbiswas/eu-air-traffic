/**
 * Turso (libSQL) client for the dashboard — a multi-target router with failover.
 *
 * Gold analytics live in one or more independent Turso databases and are
 * queried straight from the browser with **read-only** tokens. Free-tier
 * accounts have their own row budgets, so `VITE_TURSO_TARGETS` spreads the
 * serving copy over them. A table listed under several targets is mirrored:
 * the router reads the freshest copy (ranked from each target's published
 * freshness), fails over to the next when an account is down or out of quota,
 * and keeps using the fallback while the failed target cools down. Every copy
 * is an independent database — no Turso replication.
 *
 *   VITE_TURSO_URL_1 +
 *   VITE_TURSO_TOKEN_1  one variable per field, repeated with 2, 3, … — no JSON
 *                       to escape, so a hosting dashboard cannot mangle it
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
/** How often target preference is re-derived from each copy's freshness. */
const RANKING_TTL_MS = 5 * 60_000;
/** Give up on a slow freshness probe and keep the configured order. */
const RANKING_TIMEOUT_MS = 2_500;
const FRESHNESS_SQL = "SELECT payload_json FROM site_summary WHERE key = 'freshness' LIMIT 1";

/** Connection URLs, used to spot a target whose `url` key was lost. */
const URL_LIKE = /^(?:libsql|https?|wss?|file):/i;
/** Turso issues JWTs; used to spot a keyless token value. */
const TOKEN_LIKE = /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/;

const ENV = import.meta.env as Record<string, unknown>;

function envValue(key: string): string {
  return String(ENV[key] ?? "").trim();
}

function tablesOf(listed: string[]): Set<string> | null {
  return listed.length && !listed.includes("*") ? new Set(listed) : null;
}

/**
 * Turn decoded entries into targets. A value with no key that looks like a
 * connection URL or a token fills the matching missing field, so an entry
 * written as `{"name":"eu-1","libsql://…","token":"…"}` still routes.
 */
function toTargets(entries: unknown[]): TursoTarget[] {
  const targets: TursoTarget[] = [];
  for (const entry of entries) {
    if (!entry || typeof entry !== "object") continue;
    const record = entry as Record<string, unknown>;
    const strings = Object.values(record).filter(
      (value): value is string => typeof value === "string"
    );
    const name = String(record.name ?? "").trim();
    const url = String(record.url ?? strings.find((value) => URL_LIKE.test(value)) ?? "").trim();
    const token = String(record.token ?? strings.find((value) => TOKEN_LIKE.test(value)) ?? "").trim();
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
    targets.push({ name, url, token, tables: tablesOf(listed) });
  }
  return targets;
}

/** Best-effort repairs for hand-written JSON: BOM, single quotes, trailing commas. */
function repairJson(raw: string): string {
  return raw
    .replace(/^\uFEFF/, "")
    .replace(/([{,[]\s*)'([^']*)'(\s*:)/g, '$1"$2"$3')
    .replace(/:\s*'([^']*)'/g, ': "$1"')
    .replace(/,\s*([}\]])/g, "$1");
}

/** Read `{…}` blocks from an unparseable value and keep the fields they hold. */
function salvageEntries(raw: string): unknown[] {
  const entries: Record<string, unknown>[] = [];
  for (const block of raw.matchAll(/\{[^{}]*\}/g)) {
    const record: Record<string, unknown> = {};
    const values: string[] = [];
    for (const quoted of block[0].matchAll(/"((?:[^"\\]|\\.)*)"/g)) values.push(quoted[1]);
    for (const pair of block[0].matchAll(
      /"([A-Za-z_][A-Za-z0-9_]*)"\s*:\s*"((?:[^"\\]|\\.)*)"/g
    )) {
      record[pair[1]] = pair[2];
    }
    const used = new Set(
      Object.values(record).filter((value): value is string => typeof value === "string")
    );
    for (const value of values) {
      if (used.has(value)) continue;
      if (record.url === undefined && URL_LIKE.test(value)) record.url = value;
      else if (record.token === undefined && TOKEN_LIKE.test(value)) record.token = value;
    }
    if (record.name !== undefined || record.url !== undefined) entries.push(record);
  }
  return entries;
}

function jsonTargets(raw: string): TursoTarget[] {
  for (const candidate of [raw, repairJson(raw)]) {
    try {
      const data: unknown = JSON.parse(candidate);
      const entries = Array.isArray(data)
        ? data
        : Array.isArray((data as { targets?: unknown } | null)?.targets)
          ? (data as { targets: unknown[] }).targets
          : [data];
      const targets = toTargets(entries);
      if (targets.length) return targets;
    } catch {
      /* try the next form */
    }
  }
  const salvaged = toTargets(salvageEntries(raw));
  if (salvaged.length) {
    console.warn("[turso] VITE_TURSO_TARGETS is not valid JSON; recovered what it could read");
  }
  return salvaged;
}

/**
 * Targets from one-variable-per-field config (`VITE_TURSO_URL_1` +
 * `VITE_TURSO_TOKEN_1`, then 2, 3, …). No JSON escaping involved, which is what
 * makes it survive a copied or line-wrapped value in a hosting dashboard.
 */
function numberedTargets(): TursoTarget[] {
  const indexes = new Set<number>();
  for (const key of Object.keys(ENV)) {
    const match = /^VITE_TURSO_URL_(\d+)$/.exec(key);
    if (match) indexes.add(Number(match[1]));
  }

  const targets: TursoTarget[] = [];
  for (const index of [...indexes].sort((a, b) => a - b)) {
    const url = envValue(`VITE_TURSO_URL_${index}`);
    const token = envValue(`VITE_TURSO_TOKEN_${index}`);
    if (!url || !token) {
      console.warn(
        `[turso] ignoring VITE_TURSO_URL_${index}: ` +
          `VITE_TURSO_URL_${index} and VITE_TURSO_TOKEN_${index} are both required`
      );
      continue;
    }
    const name = envValue(`VITE_TURSO_NAME_${index}`) || `turso-${index}`;
    const listed = envValue(`VITE_TURSO_TABLES_${index}`)
      .split(",")
      .map((table) => table.trim())
      .filter(Boolean);
    targets.push({ name, url, token, tables: tablesOf(listed) });
  }
  return targets;
}

function parseTargets(): TursoTarget[] {
  const numbered = numberedTargets();
  if (numbered.length) return numbered;

  const raw = envValue("VITE_TURSO_TARGETS");
  if (raw) {
    const targets = jsonTargets(raw);
    if (targets.length) return targets;
    console.warn("[turso] no usable target in VITE_TURSO_TARGETS; trying VITE_TURSO_URL");
  }

  const url = envValue("VITE_TURSO_URL");
  const token = envValue("VITE_TURSO_TOKEN");
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

let ranked: TursoTarget[] | null = null;
let rankedAt = 0;
let ranking: Promise<void> | null = null;

/**
 * Order targets by the freshness each copy publishes in `site_summary`.
 *
 * A write-blocked or paused copy keeps answering reads with old data, and a
 * stale read never fails over because nothing errors. Ranking by the published
 * `as_of` means the browser reads the freshest copy even when the config lists
 * another one first; the configured order breaks ties and carries targets whose
 * freshness is unknown (empty or unreachable copies).
 */
function ensureRanking(): Promise<void> {
  if (ranking) return ranking;
  if (ranked && Date.now() - rankedAt < RANKING_TTL_MS) return Promise.resolve();

  const timeout = new Promise<void>((resolve) => setTimeout(resolve, RANKING_TIMEOUT_MS));
  ranking = Promise.race([refreshRanking(), timeout])
    .catch(() => {
      /* keep the previous order */
    })
    .finally(() => {
      ranking = null;
    });
  return ranking;
}

async function refreshRanking(): Promise<void> {
  const scored = await Promise.all(
    TARGETS.map(async (target, index) => {
      try {
        const result = await clientFor(target).execute(FRESHNESS_SQL);
        const raw = result.rows[0]?.payload_json;
        const payload = typeof raw === "string" ? (JSON.parse(raw) as { as_of?: unknown }) : null;
        const at = typeof payload?.as_of === "string" ? Date.parse(payload.as_of) : NaN;
        return { target, index, at: Number.isFinite(at) ? at : Number.NEGATIVE_INFINITY };
      } catch {
        return { target, index, at: Number.NEGATIVE_INFINITY };
      }
    })
  );
  ranked = scored
    .sort((a, b) => b.at - a.at || a.index - b.index)
    .map((entry) => entry.target);
  rankedAt = Date.now();
}

function orderedTargets(): TursoTarget[] {
  return ranked ?? TARGETS;
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
  const targets = orderedTargets();
  const referenced = tablesInSql(sql).filter((table) => TABLE_TARGETS.has(table));
  if (!referenced.length) return targets;

  const covering = targets.filter((target) =>
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
    throw new Error("Turso is not configured (VITE_TURSO_URL_1/TOKEN_1, VITE_TURSO_TARGETS or VITE_TURSO_URL).");
  }
  await ensureRanking();
  return route(sql, (target) => executeOn(target, sql, args));
}

/**
 * Union of the tables across every reachable target. A failed target is
 * skipped (its mirror still lists the shared tables), but if nothing is
 * reachable the error surfaces.
 */
export async function tursoTables(): Promise<string[]> {
  if (!tursoConfigured()) {
    throw new Error("Turso is not configured (VITE_TURSO_URL_1/TOKEN_1, VITE_TURSO_TARGETS or VITE_TURSO_URL).");
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
    throw new Error("Turso is not configured (VITE_TURSO_URL_1/TOKEN_1, VITE_TURSO_TARGETS or VITE_TURSO_URL).");
  }
  await ensureRanking();
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

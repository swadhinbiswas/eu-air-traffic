import { useCallback, useEffect, useMemo, useState } from "react";
import { Download, Loader2, Play, RotateCw, Terminal } from "lucide-react";
import { Panel, SectionHeader } from "@/components/shared";
import { liveTables, runSqlLive, type SqlRow } from "@/lib/warehouseApi";
import { tursoConfigured, tursoQuery, tursoTables } from "@/lib/turso";

type SqlEngineKind = "turso" | "live";
import { API_BASE } from "@/lib/bundle";
import { nf } from "@/lib/format";
import { cn } from "@/lib/utils";

const EXAMPLES: { label: string; sql: string }[] = [
  {
    label: "Busiest airports",
    sql: "SELECT airport_icao, total_flights, ROUND(avg_delay_minutes, 1) AS avg_delay\nFROM gold_airport_metrics\nORDER BY total_flights DESC\nLIMIT 15;",
  },
  {
    label: "Worst weather",
    sql: "SELECT weather_condition, flight_count, ROUND(avg_delay_minutes, 1) AS avg_delay\nFROM gold_weather_impact\nORDER BY avg_delay_minutes DESC;",
  },
  {
    label: "Delay by status",
    sql: "SELECT status, flight_count, min_delay_minutes, avg_delay_minutes, max_delay_minutes\nFROM gold_delay_analysis\nORDER BY flight_count DESC;",
  },
  {
    label: "Airline punctuality",
    sql: "SELECT airline_name, total_flights, ROUND(on_time_rate * 100, 1) AS on_time_pct\nFROM gold_airline_rankings\nORDER BY on_time_rate DESC;",
  },
  {
    label: "Emissions by type",
    sql: "SELECT aircraft_type, co2_kg_per_hour\nFROM fact_emissions\nORDER BY co2_kg_per_hour DESC\nLIMIT 10;",
  },
];

function toCsv(rows: SqlRow[], columns: string[]): string {
  const escape = (v: unknown) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [columns.join(","), ...rows.map((r) => columns.map((c) => escape(r[c])).join(","))].join("\n");
}

export function SqlPage() {
  const [tables, setTables] = useState<string[]>([]);
  const [status, setStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [engine, setEngine] = useState<SqlEngineKind>("turso");
  const [error, setError] = useState<string | null>(null);
  const [sql, setSql] = useState(EXAMPLES[0].sql);
  const [rows, setRows] = useState<SqlRow[]>([]);
  const [columns, setColumns] = useState<string[]>([]);
  const [ms, setMs] = useState<number | null>(null);
  const [running, setRunning] = useState(false);

  const initEngine = useCallback(async () => {
    setStatus("loading");
    setError(null);

    // Gold analytics live in Turso and are queried with a read-only token.
    let primaryError: string | null = null;
    if (tursoConfigured()) {
      try {
        setEngine("turso");
        setTables(await tursoTables());
        setStatus("ready");
        return;
      } catch (e: unknown) {
        primaryError =
          e instanceof Error
            ? `Turso: ${e.message} (check VITE_TURSO_TARGETS)`
            : "Turso connection failed";
      }
    }

    // Optional: a native FastAPI warehouse API, when one is deployed.
    if (API_BASE) {
      try {
        const live = await liveTables();
        setEngine("live");
        setTables(live);
        setStatus("ready");
        return;
      } catch {
        /* fall through to the error below */
      }
    }

    setStatus("error");
    setError(
      primaryError ??
        "No SQL engine configured. Set VITE_TURSO_TARGETS at build time."
    );
  }, []);

  useEffect(() => {
    void initEngine();
  }, [initEngine]);

  const execute = useCallback(
    async (statement?: string) => {
      const text = (statement ?? sql).trim().replace(/;\s*$/, "");
      if (!text) return;
      setRunning(true);
      setError(null);
      try {
        const result =
          engine === "turso" ? await tursoQuery(text) : await runSqlLive(text);
        setRows(result.rows);
        setColumns(result.columns);
        setMs(result.ms);
      } catch (e) {
        setRows([]);
        setColumns([]);
        setError(e instanceof Error ? e.message : "Query failed");
      } finally {
        setRunning(false);
      }
    },
    [sql, engine]
  );

  const preview = useMemo(() => rows.slice(0, 500), [rows]);

  const downloadCsv = () => {
    const blob = new Blob([toCsv(rows, columns)], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "query_result.csv";
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-6">
      <SectionHeader
        icon={<Terminal className="h-4 w-4" />}
        title="SQL Explorer"
        subtitle="Query the Gold layer in Turso directly with a read-only token"
        right={
          <span
            className={cn(
              "flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px]",
              status === "ready" && "border-emerald-500/25 bg-emerald-500/10 text-emerald-300",
              status === "loading" && "border-amber-500/25 bg-amber-500/10 text-amber-300",
              status === "error" && "border-red-500/25 bg-red-500/10 text-red-300",
              status === "idle" && "border-white/10 text-zinc-400"
            )}
          >
            {status === "loading" && <Loader2 className="h-3 w-3 animate-spin" />}
            {status === "ready"
              ? `${engine} engine · ${tables.length} tables`
              : status}
          </span>
        }
      />

      <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
        <div className="space-y-4">
          <Panel>
            <div className="hud-label mb-2">Tables</div>
            <ul className="space-y-1">
              {tables.map((t) => (
                <li key={t}>
                  <button
                    onClick={() => {
                      setSql(`SELECT * FROM ${t} LIMIT 25;`);
                      void execute(`SELECT * FROM ${t} LIMIT 25`);
                    }}
                    className="mono w-full truncate rounded-md border border-white/5 bg-white/[0.02] px-2 py-1.5 text-left text-[11px] text-zinc-300 transition-colors hover:border-emerald-500/30 hover:text-emerald-200"
                  >
                    {t}
                  </button>
                </li>
              ))}
              {!tables.length && (
                <li className="text-[11px] text-zinc-600">
                  {status === "loading" ? "Loading engine…" : "Not available"}
                </li>
              )}
            </ul>
          </Panel>
          <Panel>
            <div className="hud-label mb-2">Examples</div>
            <ul className="space-y-1">
              {EXAMPLES.map((ex) => (
                <li key={ex.label}>
                  <button
                    onClick={() => {
                      setSql(ex.sql);
                      void execute(ex.sql);
                    }}
                    className="flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-[11px] text-zinc-400 transition-colors hover:bg-white/[0.03] hover:text-zinc-200"
                  >
                    <Play className="h-3 w-3 text-emerald-400" />
                    {ex.label}
                  </button>
                </li>
              ))}
            </ul>
          </Panel>
        </div>

        <div className="space-y-4">
          <Panel className="p-0">
            <textarea
              value={sql}
              onChange={(e) => setSql(e.target.value)}
              onKeyDown={(e) => {
                if ((e.metaKey || e.ctrlKey) && e.key === "Enter") void execute();
              }}
              spellCheck={false}
              rows={7}
              className="mono w-full resize-y rounded-t-xl bg-transparent p-4 text-xs leading-relaxed text-emerald-100 outline-none placeholder:text-zinc-600"
              placeholder="SELECT * FROM gold_airport_metrics LIMIT 10;"
            />
            <div className="flex items-center justify-between border-t border-white/5 px-3 py-2">
              <span className="text-[10px] text-zinc-500">⌘/Ctrl + Enter to run · read-only</span>
              <button
                onClick={() => void execute()}
                disabled={running || status !== "ready"}
                className="flex items-center gap-1.5 rounded-md bg-emerald-500/90 px-3 py-1.5 text-xs font-medium text-zinc-950 transition-colors hover:bg-emerald-400 disabled:opacity-40"
              >
                {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
                Run
              </button>
            </div>
          </Panel>

          {error && (
            <div className="panel border-red-500/20 bg-red-500/5 p-4 text-xs text-red-300">
              <div className="flex items-center justify-between">
                <span className="font-medium">
                  {status === "error" ? "Engine unavailable" : "Query error"}
                </span>
                {status === "error" && (
                  <button
                    onClick={() => void initEngine()}
                    className="flex items-center gap-1.5 rounded-md border border-red-500/30 px-2 py-1 text-[11px] text-red-200 transition-colors hover:bg-red-500/10"
                  >
                    <RotateCw className="h-3 w-3" />
                    Retry
                  </button>
                )}
              </div>
              <pre className="mono mt-1 whitespace-pre-wrap text-red-300/80">{error}</pre>
            </div>
          )}

          {columns.length > 0 && (
            <Panel className="p-0">
              <div className="flex items-center justify-between border-b border-white/5 px-4 py-2.5">
                <span className="text-xs text-zinc-400">
                  {nf(rows.length)} rows · {columns.length} columns{ms !== null ? ` · ${ms} ms` : ""}
                </span>
                <button
                  onClick={downloadCsv}
                  className="flex items-center gap-1.5 rounded-md border border-emerald-500/25 bg-emerald-500/10 px-2.5 py-1 text-xs text-emerald-300 transition-colors hover:bg-emerald-500/20"
                >
                  <Download className="h-3.5 w-3.5" />
                  CSV
                </button>
              </div>
              <div className="max-h-[520px] overflow-auto">
                <table className="w-full text-left text-xs">
                  <thead className="sticky top-0 bg-black/90 text-zinc-500 backdrop-blur">
                    <tr className="border-b border-white/5">
                      {columns.map((c) => (
                        <th key={c} className="whitespace-nowrap px-3 py-2 font-medium">
                          {c}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="mono">
                    {preview.map((r, i) => (
                      <tr key={i} className="border-b border-white/[0.03] hover:bg-white/[0.02]">
                        {columns.map((c) => (
                          <td key={c} className="max-w-[260px] truncate whitespace-nowrap px-3 py-1.5 text-zinc-300">
                            {r[c] === null || r[c] === undefined ? (
                              <span className="text-zinc-600">null</span>
                            ) : (
                              String(r[c])
                            )}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>
          )}

          {!columns.length && !error && (
            <Panel className="grid place-items-center p-10 text-sm text-zinc-500">
              Run a query to see results (or pick an example).
            </Panel>
          )}
        </div>
      </div>
    </div>
  );
}

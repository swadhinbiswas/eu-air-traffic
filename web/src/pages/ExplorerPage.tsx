import { useEffect, useMemo, useState } from "react";
import { Download, Map as MapIcon, Table2 } from "lucide-react";
import { Panel, SectionHeader, Spinner, ErrorState, EmptyState } from "@/components/shared";
import { GeoMap, detectGeoColumns, numericColumns } from "@/components/GeoMap";
import { useAirports, useAnalytics, useCatalog, useMetars, usePositions } from "@/hooks/useBundle";
import { nf } from "@/lib/format";
import { cn } from "@/lib/utils";

type Row = Record<string, unknown>;
type View = "table" | "map";

const LABEL_CANDIDATES = ["icao", "callsign", "airport_icao", "iata", "iata_code", "name", "origin"];

function toCsv(rows: Row[]): string {
  if (!rows.length) return "";
  const cols = Object.keys(rows[0]);
  const escape = (v: unknown) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [cols.join(","), ...rows.map((r) => cols.map((c) => escape(r[c])).join(","))].join("\n");
}

function download(name: string, rows: Row[]) {
  const blob = new Blob([toCsv(rows)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${name}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

export function ExplorerPage() {
  const { data: catalog, loading, error } = useCatalog();
  const { data: airports } = useAirports();
  const { data: analytics } = useAnalytics();
  const { data: positions } = usePositions();
  const { data: metars } = useMetars();
  const [selected, setSelected] = useState<string>("gold_airport_metrics");
  const [limit, setLimit] = useState(50);
  const [view, setView] = useState<View>("table");
  const [metric, setMetric] = useState<string | null>(null);

  const datasets = useMemo<Record<string, unknown[]>>(() => {
    const out: Record<string, unknown[]> = {};
    if (!analytics) return out;
    out.gold_airport_metrics = analytics.gold_airport_metrics;
    out.gold_airline_rankings = analytics.gold_airline_rankings;
    out.gold_delay_analysis = analytics.gold_delay_analysis;
    out.gold_weather_impact = analytics.gold_weather_impact;
    out.gold_seasonal_trends = analytics.gold_seasonal_trends;
    out.gold_fuel_price_series = analytics.gold_fuel_price_series;
    out.fact_emissions = analytics.emissions;
    out.dim_aircraft = analytics.fleet;
    out.gold_route_performance = analytics.routes;
    out.status_mix = analytics.status_mix;
    out.dim_airport = (airports ?? []).slice(0, 2000);
    out.live_positions = positions ?? [];
    out.latest_metar = metars ?? [];
    return out;
  }, [analytics, airports, positions, metars]);

  const rows = (datasets[selected] ?? []) as Row[];
  const preview = rows.slice(0, limit);
  const columns = preview.length ? Object.keys(preview[0]) : [];
  const tableMeta = catalog?.tables.find((t) => t.name === selected);

  const geo = useMemo(() => detectGeoColumns(rows), [rows]);
  const metrics = useMemo(() => numericColumns(rows, geo), [rows, geo]);
  const labelKey = useMemo(
    () => LABEL_CANDIDATES.find((k) => rows.length && k in rows[0]) ?? null,
    [rows]
  );

  // Reset the map view/metric when switching datasets.
  useEffect(() => {
    setView("table");
    setMetric(null);
  }, [selected]);

  if (loading) return <Spinner label="Loading dataset explorer" />;
  if (error) return <ErrorState message={error} />;
  if (!catalog) return <EmptyState message="No catalog available." />;

  return (
    <div className="space-y-6">
      <SectionHeader
        icon={<Table2 className="h-4 w-4" />}
        title="Dataset Explorer"
        subtitle="Browse datasets, inspect column metadata and preview sample records"
      />

      <div className="grid gap-4 lg:grid-cols-[300px_1fr]">
        <Panel className="max-h-[680px] overflow-y-auto p-0">
          <div className="sticky top-0 z-10 border-b border-white/5 bg-black/80 px-4 py-3 backdrop-blur">
            <h2 className="text-sm font-semibold text-zinc-200">Datasets</h2>
          </div>
          <ul className="divide-y divide-white/[0.03]">
            {Object.keys(datasets).map((name) => (
              <li key={name}>
                <button
                  onClick={() => {
                    setSelected(name);
                    setLimit(50);
                  }}
                  className={cn(
                    "flex w-full items-center justify-between px-4 py-2.5 text-left transition-colors hover:bg-white/[0.03]",
                    selected === name && "bg-emerald-500/[0.06]"
                  )}
                >
                  <span className="mono truncate text-xs text-zinc-200">{name}</span>
                  <span className="mono text-[10px] text-zinc-500">{nf((datasets[name] ?? []).length)}</span>
                </button>
              </li>
            ))}
          </ul>
        </Panel>

        <div className="space-y-4">
          <Panel className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="mono text-sm font-semibold text-zinc-100">{selected}</h2>
              <p className="text-[11px] text-zinc-500">
                {nf(rows.length)} rows
                {view === "table" ? ` · showing ${nf(preview.length)}` : ""}
                {tableMeta ? ` · ${tableMeta.columns.length} columns` : ""}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {geo && (
                <div className="flex items-center gap-0.5 rounded-lg border border-white/10 bg-zinc-900/60 p-0.5">
                  <button
                    onClick={() => setView("table")}
                    className={cn(
                      "flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] transition-colors",
                      view === "table" ? "bg-zinc-700/60 text-white" : "text-zinc-400 hover:text-zinc-200"
                    )}
                  >
                    <Table2 className="h-3 w-3" />
                    Table
                  </button>
                  <button
                    onClick={() => {
                      setMetric((m) => m ?? metrics[0] ?? null);
                      setView("map");
                    }}
                    className={cn(
                      "flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] transition-colors",
                      view === "map" ? "bg-zinc-700/60 text-white" : "text-zinc-400 hover:text-zinc-200"
                    )}
                  >
                    <MapIcon className="h-3 w-3" />
                    Map
                  </button>
                </div>
              )}

              {view === "map" && metrics.length > 0 && (
                <select
                  value={metric ?? metrics[0]}
                  onChange={(e) => setMetric(e.target.value)}
                  className="rounded-md border border-white/10 bg-zinc-900 px-2 py-1 text-xs text-zinc-200 outline-none"
                >
                  {metrics.map((m) => (
                    <option key={m} value={m}>
                      colour: {m}
                    </option>
                  ))}
                </select>
              )}

              {view === "table" && (
                <select
                  value={limit}
                  onChange={(e) => setLimit(Number(e.target.value))}
                  className="rounded-md border border-white/10 bg-zinc-900 px-2 py-1 text-xs text-zinc-200 outline-none"
                >
                  {[25, 50, 100, 250].map((n) => (
                    <option key={n} value={n}>
                      {n} rows
                    </option>
                  ))}
                </select>
              )}

              <button
                onClick={() => download(selected, rows)}
                disabled={!rows.length}
                className="flex items-center gap-1.5 rounded-md border border-emerald-500/25 bg-emerald-500/10 px-2.5 py-1 text-xs text-emerald-300 transition-colors hover:bg-emerald-500/20 disabled:opacity-40"
              >
                <Download className="h-3.5 w-3.5" />
                CSV
              </button>
            </div>
          </Panel>

          {view === "map" ? (
            <Panel className="p-3">
              <GeoMap
                rows={rows}
                metric={metric ?? metrics[0] ?? null}
                labelKey={labelKey}
                height={520}
              />
              <p className="mt-2 text-[10px] text-zinc-500">
                {labelKey ? `Labels: ${labelKey} · ` : ""}
                Marker colour scales with the selected metric. Dataset coordinate columns:{" "}
                <span className="mono">
                  {geo?.lat}/{geo?.lon}
                </span>
              </p>
            </Panel>
          ) : preview.length ? (
            <Panel className="overflow-x-auto p-0">
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
                        <td key={c} className="max-w-[240px] truncate whitespace-nowrap px-3 py-1.5 text-zinc-300">
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
            </Panel>
          ) : (
            <EmptyState
              message={
                selected.startsWith("gold_") || selected.startsWith("fact_") || selected === "status_mix"
                  ? `No rows in ${selected} yet — this mart fills once flight history flows (OpenSky credentials on the collector).`
                  : `No preview available for ${selected} right now.`
              }
            />
          )}

          {tableMeta && (
            <Panel>
              <div className="hud-label mb-3">Column metadata</div>
              <div className="grid gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
                {tableMeta.columns.map((c) => (
                  <div
                    key={c.name}
                    className="flex items-center justify-between rounded-md border border-white/5 bg-white/[0.02] px-2.5 py-1.5"
                  >
                    <span className="mono text-[11px] text-zinc-200">{c.name}</span>
                    <span className="mono text-[10px] text-cyan-300/80">{c.type}</span>
                  </div>
                ))}
              </div>
            </Panel>
          )}
        </div>
      </div>
    </div>
  );
}

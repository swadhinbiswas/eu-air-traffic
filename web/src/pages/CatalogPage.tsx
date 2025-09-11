import { useMemo, useState } from "react";
import { Database, GitBranch, Table2 } from "lucide-react";
import { Panel, SectionHeader, Spinner, ErrorState, EmptyState, StatCard } from "@/components/shared";
import { useCatalog } from "@/hooks/useBundle";
import { nf } from "@/lib/format";
import { cn } from "@/lib/utils";

const LAYER_TONE: Record<string, string> = {
  gold: "bg-amber-500/15 text-amber-300 border-amber-500/20",
  fact: "bg-cyan-500/15 text-cyan-300 border-cyan-500/20",
  dim: "bg-blue-500/15 text-blue-300 border-blue-500/20",
  raw: "bg-zinc-500/15 text-zinc-300 border-zinc-500/20",
};

const NODE_LAYER_TONE: Record<string, string> = {
  sources: "text-zinc-400",
  staging: "text-blue-300",
  intermediate: "text-violet-300",
  marts: "text-amber-300",
  reports: "text-emerald-300",
};

export function CatalogPage() {
  const { data: catalog, loading, error } = useCatalog();
  const [selected, setSelected] = useState<string | null>(null);

  const table = useMemo(() => {
    if (!catalog) return null;
    return catalog.tables.find((t) => t.name === selected) ?? catalog.tables[0] ?? null;
  }, [catalog, selected]);

  if (loading) return <Spinner label="Loading data catalog" />;
  if (error) return <ErrorState message={error} />;
  if (!catalog) return <EmptyState message="No catalog available." />;

  const totalRows = catalog.tables.reduce((sum, t) => sum + t.rows, 0);
  const layers = ["sources", "staging", "intermediate", "marts", "reports"];
  const grouped = Object.fromEntries(layers.map((l) => [l, catalog.lineage.nodes.filter((n) => n.layer === l)]));

  return (
    <div className="space-y-6">
      <SectionHeader
        icon={<Database className="h-4 w-4" />}
        title="Data Catalog"
        subtitle="Schemas, tables, columns and dbt lineage across the Medallion stack"
      />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard label="Tables & views" value={nf(catalog.tables.length)} icon={<Table2 className="h-3.5 w-3.5" />} />
        <StatCard label="Rows catalogued" value={nf(totalRows)} />
        <StatCard label="dbt models" value={nf(catalog.lineage.nodes.length)} icon={<GitBranch className="h-3.5 w-3.5" />} />
        <StatCard label="Lineage edges" value={nf(catalog.lineage.edges.length)} />
      </div>

      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        <Panel className="max-h-[640px] overflow-y-auto p-0">
          <div className="sticky top-0 z-10 border-b border-white/5 bg-black/80 px-4 py-3 backdrop-blur">
            <h2 className="text-sm font-semibold text-zinc-200">Objects</h2>
          </div>
          <ul className="divide-y divide-white/[0.03]">
            {catalog.tables.map((t) => (
              <li key={t.name}>
                <button
                  onClick={() => setSelected(t.name)}
                  className={cn(
                    "flex w-full items-center justify-between gap-2 px-4 py-2.5 text-left transition-colors hover:bg-white/[0.03]",
                    table?.name === t.name && "bg-emerald-500/[0.06]"
                  )}
                >
                  <div className="min-w-0">
                    <div className="mono truncate text-xs text-zinc-200">{t.name}</div>
                    <div className="text-[10px] text-zinc-500">
                      {t.columns.length} cols · {nf(t.rows)} rows
                    </div>
                  </div>
                  <span
                    className={cn(
                      "flex-none rounded-full border px-1.5 py-px text-[9px] uppercase tracking-wide",
                      LAYER_TONE[t.layer] ?? LAYER_TONE.raw
                    )}
                  >
                    {t.layer}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </Panel>

        <div className="space-y-4">
          {table && (
            <Panel>
              <div className="mb-3 flex items-center justify-between">
                <div>
                  <h2 className="mono text-sm font-semibold text-zinc-100">{table.name}</h2>
                  <p className="text-[11px] text-zinc-500">
                    {table.schema} · {table.kind} · {nf(table.rows)} rows
                  </p>
                </div>
                <span
                  className={cn(
                    "rounded-full border px-2 py-0.5 text-[10px] uppercase",
                    LAYER_TONE[table.layer] ?? LAYER_TONE.raw
                  )}
                >
                  {table.layer}
                </span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead className="text-zinc-500">
                    <tr className="border-b border-white/5">
                      <th className="py-2 pr-4 font-medium">Column</th>
                      <th className="py-2 pr-4 font-medium">Type</th>
                      <th className="py-2 font-medium">Nullable</th>
                    </tr>
                  </thead>
                  <tbody className="mono">
                    {table.columns.map((c) => (
                      <tr key={c.name} className="border-b border-white/[0.03]">
                        <td className="py-1.5 pr-4 text-zinc-200">{c.name}</td>
                        <td className="py-1.5 pr-4 text-cyan-300/80">{c.type}</td>
                        <td className="py-1.5 text-zinc-500">{c.nullable ? "yes" : "no"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>
          )}

          <Panel>
            <div className="mb-4 flex items-center gap-2">
              <GitBranch className="h-4 w-4 text-emerald-400" />
              <h2 className="text-sm font-semibold text-zinc-200">dbt lineage</h2>
              <span className="hud-label ml-auto">
                {catalog.lineage.edges.length} dependencies
              </span>
            </div>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {layers.map((layer) => (
                <div key={layer} className="rounded-lg border border-white/5 bg-white/[0.02] p-3">
                  <div className={cn("hud-label mb-2", NODE_LAYER_TONE[layer])}>{layer}</div>
                  <ul className="space-y-1.5">
                    {(grouped[layer] ?? []).map((n) => (
                      <li key={n.id} className="rounded-md border border-white/5 bg-zinc-950/50 p-2">
                        <div className="mono text-[11px] text-zinc-200">{n.id}</div>
                        <div className="text-[9px] text-zinc-500">{n.materialized}</div>
                        {n.depends_on.length > 0 && (
                          <div className="mt-1 flex flex-wrap gap-1">
                            {n.depends_on.map((d) => (
                              <span
                                key={d}
                                className="rounded bg-white/[0.04] px-1 py-px text-[9px] text-zinc-500"
                              >
                                {d.replace("source.", "")}
                              </span>
                            ))}
                          </div>
                        )}
                      </li>
                    ))}
                    {(grouped[layer] ?? []).length === 0 && (
                      <li className="text-[10px] text-zinc-600">—</li>
                    )}
                  </ul>
                </div>
              ))}
            </div>
            {catalog.lineage.sources.length > 0 && (
              <div className="mt-4 border-t border-white/5 pt-3">
                <div className="hud-label mb-2">Declared sources</div>
                <div className="flex flex-wrap gap-1.5">
                  {catalog.lineage.sources.map((s) => (
                    <span
                      key={s.id}
                      className="rounded-md border border-white/5 bg-white/[0.03] px-2 py-1 text-[10px] text-zinc-400"
                      title={s.description}
                    >
                      {s.source}.{s.name}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
}

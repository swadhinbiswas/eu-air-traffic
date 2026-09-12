import { Activity, CheckCircle2, Clock, Database, ServerCrash, Wifi } from "lucide-react";
import { Panel, ProgressBar, SectionHeader, Spinner, ErrorState, StatCard, EmptyState } from "@/components/shared";
import { useCatalog, useOps } from "@/hooks/useBundle";
import { nf, relative } from "@/lib/format";
import { cn } from "@/lib/utils";

interface Step {
  status: string;
  result?: unknown;
  elapsed_seconds?: number;
}

export function OpsPage() {
  const { data: ops, loading, error } = useOps();
  const { data: catalog } = useCatalog();

  if (loading) return <Spinner label="Loading operations data" />;
  if (error) return <ErrorState message={error} />;
  if (!ops) return <EmptyState message="No operations report found." />;

  const pipeline = ops.pipeline as {
    started_at?: string;
    finished_at?: string;
    elapsed_seconds?: number;
    success?: boolean;
    steps?: Record<string, Step>;
    errors?: Record<string, string>;
  } | null;

  const quality = ops.quality as {
    generated_at?: string;
    summary?: Record<string, number>;
    sources?: Record<string, { bronze_rows: number; silver_rows: number; quarantined_rows: number; pass_rate: number; freshness: string }>;
  } | null;

  const steps = Object.entries(pipeline?.steps ?? {});

  return (
    <div className="space-y-6">
      <SectionHeader
        icon={<Activity className="h-4 w-4" />}
        title="Ops & Pipeline Health"
        subtitle="Pipeline runs, data-quality pass rates and real-time stream status"
      />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard
          label="Pipeline"
          value={pipeline?.success === false ? "FAILED" : pipeline ? "HEALTHY" : "—"}
          tone={pipeline?.success === false ? "red" : "emerald"}
          hint={pipeline?.finished_at ? `finished ${relative(pipeline.finished_at)}` : undefined}
          icon={<CheckCircle2 className="h-3.5 w-3.5" />}
        />
        <StatCard
          label="Elapsed"
          value={pipeline?.elapsed_seconds !== undefined ? `${nf(pipeline.elapsed_seconds, 1)}s` : "—"}
          icon={<Clock className="h-3.5 w-3.5" />}
        />
        <StatCard
          label="Quality pass"
          value={quality?.summary ? `${(quality.summary.overall_pass_rate * 100).toFixed(1)}%` : "—"}
          tone="emerald"
          icon={<Database className="h-3.5 w-3.5" />}
        />
        <StatCard
          label="Quarantined"
          value={quality?.summary ? nf(quality.summary.quarantined_rows) : "—"}
          tone="amber"
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-zinc-200">Pipeline steps</h2>
            <span className="hud-label">{steps.length} steps</span>
          </div>
          <ul className="space-y-1.5">
            {steps.map(([name, step]) => (
              <li
                key={name}
                className="flex items-center justify-between rounded-md border border-white/5 bg-white/[0.02] px-3 py-2"
              >
                <div className="flex items-center gap-2">
                  <span
                    className={cn(
                      "h-2 w-2 rounded-full",
                      step.status === "ok" ? "bg-emerald-400" : step.status === "failed" ? "bg-red-400" : "bg-zinc-500"
                    )}
                  />
                  <span className="mono text-xs text-zinc-200">{name}</span>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-[10px] text-zinc-500">{step.status}</span>
                  {step.elapsed_seconds !== undefined && (
                    <span className="mono text-[10px] text-zinc-400">{nf(step.elapsed_seconds, 2)}s</span>
                  )}
                </div>
              </li>
            ))}
            {!steps.length && <li className="text-xs text-zinc-600">No recent run recorded.</li>}
          </ul>
        </Panel>

        <Panel>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-zinc-200">Data quality by source</h2>
            <span className="hud-label">{quality?.generated_at ? relative(quality.generated_at) : "—"}</span>
          </div>
          <ul className="space-y-3">
            {Object.entries(quality?.sources ?? {}).map(([source, s]) => (
              <li key={source}>
                <div className="mb-1 flex items-center justify-between text-xs">
                  <span className="mono text-zinc-200">{source}</span>
                  <span className="text-zinc-500">
                    {nf(s.silver_rows)} rows · {nf(s.quarantined_rows)} quarantined ·{" "}
                    <span className="text-emerald-400">{(s.pass_rate * 100).toFixed(1)}%</span>
                  </span>
                </div>
                <ProgressBar
                  value={s.pass_rate}
                  tone={s.pass_rate > 0.95 ? "emerald" : s.pass_rate > 0.8 ? "amber" : "red"}
                />
              </li>
            ))}
          </ul>
        </Panel>
      </div>

      <Panel>
        <div className="mb-3 flex items-center gap-2">
          <Wifi className="h-4 w-4 text-emerald-400" />
          <h2 className="text-sm font-semibold text-zinc-200">Collector stream health</h2>
          <span className="hud-label ml-auto">live via /live/status</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-zinc-500">
              <tr className="border-b border-white/5">
                <th className="py-2 pr-4 font-medium">Source</th>
                <th className="py-2 pr-4 font-medium">Status</th>
                <th className="py-2 pr-4 text-right font-medium">Records</th>
                <th className="py-2 text-right font-medium">Last update</th>
              </tr>
            </thead>
            <tbody className="mono">
              {ops.stream_health.map((s) => (
                <tr key={String(s.source)} className="border-b border-white/[0.03]">
                  <td className="py-1.5 pr-4 text-zinc-200">{String(s.source)}</td>
                  <td className="py-1.5 pr-4">
                    <span
                      className={cn(
                        "rounded-full px-2 py-px text-[10px]",
                        s.is_healthy ? "bg-emerald-500/15 text-emerald-300" : "bg-amber-500/15 text-amber-300"
                      )}
                    >
                      {s.is_healthy ? "fresh" : "stale"}
                    </span>
                  </td>
                  <td className="py-1.5 pr-4 text-right text-zinc-300">{nf(s.total_records ?? 0)}</td>
                  <td className="py-1.5 text-right text-zinc-500">
                    {s.last_success ? relative(String(s.last_success)) : "—"}
                  </td>
                </tr>
              ))}
              {!ops.stream_health.length && (
                <tr>
                  <td colSpan={4} className="py-4 text-center text-zinc-600">
                    Collector not reachable — no stream health.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel>
        <div className="mb-3 flex items-center gap-2">
          <ServerCrash className="h-4 w-4 text-amber-400" />
          <h2 className="text-sm font-semibold text-zinc-200">Warehouse inventory</h2>
          <span className="hud-label ml-auto">{catalog?.tables.length ?? 0} objects</span>
        </div>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {(catalog?.tables ?? []).map((t) => (
            <div
              key={t.name}
              className="flex items-center justify-between rounded-md border border-white/5 bg-white/[0.02] px-3 py-2"
            >
              <span className="mono truncate text-[11px] text-zinc-300">{t.name}</span>
              <span className="mono text-[10px] text-zinc-500">{nf(t.rows)}</span>
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}

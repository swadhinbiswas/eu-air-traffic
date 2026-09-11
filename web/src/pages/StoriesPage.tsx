import { useMemo } from "react";
import { Radio, Sparkles } from "lucide-react";
import { Panel, SectionHeader, Spinner, ErrorState, EmptyState, StatCard } from "@/components/shared";
import { BarSeries, DonutSeries, LineSeries, ScatterSeries } from "@/components/charts";
import { useStories } from "@/hooks/useBundle";
import { useFleet } from "@/hooks/useFleet";
import { useLiveWeather } from "@/hooks/useLiveWeather";
import { airlineFromCallsign } from "@/lib/airlines";
import { altitudeBand, isEmergency } from "@/lib/fleet";
import { nf } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Story } from "@/lib/bundle";

const TONE: Record<string, string> = {
  info: "border-cyan-500/25 bg-cyan-500/10 text-cyan-300",
  warning: "border-amber-500/25 bg-amber-500/10 text-amber-300",
  critical: "border-red-500/25 bg-red-500/10 text-red-300",
  positive: "border-emerald-500/25 bg-emerald-500/10 text-emerald-300",
};

const BAND_LABEL: Record<string, string> = {
  gnd: "Ground",
  low: "<10k ft",
  mid: "10–25k ft",
  high: "25–38k ft",
  upper: ">38k ft",
};

function StoryChart({ story }: { story: Story }) {
  const { chart } = story;
  if (chart.type === "bar" && chart.x && chart.y) {
    return <BarSeries data={chart.data} x={chart.x} y={chart.y} height={200} />;
  }
  if (chart.type === "line" && chart.x && chart.y) {
    return <LineSeries data={chart.data} x={chart.x} y={chart.y} height={200} color="#22d3ee" area />;
  }
  if (chart.type === "scatter" && chart.x && chart.y) {
    return <ScatterSeries data={chart.data} x={chart.x} y={chart.y} height={200} />;
  }
  if (chart.type === "doughnut") {
    return <DonutSeries data={chart.data} height={200} />;
  }
  return null;
}

export function StoriesPage() {
  const { data: stories, loading, error } = useStories();
  const fleet = useFleet();
  const weather = useLiveWeather();

  const live = useMemo(() => {
    const ac = fleet.aircraft;
    if (!ac.length) return null;
    const airlines = ac.reduce<Record<string, number>>((acc, a) => {
      const name = airlineFromCallsign(a.callsign)?.name ?? "Other";
      acc[name] = (acc[name] ?? 0) + 1;
      return acc;
    }, {});
    const topAirline = Object.entries(airlines).sort((a, b) => b[1] - a[1])[0];
    const bands = Object.entries(
      ac.reduce<Record<string, number>>((acc, a) => {
        const k = BAND_LABEL[altitudeBand(a.altFt)] ?? "?";
        acc[k] = (acc[k] ?? 0) + 1;
        return acc;
      }, {})
    ).map(([band, count]) => ({ band, count }));
    const emergencies = ac.filter(isEmergency).length;
    const withType = ac.filter((a) => a.type);
    const topType = Object.entries(
      withType.reduce<Record<string, number>>((acc, a) => {
        acc[a.type as string] = (acc[a.type as string] ?? 0) + 1;
        return acc;
      }, {})
    ).sort((a, b) => b[1] - a[1])[0];

    return {
      total: ac.length,
      topAirline,
      topType,
      emergencies,
      bands,
      source: fleet.source,
      wxStations: weather.stations.length,
      wxSource: weather.source,
    };
  }, [fleet.aircraft, fleet.source, weather.stations, weather.source]);

  if (loading) return <Spinner label="Loading data stories" />;
  if (error) return <ErrorState message={error} />;
  if (!stories?.length) return <EmptyState message="No stories were derived from the warehouse yet." />;

  return (
    <div className="space-y-6">
      <SectionHeader
        icon={<Sparkles className="h-4 w-4" />}
        title="Data Stories"
        subtitle="Narrative insights auto-derived from the Gold layer, live ADS-B fleet and aviation weather"
      />

      {live && (
        <>
          <div className="flex items-center gap-2">
            <Radio className="h-3.5 w-3.5 text-emerald-400" />
            <h2 className="text-sm font-semibold text-zinc-200">Live right now</h2>
            <span className="hud-label">
              {live.source === "live" ? "VPS collector" : live.source} ·{" "}
              {live.wxSource === "metar" ? "METAR" : "Open-Meteo"}
            </span>
          </div>

          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <StatCard label="Airborne" value={nf(live.total)} />
            <StatCard
              label="Top operator"
              value={live.topAirline?.[0] ?? "—"}
              hint={live.topAirline ? `${nf(live.topAirline[1])} aircraft` : undefined}
              tone="cyan"
            />
            <StatCard
              label="Most common type"
              value={live.topType?.[0] ?? "—"}
              hint={live.topType ? `${nf(live.topType[1])} aircraft` : undefined}
            />
            <StatCard
              label="Emergencies"
              value={nf(live.emergencies)}
              tone={live.emergencies ? "red" : "emerald"}
            />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Panel>
              <div className="mb-3 flex items-center justify-between">
                <h3 className="text-sm font-medium text-zinc-200">Live altitude distribution</h3>
                <span className="hud-label">aircraft by band</span>
              </div>
              <BarSeries data={live.bands} x="band" y="count" color="#34d399" />
            </Panel>
            <Panel className="flex flex-col justify-center gap-3">
              <p className="text-sm leading-relaxed text-zinc-300">
                Right now <span className="mono text-emerald-300">{nf(live.total)}</span> aircraft are
                broadcasting over Europe.{" "}
                {live.topAirline && (
                  <>
                    <span className="text-zinc-100">{live.topAirline[0]}</span> is the busiest operator
                    with <span className="mono text-cyan-300">{nf(live.topAirline[1])}</span> aircraft
                    airborne
                  </>
                )}
                {live.topType && (
                  <>
                    , and <span className="text-zinc-100">{live.topType[0]}</span> is the most common
                    airframe
                  </>
                )}
                . Aviation weather covers{" "}
                <span className="mono text-amber-300">{nf(live.wxStations)}</span> stations
                {live.wxSource === "metar" ? " via live METAR reports" : ""}.
              </p>
              <p className="text-[11px] text-zinc-500">
                Derived live from the ADS-B gateway and aviationweather.gov — no batch lag.
              </p>
            </Panel>
          </div>
        </>
      )}

      <div className="flex items-center gap-2">
        <Sparkles className="h-3.5 w-3.5 text-emerald-400" />
        <h2 className="text-sm font-semibold text-zinc-200">Batch insights</h2>
        <span className="hud-label">Gold layer</span>
      </div>

      <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
        {stories.map((story) => (
          <Panel key={story.id} className="flex flex-col gap-3">
            <div className="flex items-start justify-between gap-3">
              <span
                className={cn(
                  "rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider",
                  TONE[story.tone] ?? TONE.info
                )}
              >
                {story.category}
              </span>
              <div className="text-right">
                <div className="mono text-2xl font-semibold text-zinc-100">{story.metric}</div>
                <div className="text-[10px] text-zinc-500">{story.unit}</div>
              </div>
            </div>
            <h3 className="text-sm font-semibold leading-snug text-zinc-100">{story.title}</h3>
            <p className="text-xs leading-relaxed text-zinc-400">{story.narrative}</p>
            <div className="mt-auto pt-1">
              <StoryChart story={story} />
            </div>
          </Panel>
        ))}
      </div>
    </div>
  );
}


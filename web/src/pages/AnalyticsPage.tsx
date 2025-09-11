import { useMemo, type ReactNode } from "react";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  CloudSun,
  Database,
  Gauge,
  Plane,
  Route,
  Timer,
  TrendingUp,
  Wind,
} from "lucide-react";
import { Panel, SectionHeader, StatCard, Spinner, ErrorState } from "@/components/shared";
import { BarSeries, DonutSeries, LineSeries, ScatterSeries } from "@/components/charts";
import { NetworkMap } from "@/components/NetworkMap";
import { useAirports, useAnalytics, useKpis } from "@/hooks/useBundle";
import { useFleet } from "@/hooks/useFleet";
import { useLiveWeather } from "@/hooks/useLiveWeather";
import { airlineFromCallsign } from "@/lib/airlines";
import { altitudeBand, emergencyLabel, isEmergency } from "@/lib/fleet";
import { delayTone, nf, pct } from "@/lib/format";
import { cn } from "@/lib/utils";

const BAND_LABEL: Record<string, string> = {
  gnd: "Ground",
  low: "<10k ft",
  mid: "10–25k ft",
  high: "25–38k ft",
  upper: ">38k ft",
};

export function AnalyticsPage() {
  const { data: analytics, loading, error } = useAnalytics();
  const { data: airports } = useAirports();
  const { data: kpis } = useKpis();
  const fleet = useFleet();
  const liveWeather = useLiveWeather();

  const routes = useMemo(
    () =>
      (analytics?.routes ?? []).map((r) => ({
        origin: String(r.origin),
        destination: String(r.destination),
        total_flights: Number(r.total_flights ?? 0),
        avg_delay_minutes: Number(r.avg_delay_minutes ?? 0),
        distance_km: Number(r.distance_km ?? 0),
      })),
    [analytics]
  );

  const weatherSummary = useMemo(() => {
    const stations = liveWeather.stations;
    if (!stations.length) return null;
    const withTemp = stations.filter((s) => s.tempC !== null);
    const winds = stations.map((s) => s.windKt ?? 0);
    const temps = withTemp.map((s) => s.tempC as number);
    const avg = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0);
    const categories = Object.entries(
      stations.reduce<Record<string, number>>((acc, s) => {
        const key = s.category ?? "unknown";
        acc[key] = (acc[key] ?? 0) + 1;
        return acc;
      }, {})
    )
      .map(([label, value]) => ({ label, value }))
      .sort((a, b) => b.value - a.value);
    const windiest = [...stations]
      .sort((a, b) => (b.windKt ?? 0) - (a.windKt ?? 0))
      .slice(0, 10)
      .map((s) => ({ station: s.icao, wind: Math.round(s.windKt ?? 0) }));
    const coldest = [...withTemp].sort((a, b) => (a.tempC as number) - (b.tempC as number))[0];
    const warmest = [...withTemp].sort((a, b) => (b.tempC as number) - (a.tempC as number))[0];
    return {
      count: stations.length,
      source: liveWeather.source,
      avgTemp: avg(temps),
      avgWind: avg(winds),
      maxWind: Math.max(...winds, 0),
      categories,
      windiest,
      coldest,
      warmest,
    };
  }, [liveWeather]);

  const liveAirspace = useMemo(() => {
    const ac = fleet.aircraft;
    if (!ac.length) return null;
    const bands = Object.entries(
      ac.reduce<Record<string, number>>((acc, a) => {
        const key = altitudeBand(a.altFt);
        acc[key] = (acc[key] ?? 0) + 1;
        return acc;
      }, {})
    ).map(([band, count]) => ({ band: BAND_LABEL[band] ?? band, count }));

    const types = Object.entries(
      ac.reduce<Record<string, number>>((acc, a) => {
        const t = a.type || "unknown";
        acc[t] = (acc[t] ?? 0) + 1;
        return acc;
      }, {})
    )
      .map(([type, count]) => ({ type, count }))
      .sort((a, b) => b.count - a.count)
      .slice(0, 12);

    const airlines = Object.entries(
      ac.reduce<Record<string, number>>((acc, a) => {
        const airline = airlineFromCallsign(a.callsign);
        const key = airline ? airline.name : "Other";
        acc[key] = (acc[key] ?? 0) + 1;
        return acc;
      }, {})
    )
      .map(([airline, count]) => ({ airline, count }))
      .sort((a, b) => b.count - a.count)
      .slice(0, 12);

    const classes = Object.entries(
      ac.reduce<Record<string, number>>((acc, a) => {
        const key = a.aircraftClass || "other";
        acc[key] = (acc[key] ?? 0) + 1;
        return acc;
      }, {})
    )
      .map(([klass, count]) => ({ klass: klass.replace(/_/g, " "), count }))
      .sort((a, b) => b.count - a.count);

    const speeds = ac.map((a) => a.gsKt ?? 0).filter((s) => s > 0);
    const totalCo2 = ac.reduce((sum, a) => sum + (a.co2KgPerHour ?? 0), 0);
    const measuredCo2 = ac.reduce((sum, a) => sum + (!a.co2Estimated ? (a.co2KgPerHour ?? 0) : 0), 0);
    const emergencies = ac
      .filter(isEmergency)
      .slice(0, 8)
      .map((a) => ({
        callsign: a.callsign || a.hex,
        label: emergencyLabel(a) ?? "emergency",
        squawk: a.squawk ?? "",
      }));

    return {
      total: ac.length,
      avgSpeed: speeds.length ? speeds.reduce((a, b) => a + b, 0) / speeds.length : 0,
      maxSpeed: speeds.length ? Math.max(...speeds) : 0,
      military: ac.filter((a) => a.aircraftClass === "military").length,
      cargo: ac.filter((a) => a.aircraftClass === "cargo").length,
      helicopters: ac.filter((a) => a.aircraftClass === "helicopter").length,
      totalCo2,
      measuredCo2,
      bands,
      types,
      airlines,
      classes,
      emergencies,
      source: fleet.source,
    };
  }, [fleet.aircraft, fleet.source]);

  if (loading) return <Spinner label="Loading analytics" />;
  if (error || !analytics) return <ErrorState message={error ?? "No analytics bundle found"} />;

  const topAirports = [...analytics.gold_airport_metrics]
    .sort((a, b) => Number(b.total_flights) - Number(a.total_flights))
    .slice(0, 12)
    .map((r) => ({ ...r, airport_icao: String(r.airport_icao) }));

  const airlines = [...analytics.gold_airline_rankings].sort(
    (a, b) => Number(b.on_time_rate) - Number(a.on_time_rate)
  );

  const weather = [...analytics.gold_weather_impact].filter((r) => Number(r.flight_count) > 0);
  const hourly = Object.values(
    analytics.gold_seasonal_trends.reduce<Record<string, { hour: string; flights: number; delay: number; n: number }>>(
      (acc, r) => {
        const key = String(r.hour_of_day);
        acc[key] ??= { hour: key, flights: 0, delay: 0, n: 0 };
        acc[key].flights += Number(r.flight_count);
        acc[key].delay += Number(r.avg_delay_minutes);
        acc[key].n += 1;
        return acc;
      },
      {}
    )
  )
    .map((h) => ({ hour: h.hour, flights: h.flights, delay: h.delay / Math.max(1, h.n) }))
    .sort((a, b) => Number(a.hour) - Number(b.hour));

  const statusMix = analytics.status_mix.map((r) => ({
    label: String(r.status),
    value: Number(r.flight_count),
  }));

  return (
    <div className="space-y-6">
      <SectionHeader
        icon={<BarChart3 className="h-4 w-4" />}
        title="Business Analytics"
        subtitle="Gold-layer marts: traffic, punctuality, weather impact, network and emissions"
      />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <StatCard label="Flights observed" value={nf(kpis?.total_flights ?? 0)} icon={<Plane className="h-3.5 w-3.5" />} />
        <StatCard
          label="Avg delay"
          value={`${nf(kpis?.avg_delay_minutes ?? 0, 0)}m`}
          tone="amber"
          icon={<Timer className="h-3.5 w-3.5" />}
        />
        <StatCard
          label="Cancellation"
          value={pct(kpis?.cancellation_rate ?? 0)}
          tone="red"
          icon={<Activity className="h-3.5 w-3.5" />}
        />
        <StatCard label="Airports" value={nf(kpis?.airports ?? 0)} icon={<Route className="h-3.5 w-3.5" />} />
        <StatCard label="Airlines" value={nf(kpis?.airlines ?? 0)} icon={<TrendingUp className="h-3.5 w-3.5" />} />
        <StatCard
          label="Airborne now"
          value={nf(fleet.aircraft.length)}
          tone="emerald"
          icon={<Gauge className="h-3.5 w-3.5" />}
        />
      </div>

      <Panel className="p-0">
        <div className="flex items-center justify-between px-4 pt-4">
          <div>
            <h2 className="text-sm font-semibold text-zinc-200">European route network</h2>
            <p className="text-[11px] text-zinc-500">
              Airport traffic (node size) and delay (colour) · top {Math.min(120, routes.length)} arcs
            </p>
          </div>
        </div>
        <div className="p-3">
          <NetworkMap airports={airports ?? []} routes={routes} height={460} />
        </div>
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel>
          <PanelTitle icon={<Plane className="h-3.5 w-3.5" />} title="Busiest airports" hint="departures" />
          <BarSeries data={topAirports} x="airport_icao" y="total_flights" color="#34d399" />
        </Panel>

        <Panel>
          <PanelTitle icon={<TrendingUp className="h-3.5 w-3.5" />} title="Airline on-time rate" hint="share ≤ 15 min late" />
          <BarSeries data={airlines} x="airline_icao" y="on_time_rate" color="#22d3ee" />
        </Panel>

        <Panel>
          <PanelTitle icon={<CloudSun className="h-3.5 w-3.5" />} title="Weather impact on delay" hint="avg delay by condition" />
          <BarSeries data={weather} x="weather_condition" y="avg_delay_minutes" color="#fbbf24" />
        </Panel>

        <Panel>
          <PanelTitle icon={<Activity className="h-3.5 w-3.5" />} title="Flight status mix" hint="all observed flights" />
          <DonutSeries data={statusMix} />
        </Panel>

        <Panel>
          <PanelTitle icon={<Gauge className="h-3.5 w-3.5" />} title="Hourly traffic & delay" hint="UTC hour of day" />
          <LineSeries data={hourly} x="hour" y="flights" color="#60a5fa" area />
        </Panel>

        <Panel>
          <PanelTitle icon={<Route className="h-3.5 w-3.5" />} title="Route distance vs delay" hint="each point is a route" />
          <ScatterSeries data={routes} x="distance_km" y="avg_delay_minutes" color="#a78bfa" />
        </Panel>
      </div>

      <Panel>
        <PanelTitle icon={<Plane className="h-3.5 w-3.5" />} title="Airport leaderboard" hint="Gold mart · gold_airport_metrics" />
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-zinc-500">
              <tr className="border-b border-white/5">
                <th className="py-2 pr-4 font-medium">Airport</th>
                <th className="py-2 pr-4 text-right font-medium">Flights</th>
                <th className="py-2 pr-4 text-right font-medium">Avg delay</th>
                <th className="py-2 pr-4 text-right font-medium">Max delay</th>
                <th className="py-2 text-right font-medium">On-time</th>
              </tr>
            </thead>
            <tbody className="mono tabular-nums">
              {[...analytics.gold_airport_metrics]
                .sort((a, b) => Number(b.total_flights) - Number(a.total_flights))
                .slice(0, 20)
                .map((r) => (
                  <tr key={String(r.airport_icao)} className="border-b border-white/[0.03]">
                    <td className="py-1.5 pr-4 text-zinc-200">{String(r.airport_icao)}</td>
                    <td className="py-1.5 pr-4 text-right text-zinc-300">{nf(r.total_flights)}</td>
                    <td className={cn("py-1.5 pr-4 text-right", delayTone(r.avg_delay_minutes))}>
                      {nf(r.avg_delay_minutes, 0)}m
                    </td>
                    <td className="py-1.5 pr-4 text-right text-zinc-400">{nf(r.max_delay_minutes, 0)}m</td>
                    <td className="py-1.5 text-right text-emerald-400">{pct(r.on_time_rate)}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      </Panel>

      {/* ── Live weather (Open-Meteo) ─────────────────────────────────────── */}
      {weatherSummary && (
        <>
          <div className="flex items-end justify-between">
            <div>
              <h2 className="text-sm font-semibold text-zinc-200">Live weather — Open-Meteo</h2>
              <p className="text-[11px] text-zinc-500">
                Live aviation weather at {nf(weatherSummary.count)} European stations ·{" "}
                {weatherSummary.source === "metar" ? "METAR (aviationweather.gov)" : "Open-Meteo forecast"}
              </p>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <StatCard
              label="Stations"
              value={nf(weatherSummary.count)}
              icon={<CloudSun className="h-3.5 w-3.5" />}
            />
            <StatCard label="Avg temperature" value={`${nf(weatherSummary.avgTemp, 1)} °C`} tone="cyan" />
            <StatCard label="Avg wind" value={`${nf(weatherSummary.avgWind, 0)} kt`} tone="amber" icon={<Wind className="h-3.5 w-3.5" />} />
            <StatCard label="Peak wind" value={`${nf(weatherSummary.maxWind, 0)} kt`} tone="red" />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Panel>
              <PanelTitle
                icon={<CloudSun className="h-3.5 w-3.5" />}
                title="Flight category"
                hint="VFR / MVFR / IFR / LIFR"
              />
              <DonutSeries data={weatherSummary.categories} />
            </Panel>
            <Panel>
              <PanelTitle
                icon={<Wind className="h-3.5 w-3.5" />}
                title="Windiest stations"
                hint="kt sustained"
              />
              <BarSeries data={weatherSummary.windiest} x="station" y="wind" color="#22d3ee" />
            </Panel>
          </div>

          <Panel className="flex flex-wrap items-center justify-between gap-4">
            <div className="flex items-center gap-2 text-xs text-zinc-400">
              <Database className="h-3.5 w-3.5 text-emerald-400" />
              Weather sources:{" "}
              <a
                href="https://aviationweather.gov"
                target="_blank"
                rel="noreferrer"
                className="mono text-emerald-300 hover:underline"
              >
                aviationweather.gov
              </a>{" "}
              (METAR + TAF) and{" "}
              <a
                href="https://open-meteo.com"
                target="_blank"
                rel="noreferrer"
                className="mono text-emerald-300 hover:underline"
              >
                Open-Meteo
              </a>{" "}
              (forecast).
            </div>
            <div className="flex gap-6 text-xs">
              <span className="text-zinc-500">
                Coldest:{" "}
                <span className="mono text-cyan-300">
                  {weatherSummary.coldest?.icao} {nf(weatherSummary.coldest?.tempC, 1)}°C
                </span>
              </span>
              <span className="text-zinc-500">
                Warmest:{" "}
                <span className="mono text-amber-300">
                  {weatherSummary.warmest?.icao} {nf(weatherSummary.warmest?.tempC, 1)}°C
                </span>
              </span>
            </div>
          </Panel>
        </>
      )}

      {/* ── Live airspace (ADS-B) ─────────────────────────────────────────── */}
      {liveAirspace && (
        <>
          <div className="flex items-end justify-between">
            <div>
              <h2 className="text-sm font-semibold text-zinc-200">Live airspace — ADS-B</h2>
              <p className="text-[11px] text-zinc-500">
                {nf(liveAirspace.total)} aircraft currently airborne over Europe
                {liveAirspace.source === "live" ? " · live via VPS collector" : ""}
              </p>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <StatCard label="Airborne" value={nf(liveAirspace.total)} icon={<Plane className="h-3.5 w-3.5" />} />
            <StatCard label="Military" value={nf(liveAirspace.military)} tone="amber" />
            <StatCard label="Cargo" value={nf(liveAirspace.cargo)} tone="cyan" />
            <StatCard label="Helicopters" value={nf(liveAirspace.helicopters)} tone="emerald" />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Panel>
              <PanelTitle icon={<Plane className="h-3.5 w-3.5" />} title="Operational class mix" hint="cargo · military · passenger · private · helicopter…" />
              <BarSeries data={liveAirspace.classes} x="klass" y="count" color="#f59e0b" />
            </Panel>
            <Panel>
              <PanelTitle icon={<Gauge className="h-3.5 w-3.5" />} title="Altitude distribution" hint="aircraft by band" />
              <BarSeries data={liveAirspace.bands} x="band" y="count" color="#34d399" />
            </Panel>
            <Panel>
              <PanelTitle icon={<Plane className="h-3.5 w-3.5" />} title="Most common aircraft types" hint="live fleet" />
              <BarSeries data={liveAirspace.types} x="type" y="count" color="#22d3ee" />
            </Panel>
            <Panel>
              <PanelTitle icon={<TrendingUp className="h-3.5 w-3.5" />} title="Airlines from callsigns" hint="live operators" />
              <BarSeries data={liveAirspace.airlines} x="airline" y="count" color="#a78bfa" />
            </Panel>
            <Panel>
              <PanelTitle icon={<Gauge className="h-3.5 w-3.5" />} title="Live carbon intensity" hint="measured vs A320-fallback estimate" />
              <div className="grid grid-cols-3 gap-3 py-2">
                <StatCard label="Measured CO₂ / h" value={`${nf(liveAirspace.measuredCo2, 0)} kg`} tone="emerald" />
                <StatCard label="Estimated CO₂ / h" value={`${nf(liveAirspace.totalCo2 - liveAirspace.measuredCo2, 0)} kg`} tone="amber" />
                <StatCard label="Tonnes / hour (all)" value={`${nf(liveAirspace.totalCo2 / 1000, 2)} t`} tone="red" />
              </div>
            </Panel>
            <Panel>
              <PanelTitle icon={<AlertTriangle className="h-3.5 w-3.5" />} title="Active emergencies" hint="squawk 7500/7600/7700" />
              {liveAirspace.emergencies.length ? (
                <div className="space-y-1.5">
                  {liveAirspace.emergencies.map((e, i) => (
                    <div
                      key={i}
                      className="flex items-center justify-between rounded-md border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs"
                    >
                      <span className="mono text-red-300">{e.callsign}</span>
                      <span className="text-red-300/80">{e.label}</span>
                      <span className="mono text-red-300/60">{e.squawk}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="grid h-[180px] place-items-center text-xs text-zinc-500">
                  No active emergencies — network nominal.
                </div>
              )}
            </Panel>
          </div>
        </>
      )}
    </div>
  );
}

function PanelTitle({ icon, title, hint }: { icon: ReactNode; title: string; hint: string }) {
  return (
    <div className="mb-3 flex items-center justify-between">
      <div className="flex items-center gap-2">
        <span className="text-emerald-400">{icon}</span>
        <h3 className="text-sm font-medium text-zinc-200">{title}</h3>
      </div>
      <span className="hud-label">{hint}</span>
    </div>
  );
}

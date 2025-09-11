import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle,
  ArrowUpRight,
  Gauge,
  Layers,
  Pause,
  Play,
  Radar,
  Search,
  Sparkles,
  Thermometer,
  Wind,
  X,
} from "lucide-react";
import { WorldMap, type LayerId } from "@/components/map/WorldMap";
import type { MapSelection } from "@/components/map/types";
import { ALT_BANDS } from "@/components/map/planeIcons";
import {
  CATEGORY_COLORS,
  CATEGORY_LABEL,
  type WeatherStationView,
} from "@/lib/weather";
import { useFleet } from "@/hooks/useFleet";
import { useLiveWeather } from "@/hooks/useLiveWeather";
import { useAirports, useAnalytics, useKpis, useStories } from "@/hooks/useBundle";
import { emergencyLabel, isEmergency, matchesQuery, type Aircraft } from "@/lib/fleet";
import { delayTone, nf, pct } from "@/lib/format";
import { tryWorker } from "@/lib/bundle";
import type { Airport, TafRecord } from "@/lib/bundle";
import { cn } from "@/lib/utils";

const LAYERS: { id: LayerId; label: string }[] = [
  { id: "aircraft", label: "Aircraft" },
  { id: "airports", label: "Airports" },
  { id: "routes", label: "Routes" },
  { id: "weather", label: "Weather" },
  { id: "radar", label: "Radar" },
  { id: "terminator", label: "Night" },
];

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-white/[0.04] py-1.5 last:border-0">
      <span className="hud-label flex-none">{label}</span>
      <span className="mono truncate text-right text-xs text-zinc-200">{children}</span>
    </div>
  );
}

function PanelShell({
  title,
  subtitle,
  accent,
  onClose,
  children,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  accent?: string;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <div className="panel animate-float-in w-80 p-4">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0" style={accent ? { color: accent } : undefined}>
          <div className="mono truncate text-base font-semibold text-zinc-100">{title}</div>
          {subtitle && <div className="truncate text-[11px] text-zinc-500">{subtitle}</div>}
        </div>
        <button onClick={onClose} className="flex-none text-zinc-500 hover:text-zinc-200">
          <X className="h-4 w-4" />
        </button>
      </div>
      {children}
    </div>
  );
}

function bandColor(altFt: number | null): string {
  if (altFt === null) return "#94a3b8";
  return ALT_BANDS.find((b) => altFt >= b.min && altFt < b.max)?.color ?? "#94a3b8";
}

function AircraftInfo({ plane, onClose }: { plane: Aircraft; onClose: () => void }) {
  const [enrich, setEnrich] = useState<string | null>(null);
  const emergency = emergencyLabel(plane);

  useEffect(() => {
    let alive = true;
    setEnrich(null);
    tryWorker<{ found: boolean; operator?: string | null; type?: string | null }>(
      `/live/aircraft/${plane.hex}`
    ).then((d) => {
      if (alive && d?.found) setEnrich(d.operator || d.type || null);
    });
    return () => {
      alive = false;
    };
  }, [plane.hex]);

  return (
    <PanelShell
      title={
        <span className="flex items-center gap-2">
          {emergency && <AlertTriangle className="h-4 w-4 text-red-400" />}
          {plane.callsign || plane.hex}
        </span>
      }
      subtitle={[plane.reg, plane.type, enrich].filter(Boolean).join(" · ") || plane.hex}
      onClose={onClose}
    >
      {emergency && (
        <div className="mb-3 flex items-center gap-2 rounded-md border border-red-500/30 bg-red-500/10 px-2.5 py-1.5 text-[11px] text-red-300">
          <AlertTriangle className="h-3.5 w-3.5" />
          Emergency: {emergency}
          {plane.squawk ? ` · squawk ${plane.squawk}` : ""}
        </div>
      )}
      <div className="grid grid-cols-2 gap-2">
        <Metric icon={<Gauge className="h-3 w-3" />} label="Altitude">
          <span style={{ color: bandColor(plane.altFt) }}>
            {plane.altFt !== null ? `${nf(plane.altFt)} ft` : "—"}
          </span>
        </Metric>
        <Metric icon={<Wind className="h-3 w-3" />} label="Ground speed">
          {plane.gsKt !== null ? `${nf(plane.gsKt, 0)} kt` : "—"}
        </Metric>
        <Metric icon={<Radar className="h-3 w-3" />} label="Track">
          {plane.trackDeg !== null ? `${nf(plane.trackDeg, 0)}°` : "—"}
        </Metric>
        <Metric icon={<ArrowUpRight className="h-3 w-3" />} label="Vert rate">
          {plane.verticalRateFpm !== null ? `${nf(plane.verticalRateFpm, 0)} fpm` : "—"}
        </Metric>
      </div>
      <div className="mt-3">
        <Row label="Mach">{plane.mach !== null ? plane.mach.toFixed(3) : "—"}</Row>
        <Row label="IAS / TAS">
          {plane.ias !== null ? `${nf(plane.ias, 0)}` : "—"} / {plane.tas !== null ? `${nf(plane.tas, 0)} kt` : "—"}
        </Row>
        <Row label="OAT">
          {plane.oat !== null ? `${nf(plane.oat, 0)} °C` : "—"}
          {plane.tat !== null ? <span className="text-zinc-500"> · TAT {nf(plane.tat, 0)} °C</span> : null}
        </Row>
        <Row label="Route">{plane.route ?? "—"}</Row>
        <Row label="QNH">{plane.navQnh !== null ? `${nf(plane.navQnh, 1)} hPa` : "—"}</Row>
        <Row label="Wind aloft">
          {plane.windSpeedKt !== null ? `${nf(plane.windSpeedKt, 0)} kt` : "—"}
          {plane.windDir !== null ? ` @ ${nf(plane.windDir, 0)}°` : ""}
        </Row>
        <Row label="Squawk">{plane.squawk ?? "—"}</Row>
        <Row label="Class">
          <span className="uppercase tracking-wide">{plane.aircraftClass ?? "unclassified"}</span>
          {plane.emitterClass ? <span className="text-zinc-500"> · {plane.emitterClass}</span> : null}
        </Row>
        <Row label="Operator">
          {plane.operatorName ? (
            <>
              {plane.operatorName}
              {plane.operatorCountry ? (
                <span className="text-zinc-500"> · {plane.operatorCountry}</span>
              ) : null}
            </>
          ) : (
            "—"
          )}
        </Row>
        <Row label="Type">
          {plane.typeName ?? plane.type ?? "—"}
          {plane.manufacturer ? <span className="text-zinc-500"> · {plane.manufacturer}</span> : null}
        </Row>
        {plane.wakeCategory ? <Row label="Wake cat.">{plane.wakeCategory}</Row> : null}
        <Row label="CO₂ rate">
          {plane.co2KgPerHour !== null ? `${nf(plane.co2KgPerHour, 0)} kg/h` : "—"}
        </Row>
        <Row label="Fuel burn">
          {plane.fuelBurnKgPerHour !== null ? `${nf(plane.fuelBurnKgPerHour, 0)} kg/h` : "—"}
        </Row>
        <Row label="ICAO24">{plane.hex}</Row>
      </div>
      <div className="mt-3 flex items-center justify-between text-[10px] text-zinc-500">
        <span className="mono">
          {plane.lat.toFixed(3)}, {plane.lon.toFixed(3)}
        </span>
        <span>
          {plane.source} · {plane.seen !== null ? `${plane.seen.toFixed(0)}s ago` : "live"}
        </span>
      </div>
    </PanelShell>
  );
}

function AirportInfo({ airport, onClose }: { airport: Airport; onClose: () => void }) {
  return (
    <PanelShell
      title={airport.icao}
      subtitle={`${airport.name}${airport.city ? ` · ${airport.city}` : ""}, ${airport.country}`}
      onClose={onClose}
    >
      <Row label="Flights">{nf(airport.total_flights)}</Row>
      <Row label="Avg delay">
        <span className={delayTone(airport.avg_delay_minutes)}>
          {airport.avg_delay_minutes !== null ? `${nf(airport.avg_delay_minutes, 0)} min` : "—"}
        </span>
      </Row>
      <Row label="On-time">{pct(airport.on_time_rate)}</Row>
      <Row label="IATA">{airport.iata ?? "—"}</Row>
      <Row label="Elevation">{airport.elevation_ft !== null ? `${nf(airport.elevation_ft)} ft` : "—"}</Row>
    </PanelShell>
  );
}

function WeatherInfo({ station, onClose }: { station: WeatherStationView; onClose: () => void }) {
  const [taf, setTaf] = useState<string | null>(null);
  const cat = station.category;

  useEffect(() => {
    let alive = true;
    setTaf(null);
    tryWorker<{ taf: TafRecord[] }>(`/live/taf?ids=${station.icao}`).then((d) => {
      const row = d?.taf?.find((t) => t.icao === station.icao) ?? d?.taf?.[0];
      if (alive && row?.rawTAF) setTaf(row.rawTAF);
    });
    return () => {
      alive = false;
    };
  }, [station.icao]);

  const temps = station.hourly.map((h) => h.temperature_c).filter((t): t is number => t !== null);
  const maxTemp = temps.length ? Math.max(...temps) : 0;
  const minTemp = temps.length ? Math.min(...temps) : 0;

  return (
    <PanelShell
      title={station.icao}
      subtitle={`${station.name} · ${station.source === "metar" ? "METAR" : "Open-Meteo"}`}
      onClose={onClose}
    >
      {cat && (
        <div
          className="mb-3 rounded-md border px-2.5 py-1.5 text-[11px]"
          style={{
            borderColor: `${CATEGORY_COLORS[cat]}55`,
            background: `${CATEGORY_COLORS[cat]}18`,
            color: CATEGORY_COLORS[cat],
          }}
        >
          {CATEGORY_LABEL[cat]}
        </div>
      )}
      <Row label="Temperature">
        {station.tempC !== null ? `${nf(station.tempC, 1)} °C` : "—"}
      </Row>
      {station.dewpointC !== null && (
        <Row label="Dewpoint">{`${nf(station.dewpointC, 1)} °C`}</Row>
      )}
      <Row label="Wind">
        {station.windKt !== null ? `${nf(station.windKt, 0)} kt` : "—"}
        {station.windDirDeg !== null ? ` @ ${nf(station.windDirDeg, 0)}°` : ""}
      </Row>
      {station.gustKt !== null && <Row label="Gusts">{`${nf(station.gustKt, 0)} kt`}</Row>}
      {station.visibility && <Row label="Visibility">{String(station.visibility)}</Row>}
      {station.altimeter !== null && <Row label="Altimeter">{`${nf(station.altimeter, 0)} hPa`}</Row>}
      {station.cover && <Row label="Sky">{station.cover}</Row>}
      {station.clouds.length > 0 && (
        <Row label="Clouds">
          {station.clouds
            .map((c) => `${c.cover ?? ""}${c.base !== undefined ? ` ${c.base}` : ""}`.trim())
            .join(", ")}
        </Row>
      )}
      {temps.length > 1 && (
        <Row label="24h range">{`${nf(minTemp, 0)}° → ${nf(maxTemp, 0)}°`}</Row>
      )}

      {station.raw && (
        <div className="mt-3">
          <div className="hud-label mb-1">Raw METAR</div>
          <pre className="mono whitespace-pre-wrap rounded-md border border-white/5 bg-white/[0.02] p-2 text-[10px] leading-relaxed text-zinc-400">
            {station.raw}
          </pre>
        </div>
      )}
      {taf && (
        <div className="mt-2">
          <div className="hud-label mb-1">TAF</div>
          <pre className="mono whitespace-pre-wrap rounded-md border border-white/5 bg-white/[0.02] p-2 text-[10px] leading-relaxed text-zinc-400">
            {taf}
          </pre>
        </div>
      )}
    </PanelShell>
  );
}

function Metric({
  icon,
  label,
  children,
}: {
  icon: ReactNode;
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="rounded-md border border-white/5 bg-white/[0.03] p-2">
      <div className="hud-label flex items-center gap-1">
        {icon}
        {label}
      </div>
      <div className="mono text-sm text-zinc-100">{children}</div>
    </div>
  );
}

export function LandingPage() {
  const fleet = useFleet();
  const weatherState = useLiveWeather();
  const { data: airports } = useAirports();
  const { data: analytics } = useAnalytics();
  const { data: kpis } = useKpis();
  const { data: stories } = useStories();

  const [visibleLayers, setVisibleLayers] = useState<Set<LayerId>>(
    new Set(["aircraft", "airports", "routes", "weather", "radar", "terminator"])
  );
  const [autoRotate, setAutoRotate] = useState(true);
  const [selected, setSelected] = useState<MapSelection | null>(null);
  const [query, setQuery] = useState("");

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

  const stats = useMemo(() => {
    const alts = fleet.aircraft.map((a) => a.altFt ?? 0).filter((a) => a > 0);
    const emergencies = fleet.aircraft.filter(isEmergency).length;
    return {
      avgAlt: alts.length ? alts.reduce((a, b) => a + b, 0) / alts.length : 0,
      cruising: fleet.aircraft.filter((a) => (a.altFt ?? 0) > 25000).length,
      emergencies,
    };
  }, [fleet.aircraft]);

  const onTime = useMemo(() => {
    const metrics = analytics?.gold_airport_metrics ?? [];
    const total = metrics.reduce((sum, r) => sum + Number(r.total_flights ?? 0), 0);
    if (!total) return null;
    const weighted = metrics.reduce(
      (sum, r) => sum + Number(r.on_time_rate ?? 0) * Number(r.total_flights ?? 0),
      0
    );
    return weighted / total;
  }, [analytics]);

  const toggleLayer = useCallback((id: LayerId) => {
    setVisibleLayers((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const handleSearch = useCallback(
    (value: string) => {
      setQuery(value);
      if (!value.trim()) return;
      const plane = fleet.aircraft.find((a) => matchesQuery(a, value));
      if (plane) {
        setSelected({ kind: "aircraft", data: plane });
        return;
      }
      const ap = airports?.find((a) => a.icao.toUpperCase().includes(value.trim().toUpperCase()));
      if (ap) {
        setSelected({ kind: "airport", data: ap });
        return;
      }
      const station = weatherState.stations.find((s) =>
        s.icao.toUpperCase().includes(value.trim().toUpperCase())
      );
      if (station) setSelected({ kind: "weather", data: station });
    },
    [fleet.aircraft, airports, weatherState.stations]
  );

  const ticker = useMemo(() => {
    const items = [
      ...(stories ?? []).slice(0, 6).map((s) => `${s.category.toUpperCase()}: ${s.title}`),
      ...fleet.aircraft
        .slice(0, 16)
        .map(
          (a) =>
            `${a.callsign || a.hex} · ${a.reg ?? a.type ?? ""} · FL${Math.round((a.altFt ?? 0) / 100)} · ${a.gsKt?.toFixed(0) ?? "—"} kt`
        ),
    ];
    return items.length ? items : ["Awaiting live feed…"];
  }, [stories, fleet.aircraft]);

  const statusLabel =
    fleet.status === "live"
      ? fleet.source === "live"
        ? "LIVE ADS-B"
        : "LIVE API"
      : fleet.status === "connecting"
        ? "CONNECTING"
        : "SNAPSHOT";

  return (
    <div className="relative h-full w-full overflow-hidden bg-black">
      <WorldMap
        visibleLayers={visibleLayers}
        aircraft={fleet.aircraft}
        airports={airports ?? []}
        weather={weatherState.stations}
        routes={routes}
        autoRotate={autoRotate}
        onSelect={setSelected}
      />

      {/* Top-left: brand + layers */}
      <div className="pointer-events-none absolute left-4 top-4 z-10 flex flex-col gap-3">
        <div className="pointer-events-auto panel flex items-center gap-3 px-3 py-2">
          <span className="relative grid h-7 w-7 place-items-center rounded-lg bg-emerald-500/15 text-emerald-400">
            <Radar className="h-4 w-4" />
            <span className="absolute inset-0 rounded-lg text-emerald-400 pulse-ring" />
          </span>
          <div className="leading-tight">
            <div className="text-xs font-semibold text-zinc-100">European Airspace</div>
            <div className="mono text-[10px] text-zinc-500">
              {statusLabel} · {nf(fleet.aircraft.length)} targets
            </div>
          </div>
        </div>

        <div className="pointer-events-auto panel flex flex-wrap items-center gap-1 p-1.5">
          <Layers className="ml-1 mr-1 h-3.5 w-3.5 text-zinc-500" />
          {LAYERS.map((l) => (
            <button
              key={l.id}
              onClick={() => toggleLayer(l.id)}
              className={cn(
                "rounded-md px-2 py-1 text-[11px] transition-colors",
                visibleLayers.has(l.id)
                  ? "bg-emerald-500/15 text-emerald-300"
                  : "text-zinc-500 hover:text-zinc-300"
              )}
            >
              {l.label}
            </button>
          ))}
          <span className="mx-1 h-4 w-px bg-white/10" />
          <button
            onClick={() => setAutoRotate((v) => !v)}
            className={cn(
              "flex items-center gap-1 rounded-md px-2 py-1 text-[11px] transition-colors",
              autoRotate ? "bg-cyan-500/15 text-cyan-300" : "text-zinc-500 hover:text-zinc-300"
            )}
          >
            {autoRotate ? <Pause className="h-3 w-3" /> : <Play className="h-3 w-3" />}
            Orbit
          </button>
        </div>
      </div>

      {/* Top-center: search */}
      <div className="pointer-events-none absolute left-1/2 top-4 z-10 w-80 max-w-[80vw] -translate-x-1/2">
        <div className="pointer-events-auto panel flex items-center gap-2 px-3 py-2">
          <Search className="h-3.5 w-3.5 text-zinc-500" />
          <input
            value={query}
            onChange={(e) => handleSearch(e.target.value)}
            placeholder="Search callsign, registration, type, airport…"
            className="w-full bg-transparent text-xs text-zinc-100 outline-none placeholder:text-zinc-600"
          />
        </div>
      </div>

      {/* Top-right: selection panel */}
      {selected && (
        <div className="absolute right-4 top-4 z-10">
          {selected.kind === "aircraft" && (
            <AircraftInfo plane={selected.data} onClose={() => setSelected(null)} />
          )}
          {selected.kind === "airport" && (
            <AirportInfo airport={selected.data} onClose={() => setSelected(null)} />
          )}
          {selected.kind === "weather" && (
            <WeatherInfo station={selected.data} onClose={() => setSelected(null)} />
          )}
        </div>
      )}

      {/* Bottom-left: KPI strip */}
      <div className="pointer-events-none absolute bottom-16 left-4 z-10 flex flex-wrap gap-2">
        <HudStat label="Live aircraft" value={nf(fleet.aircraft.length)} />
        <HudStat label="Cruising >FL250" value={nf(stats.cruising)} />
        <HudStat label="Avg altitude" value={`${nf(stats.avgAlt)} ft`} />
        <HudStat
          label="Emergencies"
          value={nf(stats.emergencies)}
          tone={stats.emergencies > 0 ? "text-red-400" : undefined}
        />
        <HudStat label="Weather stns" value={nf(weatherState.stations.length)} />
        <HudStat
          label="Avg delay"
          value={`${nf(kpis?.avg_delay_minutes ?? 0, 0)} min`}
          tone={delayTone(kpis?.avg_delay_minutes)}
        />
        <HudStat label="On-time" value={onTime !== null ? pct(onTime) : "—"} />
      </div>

      {/* Bottom-right: legends (offset left of the map zoom controls) */}
      <div className="pointer-events-none absolute bottom-16 right-16 z-10 flex flex-col gap-2">
        <div className="panel px-3 py-2">
          <div className="hud-label mb-1.5">Flight category</div>
          <div className="flex items-center gap-3">
            {(["VFR", "MVFR", "IFR", "LIFR"] as const).map((c) => (
              <span key={c} className="flex items-center gap-1 text-[10px] text-zinc-400">
                <span className="h-2 w-2 rounded-full" style={{ background: CATEGORY_COLORS[c] }} />
                {c}
              </span>
            ))}
          </div>
        </div>
        <div className="panel px-3 py-2">
          <div className="hud-label mb-1.5">Altitude bands</div>
          <div className="flex items-center gap-3">
            {[
              ["GND", "#94a3b8"],
              ["<10k", "#22d3ee"],
              ["10–25k", "#34d399"],
              ["25–38k", "#fbbf24"],
              [">38k", "#f87171"],
            ].map(([label, color]) => (
              <span key={label} className="flex items-center gap-1 text-[10px] text-zinc-400">
                <span className="h-2 w-2 rounded-full" style={{ background: color as string }} />
                {label}
              </span>
            ))}
          </div>
        </div>
      </div>

      {/* Bottom: ticker + stories link */}
      <div className="absolute bottom-0 left-0 right-0 z-10 flex items-center gap-4 border-t border-white/10 bg-black/70 px-4 py-2 backdrop-blur-xl">
        <Link
          to="/stories"
          className="flex flex-none items-center gap-1.5 rounded-md border border-emerald-500/20 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-300 transition-colors hover:bg-emerald-500/20"
        >
          <Sparkles className="h-3 w-3" />
          Data stories
          <ArrowUpRight className="h-3 w-3" />
        </Link>
        <div className="relative flex-1 overflow-hidden">
          <div className="animate-ticker flex w-max gap-8 whitespace-nowrap">
            {[...ticker, ...ticker].map((t, i) => (
              <span key={i} className="mono text-[10px] text-zinc-500">
                {t}
              </span>
            ))}
          </div>
        </div>
        <span className="mono hidden flex-none text-[10px] text-zinc-600 lg:block">
          © CARTO · OSM · RainViewer
        </span>
      </div>
    </div>
  );
}

function HudStat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="panel px-3 py-1.5">
      <div className="hud-label">{label}</div>
      <div className={cn("mono text-sm font-semibold text-zinc-100", tone)}>{value}</div>
    </div>
  );
}

export { Thermometer };

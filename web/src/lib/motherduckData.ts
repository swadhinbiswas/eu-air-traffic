/**
 * MotherDuck-backed dashboard data — the runtime replacement for the static
 * bundle. Every function returns the exact JSON shape the pages expect, so
 * `loadBundle()` swaps sources without touching a single page.
 *
 * Sources:
 *  - analytics / kpis / airports / catalog / stories / manifest → MotherDuck Gold
 *  - ops.pipeline / ops.quality → MotherDuck `site_ops`
 *  - ops.stream_health, positions, metars, weather → VPS live snapshot
 */
import { runMotherDuck } from "./motherduck";
import { tryLiveApi, type LiveSnapshot } from "./bundle";
import type {
  Analytics,
  Airport,
  BundleManifest,
  Catalog,
  CatalogTable,
  Kpis,
  Metar,
  Ops,
  Position,
  Story,
  WeatherStation,
} from "./bundle";

type Row = Record<string, unknown>;

/** Shape of the collector's `GET /live/status`. */
interface LiveStatusResponse {
  status: string;
  sections: Record<string, number>;
  reference: Record<string, number>;
  updatedAt: Record<string, string>;
  ageSeconds: Record<string, number | null>;
  maxAgeSeconds: number;
  totals: Record<string, number>;
}
const str = (v: unknown, fallback = ""): string => (v === null || v === undefined ? fallback : String(v));
const num = (v: unknown, fallback = 0): number => {
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : fallback;
};

async function q<T = Row>(sql: string, limit = 5000): Promise<T[]> {
  const { rows } = await runMotherDuck(`${sql} LIMIT ${limit}`);
  return rows as T[];
}

async function scalar<T>(sql: string, fallback: T): Promise<T> {
  try {
    const { rows } = await runMotherDuck(sql);
    const first = rows[0];
    if (!first) return fallback;
    const v = Object.values(first)[0];
    return (v ?? fallback) as T;
  } catch {
    return fallback;
  }
}

/** All 7 Gold marts + the Explorer datasets + catalog tables. */
export async function fetchAnalytics(): Promise<Analytics> {
  const [
    gold_airport_metrics,
    gold_airline_rankings,
    gold_delay_analysis,
    gold_weather_impact,
    gold_seasonal_trends,
    gold_fuel_price_series,
    routes,
    fleet,
    emissions,
    notam_summary,
    status_mix,
    catalog,
  ] = await Promise.all([
    q("SELECT * FROM main.gold_airport_metrics"),
    q("SELECT * FROM main.gold_airline_rankings"),
    q("SELECT * FROM main.gold_delay_analysis"),
    q("SELECT * FROM main.gold_weather_impact"),
    q("SELECT * FROM main.gold_seasonal_trends"),
    q("SELECT * FROM main.gold_fuel_price_series"),
    q(
      "SELECT r.origin, r.destination, r.airline, r.stops, r.equipment, r.distance_km, " +
        "COUNT(f.flight_id) AS total_flights, AVG(f.delay_minutes) AS avg_delay_minutes " +
        "FROM main.dim_route r LEFT JOIN main.fact_flights f " +
        "ON f.departure_icao = r.origin AND f.arrival_icao = r.destination " +
        "GROUP BY 1,2,3,4,5,6 ORDER BY total_flights DESC, distance_km DESC",
      2000
    ),
    q("SELECT type_icao, manufacturer, family, engine, capacity, range_km FROM main.dim_aircraft ORDER BY capacity DESC"),
    q("SELECT aircraft_type, fuel_burn_liters_per_hour, co2_kg_per_hour FROM main.fact_emissions ORDER BY co2_kg_per_hour DESC"),
    q("SELECT icao_location, COUNT(*) AS notam_count FROM main.fact_notams GROUP BY icao_location ORDER BY notam_count DESC"),
    q("SELECT status, COUNT(*) AS flight_count, ROUND(AVG(delay_minutes), 1) AS avg_delay FROM main.fact_flights GROUP BY status ORDER BY flight_count DESC"),
    fetchCatalog(),
  ]);
  return {
    gold_airport_metrics,
    gold_airline_rankings,
    gold_delay_analysis,
    gold_weather_impact,
    gold_seasonal_trends,
    gold_fuel_price_series,
    routes,
    fleet,
    emissions,
    notam_summary,
    status_mix,
    catalog,
  } as unknown as Analytics;
}

export async function fetchKpis(): Promise<Kpis> {
  const [total_flights, avg_delay_minutes, cancelled, airlines, airports] = await Promise.all([
    scalar<number>("SELECT COUNT(*) FROM main.fact_flights", 0),
    scalar<number>("SELECT AVG(delay_minutes) FROM main.fact_flights WHERE status != 'cancelled'", 0),
    scalar<number>("SELECT COUNT(*) FROM main.fact_flights WHERE status = 'cancelled'", 0),
    scalar<number>("SELECT COUNT(*) FROM main.dim_airline", 0),
    scalar<number>("SELECT COUNT(*) FROM main.dim_airport", 0),
  ]);
  return {
    total_flights,
    avg_delay_minutes: Math.round(avg_delay_minutes * 100) / 100,
    cancellation_rate: total_flights ? Math.round((cancelled / total_flights) * 10000) / 10000 : 0,
    airports,
    airlines,
    live_aircraft: 0,
    metars: 0,
  };
}

export async function fetchAirports(): Promise<Airport[]> {
  const rows = await q(
    "SELECT a.airport_icao AS icao, a.iata_code AS iata, a.name, a.municipality AS city, " +
      "a.iso_country AS country, a.latitude_deg AS lat, a.longitude_deg AS lon, " +
      "a.elevation_ft, a.type, COALESCE(m.total_flights, 0) AS total_flights, " +
      "m.avg_delay_minutes, m.on_time_rate " +
      "FROM main.dim_airport a LEFT JOIN main.gold_airport_metrics m " +
      "ON m.airport_icao = a.airport_icao ORDER BY a.airport_icao",
    3000
  );
  return rows.map((r) => ({
    icao: str(r.icao),
    iata: (r.iata as string | null) ?? null,
    name: str(r.name),
    city: (r.city as string | null) ?? null,
    country: (r.country as string | null) ?? null,
    lat: num(r.lat),
    lon: num(r.lon),
    elevation_ft: r.elevation_ft === null ? null : num(r.elevation_ft),
    type: str(r.type, "airport"),
    total_flights: num(r.total_flights),
    avg_delay_minutes: r.avg_delay_minutes === null ? null : num(r.avg_delay_minutes),
    on_time_rate: r.on_time_rate === null ? null : num(r.on_time_rate),
  }));
}

export async function fetchCatalog(): Promise<Catalog> {
  const tables = await q<{
    table_schema: string;
    table_name: string;
    table_type: string;
  }>(
    "SELECT table_schema, table_name, table_type FROM information_schema.tables " +
      "WHERE table_schema IN ('main','staging','marts','reports') " +
      "ORDER BY table_schema, table_name",
    500
  );
  const out: CatalogTable[] = [];
  for (const t of tables) {
    const cols = await q<{ column_name: string; data_type: string; is_nullable: string }>(
      `SELECT column_name, data_type, is_nullable FROM information_schema.columns ` +
        `WHERE table_schema = '${str(t.table_schema)}' AND table_name = '${str(t.table_name)}' ` +
        `ORDER BY ordinal_position`,
      200
    ).catch((): Array<{ column_name: string; data_type: string; is_nullable: string }> => []);
    const count = await scalar<number>(
      `SELECT COUNT(*) FROM "${str(t.table_schema)}"."${str(t.table_name)}"`,
      0
    ).catch(() => 0);
    const layer = t.table_name.startsWith("gold_")
      ? "gold"
      : t.table_name.startsWith("fact_")
        ? "fact"
        : t.table_name.startsWith("dim_")
          ? "dim"
          : t.table_schema === "marts" || t.table_schema === "staging"
            ? t.table_schema
            : "raw";
    out.push({
      schema: str(t.table_schema),
      name: str(t.table_name),
      kind: str(t.table_type),
      layer,
      rows: count,
      columns: cols.map((c) => ({
        name: str(c.column_name),
        type: str(c.data_type),
        nullable: str(c.is_nullable) === "YES",
      })),
    });
  }
  let lineage: Catalog["lineage"] = { nodes: [], edges: [], sources: [] };
  try {
    const { rows } = await runMotherDuck(
      "SELECT payload_json FROM main.site_lineage WHERE key = 'lineage' LIMIT 1"
    );
    const raw = rows[0]?.payload_json;
    if (typeof raw === "string") lineage = { ...lineage, ...(JSON.parse(raw) as Catalog["lineage"]) };
  } catch {
    /* lineage unavailable — tables still render */
  }
  return { tables: out, lineage };
}

export async function fetchStories(): Promise<Story[]> {
  const { rows } = await runMotherDuck(
    "SELECT id, category, tone, title, metric, unit, narrative, chart_json " +
      "FROM main.site_stories ORDER BY id"
  );
  return rows.map((r) => ({
    id: str(r.id),
    category: str(r.category),
    tone: (["info", "warning", "critical", "positive"] as const).includes(r.tone as never)
      ? (r.tone as Story["tone"])
      : "info",
    title: str(r.title),
    metric: str(r.metric),
    unit: str(r.unit),
    narrative: str(r.narrative),
    chart: (() => {
      try {
        return JSON.parse(str(r.chart_json, "{}")) as Story["chart"];
      } catch {
        return { type: "stat", data: [] } as Story["chart"];
      }
    })(),
  }));
}

export async function fetchOps(): Promise<Ops> {
  let pipeline: Ops["pipeline"] = null;
  let quality: Ops["quality"] = null;
  try {
    const { rows } = await runMotherDuck(
      "SELECT payload_json FROM main.site_ops WHERE key = 'latest' LIMIT 1"
    );
    const raw = rows[0]?.payload_json;
    if (typeof raw === "string") {
      const parsed = JSON.parse(raw) as { pipeline?: Ops["pipeline"]; quality?: Ops["quality"] };
      pipeline = parsed.pipeline ?? null;
      quality = parsed.quality ?? null;
    }
  } catch {
    /* ops reports unavailable */
  }

  // Live collector health comes from /live/status, whose shape is per-section
  // counts + ages (not the legacy stream_health counters).
  let stream_health: Ops["stream_health"] = [];
  const status = await tryLiveApi<LiveStatusResponse>("/live/status", 5000);
  if (status?.sections) {
    const maxAge = status.maxAgeSeconds ?? 120;
    stream_health = Object.entries(status.sections).map(([source, records]) => {
      const age = status.ageSeconds?.[source];
      return {
        source,
        is_healthy: age === null || age === undefined ? false : age <= maxAge,
        total_records: records,
        last_success: status.updatedAt?.[source] ?? null,
        age_seconds: age ?? null,
      };
    });
  }
  return { stream_health, pipeline, quality };
}

export interface BatchFreshness {
  /** Newest batch record across the warehouse, ISO string or null when empty. */
  asOf: string | null;
  totalFlights: number;
  hasFlights: boolean;
}

/** When the Gold layer was last refreshed + whether flight history exists. */
export async function fetchFreshness(): Promise<BatchFreshness> {
  const [asOfFlights, asOfPositions, asOfWeather, totalFlights] = await Promise.all([
    scalar<string | null>("SELECT MAX(collected_at) FROM main.fact_flights", null),
    scalar<string | null>("SELECT MAX(collected_at) FROM main.fact_positions", null),
    scalar<string | null>("SELECT MAX(collected_at) FROM main.weather", null),
    scalar<number>("SELECT COUNT(*) FROM main.fact_flights", 0),
  ]);
  const candidates = [asOfFlights, asOfPositions, asOfWeather].filter(
    (v): v is string => typeof v === "string" && v.length > 0
  );
  candidates.sort();
  return {
    asOf: candidates.length ? candidates[candidates.length - 1] : null,
    totalFlights,
    hasFlights: totalFlights > 0,
  };
}

export async function fetchManifest(): Promise<BundleManifest> {
  const [airportCount, flightCount, positionCount] = await Promise.all([
    scalar<number>("SELECT COUNT(*) FROM main.dim_airport", 0).catch(() => 0),
    scalar<number>("SELECT COUNT(*) FROM main.fact_flights", 0).catch(() => 0),
    scalar<number>("SELECT COUNT(*) FROM main.fact_positions", 0).catch(() => 0),
  ]);
  return {
    version: 3,
    generated_at: new Date().toISOString(),
    counts: { airports: airportCount, flights: flightCount, positions: positionCount },
    files: [
      "airports.json",
      "positions.json",
      "metars.json",
      "weather.json",
      "notams.json",
      "kpis.json",
      "analytics.json",
      "stories.json",
      "ops.json",
      "manifest.json",
    ],
    sources: { runtime: "motherduck+vps", version: 3 },
  };
}

/** Live files resolve from the VPS snapshot (no bundle, no API). */
export async function fetchLivePositions(): Promise<Position[]> {
  const snap = await tryLiveApi<LiveSnapshot>("/live/snapshot", 9000);
  if (!snap) throw new Error("live snapshot unavailable");
  return snap.positions.map((p) => ({
    icao24: p.icao24,
    callsign: p.callsign ?? null,
    lat: p.latitude,
    lon: p.longitude,
    alt: p.altitude ?? null,
    velocity: p.velocity ?? null,
    heading: p.heading ?? null,
    vertical_rate: p.vertical_rate ?? null,
    source: p.source ?? null,
    updated_at: p.collected_at ?? null,
  }));
}

export async function fetchLiveMetars(): Promise<Metar[]> {
  const snap = await tryLiveApi<LiveSnapshot>("/live/snapshot", 9000);
  if (!snap) throw new Error("live snapshot unavailable");
  return snap.weather.metar.map((m) => {
    const vis = typeof m.visibility === "number" ? m.visibility : parseFloat(str(m.visibility, ""));
    return {
      icao: m.icao,
      raw_text: m.rawOb ?? null,
      temperature: m.temp ?? null,
      wind_speed: m.windSpeedKt ?? null,
      wind_direction: m.windDir ?? null,
      visibility: Number.isFinite(vis) ? vis : null,
      cloud_cover: m.cover ?? null,
      fetched_at: m.obsTime ?? null,
    };
  });
}

export async function fetchLiveWeather(): Promise<WeatherStation[]> {
  const snap = await tryLiveApi<LiveSnapshot>("/live/snapshot", 9000);
  if (!snap) throw new Error("live snapshot unavailable");
  return snap.weather.forecast ?? [];
}

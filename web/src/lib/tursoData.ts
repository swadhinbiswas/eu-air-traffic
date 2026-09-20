/**
 * Turso-backed dashboard data — the runtime replacement for the static bundle.
 * Every function returns the JSON shape the pages already expect.
 *
 * Sources:
 *  - analytics / kpis / airports / catalog / stories / ops / manifest → Turso
 *  - live positions / weather → VPS /live/snapshot
 *
 * SQL is SQLite (no schema prefixes, sqlite_master instead of information_schema).
 */
import { tursoBatch, tursoQuery } from "./turso";
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
const str = (v: unknown, fallback = ""): string =>
  v === null || v === undefined ? fallback : String(v);
const num = (v: unknown, fallback = 0): number => {
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : fallback;
};

export interface BatchFreshness {
  asOf: string | null;
  totalFlights: number;
  hasFlights: boolean;
}

async function rows<T = Row>(sql: string): Promise<T[]> {
  const { rows: out } = await tursoQuery(sql);
  return out as T[];
}

async function scalar<T>(sql: string, fallback: T): Promise<T> {
  try {
    const { rows: out } = await tursoQuery(sql);
    if (!out.length) return fallback;
    const v = Object.values(out[0])[0];
    return (v ?? fallback) as T;
  } catch {
    return fallback;
  }
}

/**
 * Read one precomputed payload from Turso's `site_summary` table.
 *
 * The publisher computes the dashboard aggregates from the warehouse and
 * writes them as single rows, so a poll costs one row instead of scanning the
 * fact tables. Returns null when the table is missing (e.g. before the first
 * publish after a deploy), which makes callers fall back to direct queries.
 */
async function summaryPayload<T>(key: string): Promise<T | null> {
  try {
    const { rows: out } = await tursoQuery(
      "SELECT payload_json FROM site_summary WHERE key = ? LIMIT 1",
      [key]
    );
    const raw = out[0]?.payload_json;
    if (typeof raw !== "string" || !raw) return null;
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

interface KpisPayload {
  total_flights?: number;
  avg_delay_minutes?: number;
  cancellation_rate?: number;
  airports?: number;
  airlines?: number;
}

interface FreshnessPayload {
  as_of?: string | null;
  total_flights?: number;
  has_flights?: boolean;
}

interface ManifestPayload {
  airports?: number;
  flights?: number;
  positions?: number;
}

/** When the Gold layer was last refreshed + whether flight history exists. */
export async function fetchFreshness(): Promise<BatchFreshness> {
  const payload = await summaryPayload<FreshnessPayload>("freshness");
  if (payload) {
    const totalFlights = num(payload.total_flights);
    return {
      asOf: typeof payload.as_of === "string" && payload.as_of ? payload.as_of : null,
      totalFlights,
      hasFlights: payload.has_flights ?? totalFlights > 0,
    };
  }

  // Fallback until the next publish writes site_summary.
  const [asOfFlights, asOfPositions, asOfWeather, totalFlights] = await Promise.all([
    scalar<string | null>("SELECT MAX(collected_at) FROM fact_flights", null),
    scalar<string | null>("SELECT MAX(collected_at) FROM fact_positions", null),
    scalar<string | null>("SELECT MAX(timestamp) FROM weather", null),
    scalar<number>("SELECT COUNT(*) FROM fact_flights", 0),
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
  ] = await Promise.all([
    rows("SELECT * FROM gold_airport_metrics"),
    rows("SELECT * FROM gold_airline_rankings"),
    rows("SELECT * FROM gold_delay_analysis"),
    rows("SELECT * FROM gold_weather_impact"),
    rows("SELECT * FROM gold_seasonal_trends"),
    rows("SELECT * FROM gold_fuel_price_series"),
    // Route aggregates are precomputed in Gold. The old query joined dim_route
    // to fact_flights here, which read every flight in the serving copy on
    // each poll; gold_route_performance costs a fraction of those rows.
    rows(
      "SELECT origin, destination, airline, total_flights, avg_delay_minutes, " +
        "avg_distance_km AS distance_km, on_time_rate " +
        "FROM gold_route_performance " +
        "ORDER BY total_flights DESC, distance_km DESC LIMIT 2000"
    ),
    rows("SELECT type_icao, manufacturer, family, engine, capacity, range_km FROM dim_aircraft ORDER BY capacity DESC"),
    rows("SELECT aircraft_type, fuel_burn_liters_per_hour, co2_kg_per_hour FROM fact_emissions ORDER BY co2_kg_per_hour DESC"),
    // Gold mart: the same grouping the client used to compute over fact_notams.
    rows("SELECT icao_location, notam_count, notam_type FROM gold_notam_summary ORDER BY notam_count DESC"),
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
    // Status mix is already one row per status in gold_delay_analysis.
    status_mix: gold_delay_analysis,
  } as unknown as Analytics;
}

export async function fetchKpis(): Promise<Kpis> {
  const payload = await summaryPayload<KpisPayload>("kpis");
  if (payload) {
    return {
      total_flights: num(payload.total_flights),
      avg_delay_minutes: num(payload.avg_delay_minutes),
      cancellation_rate: num(payload.cancellation_rate),
      airports: num(payload.airports),
      airlines: num(payload.airlines),
      live_aircraft: 0,
      metars: 0,
    };
  }

  // Fallback until the next publish writes site_summary: direct aggregates.
  const [total_flights, avg_delay_minutes, cancelled, airlines, airports] = await Promise.all([
    scalar<number>("SELECT COUNT(*) FROM fact_flights", 0),
    scalar<number>("SELECT AVG(delay_minutes) FROM fact_flights WHERE status != 'cancelled'", 0),
    scalar<number>("SELECT COUNT(*) FROM fact_flights WHERE status = 'cancelled'", 0),
    scalar<number>("SELECT COUNT(*) FROM dim_airline", 0),
    scalar<number>("SELECT COUNT(*) FROM dim_airport", 0),
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
  const result = await rows(
    "SELECT a.airport_icao AS icao, a.iata_code AS iata, a.name, a.municipality AS city, " +
      "a.iso_country AS country, a.latitude_deg AS lat, a.longitude_deg AS lon, " +
      "a.elevation_ft, a.type, a.score, COALESCE(m.total_flights, 0) AS total_flights, " +
      "m.avg_delay_minutes, m.on_time_rate " +
      "FROM dim_airport a LEFT JOIN gold_airport_metrics m " +
      "ON m.airport_icao = a.airport_icao ORDER BY a.airport_icao"
  );
  return result.map((r) => ({
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
    score: r.score === null || r.score === undefined ? null : num(r.score),
  }));
}

function layerFor(name: string): string {
  if (name.startsWith("gold_")) return "gold";
  if (name.startsWith("fact_")) return "fact";
  if (name.startsWith("dim_")) return "dim";
  return "raw";
}

export interface OfficialTrafficRow {
  airport_icao: string;
  airport_name: string | null;
  passengers_12m: number | null;
  passengers_ytd: number | null;
  latest_month: string | null;
  official_rank: number;
  observed_flights: number | null;
  observed_rank: number | null;
}

/**
 * Eurostat's official monthly passengers (12-month and YTD) joined to our own
 * counted movements, so the dashboard can cross-check itself against official
 * statistics instead of ranking airports on its own data alone.
 */
export async function fetchOfficialTraffic(): Promise<OfficialTrafficRow[]> {
  return rows<OfficialTrafficRow>(`
    WITH ours AS (
      SELECT airport_icao, total_flights,
             ROW_NUMBER() OVER (ORDER BY total_flights DESC) AS observed_rank
      FROM gold_airport_metrics
    )
    SELECT o.airport_icao, o.airport_name, o.passengers_12m, o.passengers_ytd,
           o.latest_month, o.official_rank,
           m.total_flights AS observed_flights, m.observed_rank
    FROM gold_airport_official_traffic o
    LEFT JOIN ours m ON o.airport_icao = m.airport_icao
    ORDER BY o.official_rank
    LIMIT 100
  `);
}

export async function fetchCatalog(): Promise<Catalog> {
  const tableRows = await rows<{ name: string; type: string }>(
    "SELECT name, type FROM sqlite_master WHERE type IN ('table','view') " +
      "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '\\_%' ESCAPE '\\' ORDER BY name"
  );
  const names = tableRows.map((t) => str(t.name)).filter(Boolean);

  // Row counts come from the publisher's precomputed summary: counting every
  // table from the browser read each fact table in full on every catalog view.
  const summaryCounts = await summaryPayload<Record<string, number>>("catalog");
  let counts: Record<string, number> = summaryCounts ?? {};
  // Column metadata is a metadata-only round trip, so it stays live.
  let columnSets: Record<string, Array<{ name: string; type: string; notnull: number }>> = {};
  try {
    const columns = await tursoBatch(names.map((n) => `PRAGMA table_info("${n.replace(/"/g, "")}")`));
    columnSets = Object.fromEntries(
      names.map((n, i) => [
        n,
        columns[i].rows.map((c) => ({
          name: str(c.name),
          type: str(c.type),
          notnull: num(c.notnull),
        })),
      ])
    );
    if (!summaryCounts) {
      // Fallback until the next publish writes site_summary.
      const rowCounts = await tursoBatch(
        names.map((n) => `SELECT COUNT(*) AS n FROM "${n.replace(/"/g, "")}"`)
      );
      counts = Object.fromEntries(
        names.map((n, i) => [n, num(Object.values(rowCounts[i].rows[0] ?? {})[0])])
      );
    }
  } catch {
    /* metadata is best-effort; tables still render */
  }

  const tables: CatalogTable[] = names.map((name) => ({
    schema: "main",
    name,
    kind: tableRows.find((t) => t.name === name)?.type === "view" ? "VIEW" : "BASE TABLE",
    layer: layerFor(name),
    rows: counts[name] ?? 0,
    columns: (columnSets[name] ?? []).map((c) => ({
      name: c.name,
      type: c.type,
      nullable: c.notnull === 0,
    })),
  }));

  let lineage: Catalog["lineage"] = { nodes: [], edges: [], sources: [] };
  try {
    const { rows: lrows } = await tursoQuery(
      "SELECT payload_json FROM site_lineage WHERE key = 'lineage' LIMIT 1"
    );
    const raw = lrows[0]?.payload_json;
    if (typeof raw === "string") lineage = { ...lineage, ...(JSON.parse(raw) as Catalog["lineage"]) };
  } catch {
    /* lineage unavailable — tables still render */
  }
  return { tables, lineage };
}

export async function fetchStories(): Promise<Story[]> {
  const { rows: out } = await tursoQuery(
    "SELECT id, category, tone, title, metric, unit, narrative, chart_json " +
      "FROM site_stories ORDER BY id"
  );
  return out.map((r) => ({
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
    const { rows: out } = await tursoQuery(
      "SELECT payload_json FROM site_ops WHERE key = 'latest' LIMIT 1"
    );
    const raw = out[0]?.payload_json;
    if (typeof raw === "string") {
      const parsed = JSON.parse(raw) as { pipeline?: Ops["pipeline"]; quality?: Ops["quality"] };
      pipeline = parsed.pipeline ?? null;
      quality = parsed.quality ?? null;
    }
  } catch {
    /* ops reports unavailable */
  }

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

interface LiveStatusResponse {
  status: string;
  sections: Record<string, number>;
  updatedAt: Record<string, string>;
  ageSeconds: Record<string, number | null>;
  maxAgeSeconds: number;
}

export async function fetchManifest(): Promise<BundleManifest> {
  const payload = await summaryPayload<ManifestPayload>("manifest");
  const counts = payload
    ? {
        airports: num(payload.airports),
        flights: num(payload.flights),
        positions: num(payload.positions),
      }
    : await (async () => {
        // Fallback until the next publish writes site_summary.
        const [airportCount, flightCount, positionCount] = await Promise.all([
          scalar<number>("SELECT COUNT(*) FROM dim_airport", 0),
          scalar<number>("SELECT COUNT(*) FROM fact_flights", 0),
          scalar<number>("SELECT COUNT(*) FROM fact_positions", 0),
        ]);
        return { airports: airportCount, flights: flightCount, positions: positionCount };
      })();
  return {
    version: 4,
    generated_at: new Date().toISOString(),
    counts,
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
    sources: { runtime: "turso+vps", version: 4 },
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
    // Snapshot METAR rows are snake_case; visibility is metres.
    const vis = m.visibility_m !== null ? m.visibility_m / 1000 : parseFloat(str(m.visibility_raw, ""));
    return {
      icao: m.station_icao,
      raw_text: m.raw_metar ?? null,
      temperature: m.temperature_c ?? null,
      wind_speed: m.wind_speed_kt ?? null,
      wind_direction: m.wind_dir_deg ?? null,
      visibility: Number.isFinite(vis) ? vis : null,
      cloud_cover: m.condition ?? null,
      fetched_at: m.timestamp ?? null,
    };
  });
}

export async function fetchLiveWeather(): Promise<WeatherStation[]> {
  const snap = await tryLiveApi<LiveSnapshot>("/live/snapshot", 9000);
  if (!snap) throw new Error("live snapshot unavailable");
  return snap.weather.forecast ?? [];
}

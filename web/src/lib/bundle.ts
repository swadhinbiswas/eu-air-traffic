/**
 * Runtime data loader.
 *
 * The dashboard fetches its data live from the API (warehouse analytics on
 * MotherDuck + the collector's live feed) — there is no build-time static
 * bundle. `loadBundle(file)` resolves the same JSON shapes the pages expect,
 * preferring the warehouse API and falling back to the collector's live API.
 */

// ── types ───────────────────────────────────────────────────────────────────
export interface Airport {
  icao: string;
  iata: string | null;
  name: string;
  city: string | null;
  country: string | null;
  lat: number;
  lon: number;
  elevation_ft: number | null;
  type: string;
  total_flights: number;
  avg_delay_minutes: number | null;
  on_time_rate: number | null;
  /** Capability score from the reference data — used when there is no traffic yet. */
  score?: number | null;
}

export interface Position {
  icao24: string;
  callsign: string | null;
  lat: number;
  lon: number;
  alt: number | null;
  velocity: number | null;
  heading: number | null;
  vertical_rate: number | null;
  source: string | null;
  updated_at: string | null;
}

export interface Metar {
  icao: string;
  raw_text: string | null;
  temperature: number | null;
  wind_speed: number | null;
  wind_direction: number | null;
  visibility: number | null;
  cloud_cover: string | null;
  fetched_at: string | null;
}

export interface WeatherHour {
  time: string;
  temperature_c: number | null;
  wind_speed_ms: number | null;
  wind_direction_deg: number | null;
  precipitation_mm: number | null;
  condition: string;
  weather_code: number | null;
}

export interface WeatherStation {
  icao: string;
  name: string;
  country: string;
  lat: number;
  lon: number;
  temperature_c: number | null;
  wind_speed_ms: number | null;
  wind_direction_deg: number | null;
  precipitation_mm: number | null;
  humidity_pct: number | null;
  condition: string;
  weather_code: number | null;
  time: string | null;
  hourly: WeatherHour[];
}

export interface RouteDatum {
  origin: string;
  destination: string;
  total_flights: number;
  avg_delay_minutes: number;
  distance_km: number;
}

// ── live gateway types ──────────────────────────────────────────────────────
export interface LiveAircraft {
  hex: string;
  callsign: string;
  reg: string | null;
  type: string | null;
  lat: number;
  lon: number;
  alt: number | null;
  altGeom: number | null;
  gs: number | null;
  track: number | null;
  magHeading: number | null;
  baroRate: number | null;
  geomRate: number | null;
  ias: number | null;
  tas: number | null;
  mach: number | null;
  roll: number | null;
  oat: number | null;
  tat: number | null;
  windDir: number | null;
  windSpeed: number | null;
  squawk: string | null;
  emergency: string | null;
  category: string | null;
  navAlt: number | null;
  navQnh: number | null;
  seen: number | null;
  seenPos: number | null;
  mlat: boolean;
  tisb: boolean;
}

export interface LivePositionsResponse {
  source: string;
  generatedAt: string;
  count: number;
  aircraft: LiveAircraft[];
  relayedAt?: string;
  stale?: boolean;
}

export type FlightCategory = "VFR" | "MVFR" | "IFR" | "LIFR";

export interface MetarStation {
  icao: string;
  name: string;
  lat: number;
  lon: number;
  temp: number | null;
  dewp: number | null;
  windDir: number | null;
  windSpeedKt: number | null;
  gustKt: number | null;
  visibility: string | null;
  altimeter: number | null;
  flightCategory: FlightCategory | null;
  cover: string | null;
  clouds: Array<{ cover?: string; base?: number }>;
  rawOb: string | null;
  obsTime: string | null;
}

/** METAR row as the collector stores it (snake_case) in the live snapshot. */
export interface SnapshotMetar {
  station_icao: string;
  name: string | null;
  latitude: number | null;
  longitude: number | null;
  temperature_c: number | null;
  dewpoint_c: number | null;
  wind_dir_deg: number | null;
  wind_speed_kt: number | null;
  wind_speed_ms: number | null;
  gust_kt: number | null;
  visibility_m: number | null;
  visibility_raw: string | null;
  pressure_hpa: number | null;
  flight_category: string | null;
  condition: string | null;
  raw_metar: string | null;
  timestamp: string | null;
  humidity_pct: number | null;
}

export interface MetarResponse {
  source: string;
  generatedAt: string;
  count: number;
  stations: MetarStation[];
}

export interface TafRecord {
  icao: string;
  issueTime: string | null;
  validFrom: number | null;
  validTo: number | null;
  rawTAF: string | null;
}

export interface AircraftEnrichment {
  icao24: string;
  found: boolean;
  registration?: string | null;
  manufacturer?: string | null;
  typeCode?: string | null;
  type?: string | null;
  operator?: string | null;
  operatorFlag?: string | null;
}

export interface Kpis {
  total_flights: number;
  avg_delay_minutes: number;
  cancellation_rate: number;
  airports: number;
  airlines: number;
  live_aircraft: number;
  metars: number;
}

export interface StoryChart {
  type: string;
  x?: string;
  y?: string;
  data: Array<Record<string, string | number>>;
}

export interface Story {
  id: string;
  category: string;
  tone: "info" | "warning" | "critical" | "positive";
  title: string;
  metric: string;
  unit: string;
  narrative: string;
  chart: StoryChart;
}

export interface CatalogColumn {
  name: string;
  type: string;
  nullable: boolean;
}

export interface CatalogTable {
  schema: string;
  name: string;
  kind: string;
  layer: string;
  rows: number;
  columns: CatalogColumn[];
}

export interface LineageNode {
  id: string;
  layer: string;
  materialized: string;
  description: string;
  path: string;
  depends_on: string[];
}

export interface LineageEdge {
  from: string;
  to: string;
}

export interface Catalog {
  tables: CatalogTable[];
  lineage: {
    nodes: LineageNode[];
    edges: LineageEdge[];
    sources: Array<{ id: string; name: string; source: string; description: string }>;
  };
}

export interface Analytics {
  gold_airport_metrics: Array<Record<string, number | string>>;
  gold_airline_rankings: Array<Record<string, number | string>>;
  gold_delay_analysis: Array<Record<string, number | string>>;
  gold_weather_impact: Array<Record<string, number | string>>;
  gold_seasonal_trends: Array<Record<string, number | string>>;
  gold_fuel_price_series: Array<Record<string, number | string>>;
  routes: Array<Record<string, number | string>>;
  fleet: Array<Record<string, number | string>>;
  emissions: Array<Record<string, number | string>>;
  notam_summary: Array<Record<string, number | string>>;
  status_mix: Array<Record<string, number | string>>;
  catalog?: Catalog;
}

export interface Ops {
  stream_health: Array<Record<string, string | number | boolean | null>>;
  pipeline?: Record<string, unknown> | null;
  quality?: Record<string, unknown> | null;
}

export interface BundleManifest {
  version: number;
  generated_at: string;
  counts: Record<string, number>;
  files: string[];
  sources: Record<string, unknown>;
}

// ── loaders ─────────────────────────────────────────────────────────────────
// Warehouse/analytics API (Turso-backed) and the live API served by the VPS
// collector. Both fall back to the deployed host so a missing build variable
// can never take the site down. Override with VITE_API_URL / VITE_LIVE_URL.
// .trim() because a stray trailing space in a build variable produces a URL
// that silently fails every request.
const FALLBACK_API = "https://vps.swadhin.cv";
const API = (import.meta.env.VITE_API_URL as string | undefined)?.trim() || FALLBACK_API;
const LIVE = (import.meta.env.VITE_LIVE_URL as string | undefined)?.trim() || FALLBACK_API;

// Short-lived client cache so several components on a page share one request
// without pinning stale data (the server caches for longer anyway).
const CACHE_TTL_MS = 15_000;
const cache = new Map<string, { at: number; promise: Promise<unknown> }>();

export async function loadBundle<T>(file: string): Promise<T> {
  const hit = cache.get(file);
  if (hit && Date.now() - hit.at < CACHE_TTL_MS) return hit.promise as Promise<T>;

  const promise = (async (): Promise<T> => {
    // Runtime sources: Turso for Gold analytics, the VPS live snapshot for
    // live files. No build-time static bundle exists.
    const md = await import("./tursoData");
    switch (file) {
      case "analytics.json":
        return (await md.fetchAnalytics()) as T;
      case "kpis.json":
        return (await md.fetchKpis()) as T;
      case "airports.json":
        return (await md.fetchAirports()) as T;
      case "stories.json":
        return (await md.fetchStories()) as T;
      case "ops.json":
        return (await md.fetchOps()) as T;
      case "manifest.json":
        return (await md.fetchManifest()) as T;
      case "freshness.json":
        return (await md.fetchFreshness()) as T;
      case "positions.json":
        return (await md.fetchLivePositions()) as T;
      case "metars.json":
        return (await md.fetchLiveMetars()) as T;
      case "weather.json":
        return (await md.fetchLiveWeather()) as T;
      case "notams.json":
        return ([] as unknown) as T;
      default:
        throw new Error(`Unknown bundle file: ${file}`);
    }
  })();

  cache.set(file, { at: Date.now(), promise });
  promise.catch(() => cache.delete(file));
  return promise as Promise<T>;
}

async function fetchWithTimeout<T>(url: string, timeoutMs: number): Promise<T | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    // Live and analytics endpoints must never be served from the HTTP cache.
    const res = await fetch(url, { signal: controller.signal, cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

/** Attempt a warehouse API call; resolve `null` on any failure/timeout. */
export function tryLive<T>(path: string, timeoutMs = 2500): Promise<T | null> {
  if (!API) return Promise.resolve(null);
  return fetchWithTimeout<T>(`${API.replace(/\/$/, "")}${path}`, timeoutMs);
}

/** Call the live API (VPS collector). Returns null when unconfigured/unreachable. */
export function tryLiveApi<T>(path: string, timeoutMs = 9000): Promise<T | null> {
  if (!LIVE) return Promise.resolve(null);
  return fetchWithTimeout<T>(`${LIVE.replace(/\/$/, "")}${path}`, timeoutMs);
}

/** @deprecated use {@link tryLiveApi} — the Worker gateway was replaced by the VPS. */
export const tryWorker = tryLiveApi;

export function liveGatewayConfigured(): boolean {
  return Boolean(LIVE);
}

export function isLive(): boolean {
  return Boolean(API) || Boolean(LIVE);
}

// ── live snapshot (single endpoint served by the VPS collector) ──────────────
export interface CanonicalAircraft {
  icao24: string;
  callsign?: string | null;
  registration?: string | null;
  aircraft_type?: string | null;
  latitude: number;
  longitude: number;
  altitude?: number | null;
  velocity?: number | null;
  heading?: number | null;
  vertical_rate?: number | null;
  mach?: number | null;
  ias?: number | null;
  tas?: number | null;
  oat?: number | null;
  tat?: number | null;
  wind_dir?: number | null;
  wind_speed?: number | null;
  nav_qnh?: number | null;
  nav_altitude?: number | null;
  nav_heading?: number | null;
  true_heading?: number | null;
  mag_heading?: number | null;
  roll?: number | null;
  geom_rate?: number | null;
  route?: string | null;
  route_source?: string | null;
  route_origin?: string | null;
  route_destination?: string | null;
  nic?: string | null;
  nac_p?: string | null;
  nac_v?: string | null;
  sil?: string | null;
  rc?: number | null;
  db_flags?: number | null;
  squawk?: string | null;
  emergency?: string | null;
  category?: string | null;
  aircraft_class?: string | null;
  emitter_class?: string | null;
  is_cargo?: boolean;
  is_military?: boolean;
  operator_name?: string | null;
  operator_country?: string | null;
  operator_category?: string | null;
  type_name?: string | null;
  manufacturer?: string | null;
  airframe?: string | null;
  wake_category?: string | null;
  co2_kg_per_hour?: number | null;
  fuel_burn_kg_per_hour?: number | null;
  co2_estimated?: boolean;
  source?: string | null;
  collected_at?: string | null;
  on_ground?: boolean;
}

export interface LiveSnapshot {
  generatedAt: string;
  counts: Record<string, number>;
  positions: CanonicalAircraft[];
  flights: Array<Record<string, unknown>>;
  weather: {
    metar: SnapshotMetar[];
    taf: Array<Record<string, unknown>>;
    forecast: WeatherStation[];
  };
  fuel: Array<Record<string, unknown>>;
  reference: {
    airports: Array<Record<string, unknown>>;
    routes: Array<Record<string, unknown>>;
    aircraft: Array<Record<string, unknown>>;
    emission_factors: Array<Record<string, unknown>>;
    holidays: Array<Record<string, unknown>>;
  };
  emissions: {
    total_co2_kg_per_hour: number;
    total_co2_tonnes_per_hour: number;
    by_type: Array<{ aircraft_type: string; aircraft: number; co2_kg_per_hour: number }>;
  };
  airspace: {
    total: number;
    by_class: Record<string, number>;
    by_emitter: Record<string, number>;
    military: number;
    cargo: number;
    helicopter: number;
    passenger: number;
    private: number;
    total_co2_kg_per_hour: number;
  };
}

export { API as API_BASE, LIVE as LIVE_BASE };

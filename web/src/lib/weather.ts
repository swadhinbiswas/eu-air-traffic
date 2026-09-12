import type {
  FlightCategory,
  MetarStation,
  SnapshotMetar,
  WeatherHour,
  WeatherStation,
} from "./bundle";

const MS_TO_KT = 1.94384;

/** Unified weather-station record for the map and panels. */
export interface WeatherStationView {
  icao: string;
  name: string;
  lat: number;
  lon: number;
  tempC: number | null;
  dewpointC: number | null;
  windDirDeg: number | null;
  windKt: number | null;
  gustKt: number | null;
  visibility: string | null;
  altimeter: number | null;
  category: FlightCategory | null;
  condition: string | null;
  cover: string | null;
  clouds: Array<{ cover?: string; base?: number }>;
  raw: string | null;
  obsTime: string | null;
  source: "metar" | "open-meteo";
  hourly: WeatherHour[];
}

export const CATEGORY_COLORS: Record<FlightCategory, string> = {
  VFR: "#34d399",
  MVFR: "#38bdf8",
  IFR: "#f87171",
  LIFR: "#c084fc",
};

export const CATEGORY_LABEL: Record<FlightCategory, string> = {
  VFR: "VFR · visual",
  MVFR: "MVFR · marginal",
  IFR: "IFR · instrument",
  LIFR: "LIFR · low IFR",
};

export function fromMetar(m: MetarStation): WeatherStationView {
  return {
    icao: m.icao,
    name: m.name,
    lat: m.lat,
    lon: m.lon,
    tempC: m.temp,
    dewpointC: m.dewp,
    windDirDeg: m.windDir,
    windKt: m.windSpeedKt,
    gustKt: m.gustKt,
    visibility: m.visibility,
    altimeter: m.altimeter,
    category: m.flightCategory,
    condition: m.cover,
    cover: m.cover,
    clouds: m.clouds ?? [],
    raw: m.rawOb,
    obsTime: m.obsTime,
    source: "metar",
    hourly: [],
  };
}

/** Adapter for the collector's snapshot rows, which are snake_case. */
export function fromSnapshotMetar(m: SnapshotMetar): WeatherStationView {
  const visibility =
    m.visibility_m !== null
      ? `${(m.visibility_m / 1000).toFixed(1)} km`
      : (m.visibility_raw ?? null);
  return {
    icao: m.station_icao,
    name: m.name ?? m.station_icao,
    lat: m.latitude ?? 0,
    lon: m.longitude ?? 0,
    tempC: m.temperature_c,
    dewpointC: m.dewpoint_c,
    windDirDeg: m.wind_dir_deg,
    windKt: m.wind_speed_kt,
    gustKt: m.gust_kt,
    visibility,
    altimeter: m.pressure_hpa,
    category: (m.flight_category as FlightCategory | null) ?? null,
    condition: m.condition,
    cover: m.condition,
    clouds: [],
    raw: m.raw_metar,
    obsTime: m.timestamp,
    source: "metar",
    hourly: [],
  };
}

export function fromOpenMeteo(s: WeatherStation): WeatherStationView {
  return {
    icao: s.icao,
    name: s.name,
    lat: s.lat,
    lon: s.lon,
    tempC: s.temperature_c,
    dewpointC: null,
    windDirDeg: s.wind_direction_deg,
    windKt: s.wind_speed_ms !== null ? s.wind_speed_ms * MS_TO_KT : null,
    gustKt: null,
    visibility: null,
    altimeter: null,
    category: null,
    condition: s.condition,
    cover: s.condition,
    clouds: [],
    raw: null,
    obsTime: s.time,
    source: "open-meteo",
    hourly: s.hourly ?? [],
  };
}

/** Temperature → colour ramp shared by layers and panels. */
export function tempColor(t: number | null | undefined): string {
  if (t === null || t === undefined) return "#a1a1aa";
  if (t <= -5) return "#3b82f6";
  if (t <= 2) return "#60a5fa";
  if (t <= 10) return "#22d3ee";
  if (t <= 18) return "#34d399";
  if (t <= 26) return "#facc15";
  if (t <= 33) return "#fb923c";
  return "#f87171";
}

export function weatherGlyph(code: number | null | undefined): string {
  if (code === null || code === undefined) return "·";
  if (code === 0) return "☀";
  if (code <= 2) return "⛅";
  if (code === 3) return "☁";
  if (code <= 48) return "🌫";
  if (code <= 57) return "🌦";
  if (code <= 67) return "🌧";
  if (code <= 77) return "❄";
  if (code <= 82) return "🌦";
  if (code <= 86) return "🌨";
  return "⛈";
}

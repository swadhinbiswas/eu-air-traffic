import type { CanonicalAircraft, LiveAircraft, Position } from "./bundle";

/** Unified aircraft record used by the map, panels and analysis. */
export interface Aircraft {
  hex: string;
  callsign: string;
  reg: string | null;
  type: string | null;
  lat: number;
  lon: number;
  altFt: number | null;
  gsKt: number | null;
  trackDeg: number | null;
  verticalRateFpm: number | null;
  ias: number | null;
  tas: number | null;
  mach: number | null;
  oat: number | null;
  windDir: number | null;
  windSpeedKt: number | null;
  squawk: string | null;
  emergency: string | null;
  category: string | null;
  aircraftClass: string | null;
  emitterClass: string | null;
  operatorName: string | null;
  operatorCountry: string | null;
  typeName: string | null;
  manufacturer: string | null;
  wakeCategory: string | null;
  co2KgPerHour: number | null;
  fuelBurnKgPerHour: number | null;
  source: string;
  seen: number | null;
}

export type FleetSource = "live" | "api" | "snapshot";

const EMERGENCY_SQUAWKS: Record<string, string> = {
  "7500": "unlawful interference",
  "7600": "radio failure",
  "7700": "general emergency",
};

export function emergencyLabel(a: Aircraft): string | null {
  if (a.squawk && EMERGENCY_SQUAWKS[a.squawk]) return EMERGENCY_SQUAWKS[a.squawk];
  if (a.emergency && a.emergency !== "none" && a.emergency !== "") {
    return a.emergency.replace(/_/g, " ");
  }
  return null;
}

export function isEmergency(a: Aircraft): boolean {
  return emergencyLabel(a) !== null;
}

const MS_TO_KT = 1.94384;
const MS_TO_FPM = 196.8504;

/** Worker /live/positions record → Aircraft. */
export function fromLive(l: LiveAircraft): Aircraft {
  return {
    hex: l.hex,
    callsign: (l.callsign || "").trim(),
    reg: l.reg,
    type: l.type,
    lat: l.lat,
    lon: l.lon,
    altFt: l.alt,
    gsKt: l.gs,
    trackDeg: l.track,
    verticalRateFpm: l.baroRate ?? l.geomRate,
    ias: l.ias,
    tas: l.tas,
    mach: l.mach,
    oat: l.oat,
    windDir: l.windDir,
    windSpeedKt: l.windSpeed,
    squawk: l.squawk,
    emergency: l.emergency,
    category: l.category,
    aircraftClass: null,
    emitterClass: null,
    operatorName: null,
    operatorCountry: null,
    typeName: null,
    manufacturer: null,
    wakeCategory: null,
    co2KgPerHour: null,
    fuelBurnKgPerHour: null,
    source: l.mlat ? "mlat" : "adsb",
    seen: l.seen,
  };
}

/** Static bundle snapshot position → Aircraft (velocity in m/s, alt in ft). */
export function fromSnapshot(p: Position): Aircraft {
  return {
    hex: p.icao24,
    callsign: (p.callsign || "").trim(),
    reg: null,
    type: null,
    lat: p.lat,
    lon: p.lon,
    altFt: p.alt,
    gsKt: p.velocity !== null ? p.velocity * MS_TO_KT : null,
    trackDeg: p.heading,
    verticalRateFpm: p.vertical_rate !== null ? p.vertical_rate * MS_TO_FPM : null,
    ias: null,
    tas: null,
    mach: null,
    oat: null,
    windDir: null,
    windSpeedKt: null,
    squawk: null,
    emergency: null,
    category: null,
    aircraftClass: null,
    emitterClass: null,
    operatorName: null,
    operatorCountry: null,
    typeName: null,
    manufacturer: null,
    wakeCategory: null,
    co2KgPerHour: null,
    fuelBurnKgPerHour: null,
    source: p.source ?? "snapshot",
    seen: null,
  };
}

/** FastAPI /api/v1/realtime/positions row → Aircraft. */
export function fromApi(row: Record<string, unknown>): Aircraft {
  const num = (v: unknown): number | null =>
    typeof v === "number" && Number.isFinite(v) ? v : null;
  const heading = num(row.heading);
  return {
    hex: String(row.icao24 ?? "").toUpperCase(),
    callsign: String(row.callsign ?? "").trim(),
    reg: null,
    type: null,
    lat: num(row.latitude) ?? 0,
    lon: num(row.longitude) ?? 0,
    altFt: num(row.altitude),
    gsKt: num(row.velocity) !== null ? (num(row.velocity) as number) * MS_TO_KT : null,
    trackDeg: heading,
    verticalRateFpm:
      num(row.vertical_rate) !== null ? (num(row.vertical_rate) as number) * MS_TO_FPM : null,
    ias: null,
    tas: null,
    mach: null,
    oat: null,
    windDir: null,
    windSpeedKt: null,
    squawk: null,
    emergency: null,
    category: null,
    aircraftClass: null,
    emitterClass: null,
    operatorName: null,
    operatorCountry: null,
    typeName: null,
    manufacturer: null,
    wakeCategory: null,
    co2KgPerHour: null,
    fuelBurnKgPerHour: null,
    source: String(row.source ?? "api"),
    seen: null,
  };
}

/** Live `/live/snapshot` canonical aircraft → Aircraft (values already in kt/fpm). */
export function fromCanonical(c: CanonicalAircraft): Aircraft {
  const num = (v: unknown): number | null =>
    typeof v === "number" && Number.isFinite(v) ? v : null;
  return {
    hex: String(c.icao24 ?? "").toUpperCase(),
    callsign: String(c.callsign ?? "").trim(),
    reg: c.registration ?? null,
    type: c.aircraft_type ?? null,
    lat: c.latitude,
    lon: c.longitude,
    altFt: num(c.altitude),
    gsKt: num(c.velocity),
    trackDeg: num(c.heading),
    verticalRateFpm: num(c.vertical_rate),
    ias: num(c.ias),
    tas: num(c.tas),
    mach: num(c.mach),
    oat: num(c.oat),
    windDir: num(c.wind_dir),
    windSpeedKt: num(c.wind_speed),
    squawk: c.squawk ?? null,
    emergency: c.emergency ?? null,
    category: c.category ?? null,
    aircraftClass: c.aircraft_class ?? null,
    emitterClass: c.emitter_class ?? null,
    operatorName: c.operator_name ?? null,
    operatorCountry: c.operator_country ?? null,
    typeName: c.type_name ?? null,
    manufacturer: c.manufacturer ?? null,
    wakeCategory: c.wake_category ?? null,
    co2KgPerHour: num(c.co2_kg_per_hour),
    fuelBurnKgPerHour: num(c.fuel_burn_kg_per_hour),
    source: c.source ?? "adsb",
    seen: null,
  };
}

/**
 * Dead-reckon an aircraft forward in time using ground speed and track so the
 * map glides between polls instead of jumping every few seconds.
 */
export function advance(a: Aircraft, elapsedSec: number, maxSec = 30): Aircraft {
  if (!a.gsKt || a.trackDeg === null) return a;
  const seconds = Math.min(elapsedSec, maxSec);
  if (seconds <= 0) return a;

  const distanceKm = (a.gsKt * 1.852 / 3600) * seconds;
  const rad = (a.trackDeg * Math.PI) / 180;
  const dNorth = distanceKm * Math.cos(rad);
  const dEast = distanceKm * Math.sin(rad);

  const lat = a.lat + dNorth / 111.32;
  const cosLat = Math.max(0.1, Math.cos((a.lat * Math.PI) / 180));
  const lon = a.lon + dEast / (111.32 * cosLat);

  const minutes = seconds / 60;
  const altFt =
    a.altFt !== null && a.verticalRateFpm !== null
      ? Math.max(0, a.altFt + a.verticalRateFpm * minutes)
      : a.altFt;

  return { ...a, lat, lon, altFt };
}

export function altitudeBand(altFt: number | null): "gnd" | "low" | "mid" | "high" | "upper" {
  if (altFt === null || altFt <= 1000) return "gnd";
  if (altFt < 10000) return "low";
  if (altFt < 25000) return "mid";
  if (altFt < 38000) return "high";
  return "upper";
}

/** Filter helper used by the UI. */
export function matchesQuery(a: Aircraft, query: string): boolean {
  const q = query.trim().toUpperCase();
  if (!q) return false;
  return (
    a.callsign.toUpperCase().includes(q) ||
    a.hex.toUpperCase().includes(q) ||
    (a.reg ?? "").toUpperCase().includes(q) ||
    (a.type ?? "").toUpperCase().includes(q)
  );
}

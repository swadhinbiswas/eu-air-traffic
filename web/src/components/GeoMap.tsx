import { useMemo } from "react";
import { Map as MapView, MapControls, MapMarker, MarkerContent } from "@/components/ui/map";

type Row = Record<string, unknown>;

const COORD_PAIRS: [string, string][] = [
  ["latitude_deg", "longitude_deg"],
  ["lat", "lon"],
  ["latitude", "longitude"],
];

export interface GeoColumns {
  lat: string;
  lon: string;
}

/** Detect a supported latitude/longitude column pair in a dataset. */
export function detectGeoColumns(rows: Row[]): GeoColumns | null {
  if (!rows.length) return null;
  const keys = new Set(Object.keys(rows[0]));
  for (const [lat, lon] of COORD_PAIRS) {
    if (keys.has(lat) && keys.has(lon)) return { lat, lon };
  }
  return null;
}

/** Numeric columns usable as a colour/size metric (excludes coordinates). */
export function numericColumns(rows: Row[], geo: GeoColumns | null): string[] {
  if (!rows.length) return [];
  return Object.keys(rows[0]).filter((k) => {
    if (geo && (k === geo.lat || k === geo.lon)) return false;
    const sample = rows.find((r) => r[k] !== null && r[k] !== undefined);
    return sample ? typeof sample[k] === "number" : false;
  });
}

/** Map a value in [min, max] to an emerald → amber → red scale. */
function colorForValue(value: number, min: number, max: number): string {
  if (max <= min) return "#34d399";
  const t = Math.max(0, Math.min(1, (value - min) / (max - min)));
  const hue = (1 - t) * 155; // 155° emerald → 0° red
  return `hsl(${hue}, 72%, 55%)`;
}

interface GeoMapProps {
  rows: Row[];
  metric?: string | null;
  height?: number;
  maxPoints?: number;
  center?: [number, number];
  zoom?: number;
  labelKey?: string | null;
}

export function GeoMap({
  rows,
  metric,
  height = 460,
  maxPoints = 1500,
  center = [10, 50],
  zoom = 3.2,
  labelKey,
}: GeoMapProps) {
  const geo = useMemo(() => detectGeoColumns(rows), [rows]);

  const { points, mappable } = useMemo(() => {
    if (!geo) return { points: [], mappable: 0 };
    const all: { lat: number; lon: number; value: number | null; label: string }[] = [];
    for (const r of rows) {
      const lat = Number(r[geo.lat]);
      const lon = Number(r[geo.lon]);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
      if (lat === 0 && lon === 0) continue;
      const raw = metric ? r[metric] : null;
      all.push({
        lat,
        lon,
        value: typeof raw === "number" ? raw : null,
        label: labelKey && r[labelKey] !== undefined ? String(r[labelKey]) : "",
      });
    }
    return { points: all.slice(0, maxPoints), mappable: all.length };
  }, [rows, geo, metric, labelKey, maxPoints]);

  const { min, max } = useMemo(() => {
    const values = points.map((p) => p.value).filter((v): v is number => v !== null);
    if (!values.length) return { min: 0, max: 1 };
    return { min: Math.min(...values), max: Math.max(...values) };
  }, [points]);

  if (!geo || !points.length) {
    return (
      <div
        style={{ height }}
        className="grid place-items-center rounded-lg border border-white/10 bg-white/[0.02] text-xs text-zinc-500"
      >
        No mappable coordinate records in this dataset.
      </div>
    );
  }

  const hasMetric = Boolean(metric) && points.some((p) => p.value !== null);

  return (
    <div style={{ height }} className="relative overflow-hidden rounded-lg border border-white/10">
      <MapView theme="dark" center={center} zoom={zoom} className="h-full w-full">
        <MapControls showZoom showCompass />
        {points.map((p, i) => {
          const color =
            hasMetric && p.value !== null ? colorForValue(p.value, min, max) : "#34d399";
          const valueText =
            metric && p.value !== null
              ? `${metric}: ${p.value.toLocaleString(undefined, { maximumFractionDigits: 1 })}`
              : "";
          return (
            <MapMarker key={i} longitude={p.lon} latitude={p.lat}>
              <MarkerContent>
                <span
                  title={[p.label, valueText].filter(Boolean).join(" · ")}
                  className="block rounded-full"
                  style={{
                    width: 7,
                    height: 7,
                    background: color,
                    boxShadow: `0 0 0 1.5px rgba(5,7,13,0.7), 0 0 8px ${color}66`,
                  }}
                />
              </MarkerContent>
            </MapMarker>
          );
        })}
      </MapView>

      <div className="pointer-events-none absolute bottom-2 left-2 rounded-md border border-white/10 bg-black/80 px-2.5 py-1.5 text-[10px] text-zinc-300 backdrop-blur">
        <div className="mono">
          {points.length.toLocaleString()} points
          {mappable > points.length ? ` of ${mappable.toLocaleString()} (capped)` : ""}
        </div>
        {hasMetric && (
          <div className="mt-1 flex items-center gap-2">
            <span className="flex items-center gap-1">
              <span className="h-2 w-2 rounded-full" style={{ background: colorForValue(min, min, max) }} />
              {min.toLocaleString(undefined, { maximumFractionDigits: 1 })}
            </span>
            <span className="h-1 w-8 rounded-full bg-gradient-to-r from-emerald-400 via-amber-400 to-red-400" />
            <span className="flex items-center gap-1">
              <span className="h-2 w-2 rounded-full" style={{ background: colorForValue(max, min, max) }} />
              {max.toLocaleString(undefined, { maximumFractionDigits: 1 })}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

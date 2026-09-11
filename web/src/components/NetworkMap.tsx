import { useMemo } from "react";
import { Map as MapView, MapArc, MapControls, MapMarker, MarkerContent } from "@/components/ui/map";
import type { Airport } from "@/lib/bundle";
import type { RouteDatum } from "@/lib/bundle";

interface NetworkMapProps {
  airports: Airport[];
  routes: RouteDatum[];
  height?: number;
  showRoutes?: boolean;
  maxAirports?: number;
  center?: [number, number];
  zoom?: number;
}

function dotColor(delay: number | null): string {
  if (delay === null) return "#64748b";
  if (delay <= 5) return "#34d399";
  if (delay <= 15) return "#fbbf24";
  return "#f87171";
}

export function NetworkMap({
  airports,
  routes,
  height = 420,
  showRoutes = true,
  maxAirports = 140,
  center = [10, 50],
  zoom = 3.4,
}: NetworkMapProps) {
  const coords = useMemo(
    () => new Map(airports.map((a) => [a.icao, [a.lon, a.lat] as [number, number]])),
    [airports]
  );

  const traffic = useMemo(
    () =>
      airports
        .filter((a) => a.total_flights > 0)
        .sort((a, b) => b.total_flights - a.total_flights)
        .slice(0, maxAirports),
    [airports, maxAirports]
  );
  const maxFlights = useMemo(
    () => traffic.reduce((m, a) => Math.max(m, a.total_flights), 1),
    [traffic]
  );

  const arcs = useMemo(
    () =>
      routes
        .filter((r) => r.total_flights > 0)
        .map((r) => {
          const from = coords.get(r.origin);
          const to = coords.get(r.destination);
          if (!from || !to) return null;
          return { id: `${r.origin}-${r.destination}`, from, to, flights: r.total_flights };
        })
        .filter((a): a is { id: string; from: [number, number]; to: [number, number]; flights: number } => a !== null)
        .sort((a, b) => b.flights - a.flights)
        .slice(0, 120),
    [routes, coords]
  );

  return (
    <div style={{ height }} className="overflow-hidden rounded-lg border border-white/10">
      <MapView theme="dark" center={center} zoom={zoom} className="h-full w-full">
        <MapControls showZoom showCompass />
        {showRoutes && (
          <MapArc
            data={arcs}
            curvature={0.22}
            paint={{
              "line-color": "#22d3ee",
              "line-width": ["interpolate", ["linear"], ["get", "flights"], 0, 0.6, 20, 2.4],
              "line-opacity": 0.35,
              "line-blur": 0.6,
            }}
            hoverPaint={{ "line-color": "#34d399", "line-opacity": 0.9 }}
          />
        )}
        {traffic.map((a) => {
          const size = 6 + (a.total_flights / maxFlights) * 14;
          return (
            <MapMarker key={a.icao} longitude={a.lon} latitude={a.lat}>
              <MarkerContent>
                <span
                  title={`${a.icao} · ${a.total_flights} flights`}
                  className="block rounded-full"
                  style={{
                    width: size,
                    height: size,
                    background: dotColor(a.avg_delay_minutes),
                    boxShadow: `0 0 0 2px rgba(5,7,13,0.7), 0 0 10px ${dotColor(a.avg_delay_minutes)}66`,
                  }}
                />
              </MarkerContent>
            </MapMarker>
          );
        })}
      </MapView>
    </div>
  );
}

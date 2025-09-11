import { useMemo } from "react";
import { MapArc } from "@/components/ui/map";
import type { Airport } from "@/lib/bundle";
import type { RouteDatum } from "@/lib/bundle";

interface RoutesLayerProps {
  routes: RouteDatum[];
  airports: Airport[];
  visible: boolean;
  maxArcs?: number;
}

/** Great-circle-ish route arcs between European airports. */
export function RoutesLayer({ routes, airports, visible, maxArcs = 160 }: RoutesLayerProps) {
  const coords = useMemo(
    () => new Map(airports.map((a) => [a.icao, [a.lon, a.lat] as [number, number]])),
    [airports]
  );

  const arcs = useMemo(
    () =>
      routes
        .filter((r) => r.total_flights > 0)
        .map((r) => {
          const from = coords.get(r.origin);
          const to = coords.get(r.destination);
          if (!from || !to) return null;
          return {
            id: `${r.origin}-${r.destination}`,
            from,
            to,
            flights: r.total_flights,
            delay: r.avg_delay_minutes,
          };
        })
        .filter(
          (
            a
          ): a is {
            id: string;
            from: [number, number];
            to: [number, number];
            flights: number;
            delay: number;
          } => a !== null
        )
        .sort((a, b) => b.flights - a.flights)
        .slice(0, maxArcs),
    [routes, coords, maxArcs]
  );

  if (!visible || !arcs.length) return null;

  return (
    <MapArc
      data={arcs}
      curvature={0.24}
      paint={{
        "line-color": "#22d3ee",
        "line-width": ["interpolate", ["linear"], ["get", "flights"], 0, 0.4, 20, 2.2],
        "line-opacity": 0.28,
        "line-blur": 0.5,
      }}
      hoverPaint={{ "line-color": "#34d399", "line-opacity": 0.85 }}
    />
  );
}

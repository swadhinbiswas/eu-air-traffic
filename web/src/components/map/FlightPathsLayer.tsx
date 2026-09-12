import { useEffect, useRef } from "react";
import { useMap } from "@/components/ui/map";
import type { GeoJSONSource, Map as MapLibreMap } from "maplibre-gl";
import type { Airport } from "@/lib/bundle";
import type { Aircraft } from "@/lib/fleet";

const SOURCE_ID = "flight-paths-src";
const PATH_LAYER = "flight-paths";
const SELECTED_LAYER = "flight-path-selected";
const MIN_PUSH_MS = 1500;
// Lines for the whole fleet would dwarf the aircraft themselves; the selected
// flight is always drawn, plus this many in-view paths.
const MAX_PATHS = 250;
const CULL_MIN_ZOOM = 4;

interface FlightPathsLayerProps {
  aircraft: Aircraft[];
  airports: Airport[];
  visible: boolean;
  selectedHex?: string | null;
}

const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
const EMPTY_FILTER = ["==", ["get", "hex"], "__none__"] as never;

interface PathProps {
  hex: string;
  selected: boolean;
  destination: string;
}

/** Straight lines from each aircraft to the airport it is heading for. */
function toGeoJson(
  map: MapLibreMap,
  aircraft: Aircraft[],
  coords: Map<string, Airport>,
  selectedHex: string | null | undefined
): GeoJSON.FeatureCollection {
  const features: GeoJSON.Feature[] = [];
  let drawn = 0;

  const zoomedIn = map.getZoom() >= CULL_MIN_ZOOM;
  const bounds = zoomedIn ? map.getBounds() : null;
  const pad = 1;

  for (const plane of aircraft) {
    const selected = selectedHex !== null && plane.hex === selectedHex;
    if (!selected && drawn >= MAX_PATHS) continue;

    const destination = plane.routeDestination;
    if (!destination) continue;
    const target = coords.get(destination);
    if (!target) continue;

    if (!selected && bounds) {
      const onScreen =
        plane.lat >= bounds.getSouth() - pad &&
        plane.lat <= bounds.getNorth() + pad &&
        plane.lon >= bounds.getWest() - pad &&
        plane.lon <= bounds.getEast() + pad;
      if (!onScreen) continue;
    }

    // Draw the full origin → destination when we know where it came from,
    // otherwise just the remaining leg.
    const origin = plane.routeOrigin ? coords.get(plane.routeOrigin) : undefined;
    const start = origin ? [origin.lon, origin.lat] : [plane.lon, plane.lat];
    const properties: PathProps = { hex: plane.hex, selected, destination };

    features.push({
      type: "Feature",
      geometry: { type: "LineString", coordinates: [start, [target.lon, target.lat]] },
      properties,
    });
    if (!selected) drawn += 1;
  }

  return { type: "FeatureCollection", features };
}

/**
 * Route lines: which airport each aircraft is heading to (the full flown route
 * for the selected aircraft). Lines are few and thin, so the cost is trivial
 * compared with the aircraft symbols.
 */
export function FlightPathsLayer({
  aircraft,
  airports,
  visible,
  selectedHex,
}: FlightPathsLayerProps) {
  const { map, isLoaded } = useMap();
  const aircraftRef = useRef(aircraft);
  const selectedRef = useRef(selectedHex);
  const lastPushRef = useRef(0);
  const pushRef = useRef<(() => void) | null>(null);
  aircraftRef.current = aircraft;
  selectedRef.current = selectedHex ?? null;

  // ICAO → coordinates lookup, rebuilt only when the airport list changes.
  const coordsRef = useRef<Map<string, Airport>>(new Map());
  useEffect(() => {
    const index = new Map<string, Airport>();
    for (const airport of airports) index.set(airport.icao, airport);
    coordsRef.current = index;
  }, [airports]);

  useEffect(() => {
    if (!map || !isLoaded) return;
    let cleanupMoveEnd: (() => void) | null = null;

    const push = () => {
      lastPushRef.current = performance.now();
      const source = map.getSource(SOURCE_ID) as GeoJSONSource | undefined;
      source?.setData(toGeoJson(map, aircraftRef.current, coordsRef.current, selectedRef.current));
    };
    pushRef.current = push;

    if (!map.getSource(SOURCE_ID)) {
      map.addSource(SOURCE_ID, { type: "geojson", data: EMPTY });

      map.addLayer({
        id: PATH_LAYER,
        type: "line",
        source: SOURCE_ID,
        filter: ["!=", ["get", "selected"], true],
        layout: {
          visibility: visible ? "visible" : "none",
          "line-cap": "round",
          "line-join": "round",
        },
        paint: {
          "line-color": "#38bdf8",
          "line-width": ["interpolate", ["linear"], ["zoom"], 3, 0.4, 6, 0.9, 10, 1.6],
          "line-opacity": 0.32,
          "line-dasharray": [2, 3],
        },
      });

      map.addLayer({
        id: SELECTED_LAYER,
        type: "line",
        source: SOURCE_ID,
        filter: EMPTY_FILTER,
        layout: { visibility: visible ? "visible" : "none", "line-cap": "round" },
        paint: {
          "line-color": "#34d399",
          "line-width": ["interpolate", ["linear"], ["zoom"], 3, 1.2, 8, 2.4],
          "line-opacity": 0.9,
        },
      });

      const onMoveEnd = () => push();
      map.on("moveend", onMoveEnd);
      cleanupMoveEnd = () => map.off("moveend", onMoveEnd);
    }

    push();

    return () => {
      cleanupMoveEnd?.();
      pushRef.current = null;
      for (const id of [SELECTED_LAYER, PATH_LAYER]) {
        if (map.getLayer(id)) map.removeLayer(id);
      }
      if (map.getSource(SOURCE_ID)) map.removeSource(SOURCE_ID);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, isLoaded, airports]);

  // Refresh when the fleet or the selection changes (throttled, never mid-move).
  useEffect(() => {
    if (!map || !isLoaded) return;
    const schedule = () => pushRef.current?.();

    if (map.isMoving() || map.isZooming()) {
      map.once("idle", schedule);
      return () => {
        map.off("idle", schedule);
      };
    }
    const wait = MIN_PUSH_MS - (performance.now() - lastPushRef.current);
    if (wait <= 0) {
      schedule();
      return;
    }
    const timer = setTimeout(schedule, wait);
    return () => clearTimeout(timer);
  }, [map, isLoaded, aircraft, selectedHex]);

  useEffect(() => {
    if (!map || !isLoaded || !map.getLayer(SELECTED_LAYER)) return;
    map.setFilter(
      SELECTED_LAYER,
      (selectedHex ? ["==", ["get", "hex"], selectedHex] : EMPTY_FILTER) as never
    );
  }, [map, isLoaded, selectedHex]);

  useEffect(() => {
    if (!map || !isLoaded) return;
    for (const id of [PATH_LAYER, SELECTED_LAYER]) {
      if (map.getLayer(id)) {
        map.setLayoutProperty(id, "visibility", visible ? "visible" : "none");
      }
    }
  }, [map, isLoaded, visible]);

  return null;
}

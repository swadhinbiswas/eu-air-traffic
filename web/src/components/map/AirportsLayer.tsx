import { useEffect, useRef } from "react";
import { useMap } from "@/components/ui/map";
import type { GeoJSONSource } from "maplibre-gl";
import type { Airport } from "@/lib/bundle";

const SOURCE_ID = "airports-src";
const DOT_LAYER = "airports-dot";
const HALO_LAYER = "airports-halo";
const LABEL_LAYER = "airports-label";

interface AirportsLayerProps {
  airports: Airport[];
  visible: boolean;
  onSelect?: (airport: Airport) => void;
  labelCount?: number;
}

const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

function toGeoJson(airports: Airport[], labelCount: number): GeoJSON.FeatureCollection {
  // Render every airport: traffic metrics only exist once flight history has
  // landed, so requiring total_flights > 0 hid the entire network before then.
  const traffic = airports
    .filter((a) => Number.isFinite(a.lat) && Number.isFinite(a.lon))
    .sort(
      (a, b) =>
        b.total_flights - a.total_flights || (b.score ?? 0) - (a.score ?? 0)
    );
  const major = new Set(traffic.slice(0, labelCount).map((a) => a.icao));

  return {
    type: "FeatureCollection",
    features: traffic.map((a) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [a.lon, a.lat] },
      properties: {
        icao: a.icao,
        name: a.name,
        iata: a.iata ?? "",
        flights: a.total_flights,
        delay: a.avg_delay_minutes ?? 0,
        has_metrics: a.avg_delay_minutes !== null,
        on_time: a.on_time_rate ?? 0,
        // Without traffic, importance comes from the capability score instead.
        rank: a.total_flights > 0 ? a.total_flights : (a.score ?? 0) / 10,
        major: major.has(a.icao),
        label: a.icao,
      },
    })),
  };
}

export function AirportsLayer({
  airports,
  visible,
  onSelect,
  labelCount = 18,
}: AirportsLayerProps) {
  const { map, isLoaded } = useMap();
  const airportsRef = useRef(airports);
  const onSelectRef = useRef(onSelect);
  airportsRef.current = airports;
  onSelectRef.current = onSelect;

  useEffect(() => {
    if (!map || !isLoaded) return;
    const instance = map;

    const handleClick = (event: { features?: Array<{ properties?: Record<string, unknown> }> }) => {
      const icao = String(event.features?.[0]?.properties?.icao ?? "");
      const match = airportsRef.current.find((a) => a.icao === icao);
      if (match && onSelectRef.current) onSelectRef.current(match);
    };

    if (!instance.getSource(SOURCE_ID)) {
      instance.addSource(SOURCE_ID, { type: "geojson", data: EMPTY });

      instance.addLayer({
        id: HALO_LAYER,
        type: "circle",
        source: SOURCE_ID,
        layout: { visibility: visible ? "visible" : "none" },
        paint: {
          "circle-radius": [
            "interpolate",
            ["linear"],
            ["get", "rank"],
            0,
            4,
            5,
            7,
            10,
            12,
            20,
            20,
          ],
          "circle-color": [
            "case",
            ["!", ["get", "has_metrics"]],
            "#7dd3fc",
            ["<=", ["coalesce", ["get", "delay"], 0], 5],
            "#34d399",
            ["<=", ["coalesce", ["get", "delay"], 0], 15],
            "#fbbf24",
            "#f87171",
          ],
          "circle-opacity": 0.14,
          "circle-blur": 0.8,
        },
      });

      instance.addLayer({
        id: DOT_LAYER,
        type: "circle",
        source: SOURCE_ID,
        layout: { visibility: visible ? "visible" : "none" },
        paint: {
          "circle-radius": [
            "interpolate",
            ["linear"],
            ["get", "rank"],
            0,
            2.2,
            5,
            3.2,
            10,
            4.6,
            20,
            7,
          ],
          "circle-color": [
            "case",
            ["!", ["get", "has_metrics"]],
            "#7dd3fc",
            ["<=", ["coalesce", ["get", "delay"], 0], 5],
            "#34d399",
            ["<=", ["coalesce", ["get", "delay"], 0], 15],
            "#fbbf24",
            "#f87171",
          ],
          "circle-stroke-color": "#000000",
          "circle-stroke-width": 1,
        },
      });

      instance.addLayer({
        id: LABEL_LAYER,
        type: "symbol",
        source: SOURCE_ID,
        filter: ["==", ["get", "major"], true],
        layout: {
          visibility: visible ? "visible" : "none",
          "text-field": ["get", "label"],
          "text-font": ["Open Sans Semibold"],
          "text-size": 10,
          "text-offset": [0, 1.3],
          "text-anchor": "top",
          "text-allow-overlap": false,
          "text-ignore-placement": false,
        },
        paint: {
          "text-color": "#e4e4e7",
          "text-halo-color": "#000000",
          "text-halo-width": 1.4,
        },
      });

      instance.on("click", DOT_LAYER, handleClick);
      instance.on("mouseenter", DOT_LAYER, () => {
        instance.getCanvas().style.cursor = "pointer";
      });
      instance.on("mouseleave", DOT_LAYER, () => {
        instance.getCanvas().style.cursor = "";
      });
    }

    const source = instance.getSource(SOURCE_ID) as GeoJSONSource | undefined;
    source?.setData(toGeoJson(airportsRef.current, labelCount));

    return () => {
      instance.off("click", DOT_LAYER, handleClick);
      for (const layer of [LABEL_LAYER, DOT_LAYER, HALO_LAYER]) {
        if (instance.getLayer(layer)) instance.removeLayer(layer);
      }
      if (instance.getSource(SOURCE_ID)) instance.removeSource(SOURCE_ID);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, isLoaded]);

  useEffect(() => {
    if (!map || !isLoaded) return;
    const source = map.getSource(SOURCE_ID) as GeoJSONSource | undefined;
    source?.setData(toGeoJson(airports, labelCount));
  }, [map, isLoaded, airports, labelCount]);

  useEffect(() => {
    if (!map || !isLoaded) return;
    for (const layer of [LABEL_LAYER, DOT_LAYER, HALO_LAYER]) {
      if (map.getLayer(layer)) {
        map.setLayoutProperty(layer, "visibility", visible ? "visible" : "none");
      }
    }
  }, [map, isLoaded, visible]);

  return null;
}

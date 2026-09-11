import { useEffect, useRef } from "react";
import { useMap } from "@/components/ui/map";
import type { GeoJSONSource } from "maplibre-gl";
import { CATEGORY_COLORS, tempColor, type WeatherStationView } from "@/lib/weather";

const SOURCE_ID = "weather-src";
const GLOW_LAYER = "weather-glow";
const DOT_LAYER = "weather-dot";
const LABEL_LAYER = "weather-label";

interface WeatherLayerProps {
  stations: WeatherStationView[];
  visible: boolean;
  onSelect?: (station: WeatherStationView) => void;
}

const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

const CATEGORY_COLOR_EXPR: unknown = [
  "match",
  ["get", "category"],
  "VFR", CATEGORY_COLORS.VFR,
  "MVFR", CATEGORY_COLORS.MVFR,
  "IFR", CATEGORY_COLORS.IFR,
  "LIFR", CATEGORY_COLORS.LIFR,
  "#facc15",
];

function toGeoJson(stations: WeatherStationView[]): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: stations
      .filter((s) => Number.isFinite(s.lat) && Number.isFinite(s.lon))
      .map((s) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [s.lon, s.lat] },
        properties: {
          icao: s.icao,
          name: s.name,
          category: s.category ?? "none",
          temp: s.tempC ?? 0,
          wind: s.windKt ?? 0,
          label: s.tempC !== null ? `${Math.round(s.tempC)}°` : s.category ?? "·",
          color: s.category
            ? CATEGORY_COLORS[s.category]
            : tempColor(s.tempC),
        },
      })),
  };
}

/** Weather station layer coloured by flight category (VFR/MVFR/IFR/LIFR). */
export function WeatherLayer({ stations, visible, onSelect }: WeatherLayerProps) {
  const { map, isLoaded } = useMap();
  const stationsRef = useRef(stations);
  const onSelectRef = useRef(onSelect);
  stationsRef.current = stations;
  onSelectRef.current = onSelect;

  useEffect(() => {
    if (!map || !isLoaded) return;
    const instance = map;

    const handleClick = (event: { features?: Array<{ properties?: Record<string, unknown> }> }) => {
      const icao = String(event.features?.[0]?.properties?.icao ?? "");
      const match = stationsRef.current.find((s) => s.icao === icao);
      if (match && onSelectRef.current) onSelectRef.current(match);
    };

    if (!instance.getSource(SOURCE_ID)) {
      instance.addSource(SOURCE_ID, { type: "geojson", data: EMPTY });

      instance.addLayer({
        id: GLOW_LAYER,
        type: "circle",
        source: SOURCE_ID,
        layout: { visibility: visible ? "visible" : "none" },
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["coalesce", ["get", "wind"], 0], 0, 9, 30, 22],
          "circle-color": CATEGORY_COLOR_EXPR as never,
          "circle-opacity": 0.16,
          "circle-blur": 1,
        },
      });

      instance.addLayer({
        id: DOT_LAYER,
        type: "circle",
        source: SOURCE_ID,
        layout: { visibility: visible ? "visible" : "none" },
        paint: {
          "circle-radius": 3.5,
          "circle-color": CATEGORY_COLOR_EXPR as never,
          "circle-stroke-color": "#000000",
          "circle-stroke-width": 1,
        },
      });

      instance.addLayer({
        id: LABEL_LAYER,
        type: "symbol",
        source: SOURCE_ID,
        layout: {
          visibility: visible ? "visible" : "none",
          "text-field": ["get", "label"],
          "text-font": ["Open Sans Semibold"],
          "text-size": 10,
          "text-offset": [0, -1.1],
          "text-anchor": "bottom",
          "text-allow-overlap": false,
        } as never,
        paint: {
          "text-color": CATEGORY_COLOR_EXPR as never,
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
    source?.setData(toGeoJson(stationsRef.current));

    return () => {
      instance.off("click", DOT_LAYER, handleClick);
      for (const layer of [LABEL_LAYER, DOT_LAYER, GLOW_LAYER]) {
        if (instance.getLayer(layer)) instance.removeLayer(layer);
      }
      if (instance.getSource(SOURCE_ID)) instance.removeSource(SOURCE_ID);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, isLoaded]);

  useEffect(() => {
    if (!map || !isLoaded) return;
    const source = map.getSource(SOURCE_ID) as GeoJSONSource | undefined;
    source?.setData(toGeoJson(stations));
  }, [map, isLoaded, stations]);

  useEffect(() => {
    if (!map || !isLoaded) return;
    for (const layer of [LABEL_LAYER, DOT_LAYER, GLOW_LAYER]) {
      if (map.getLayer(layer)) {
        map.setLayoutProperty(layer, "visibility", visible ? "visible" : "none");
      }
    }
  }, [map, isLoaded, visible]);

  return null;
}

import { useEffect, useRef } from "react";
import { useMap } from "@/components/ui/map";
import type { GeoJSONSource, Map as MapLibreMap } from "maplibre-gl";
import { emergencyLabel, isEmergency, type Aircraft } from "@/lib/fleet";
import { ALT_BANDS, loadImage, planeDataUrl } from "./planeIcons";

const SOURCE_ID = "aircraft-src";
const EMERGENCY_IMAGE = "plane-emergency";

interface AircraftLayerProps {
  aircraft: Aircraft[];
  visible: boolean;
  onSelect?: (aircraft: Aircraft) => void;
}

const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

function toGeoJson(aircraft: Aircraft[]): GeoJSON.FeatureCollection {
  const features: GeoJSON.Feature[] = [];
  for (const a of aircraft) {
    if (!Number.isFinite(a.lat) || !Number.isFinite(a.lon)) continue;
    features.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: [a.lon, a.lat] },
      properties: {
        hex: a.hex,
        callsign: a.callsign,
        reg: a.reg ?? "",
        type: a.type ?? "",
        alt: a.altFt ?? 0,
        gs: a.gsKt ?? 0,
        heading: a.trackDeg ?? 0,
        vrate: a.verticalRateFpm ?? 0,
        ias: a.ias ?? 0,
        tas: a.tas ?? 0,
        mach: a.mach ?? 0,
        oat: a.oat ?? 0,
        wd: a.windDir ?? 0,
        ws: a.windSpeedKt ?? 0,
        squawk: a.squawk ?? "",
        emergency: a.emergency ?? "none",
        isEmg: isEmergency(a),
        source: a.source,
      },
    });
  }
  return { type: "FeatureCollection", features };
}

/**
 * Renders live aircraft as rotated silhouettes. One symbol layer per altitude
 * band (filter-only colouring) plus a dedicated emergency layer that overrides
 * band colour for squawk 7500/7600/7700 or ADS-B emergency flags.
 */
export function AircraftLayer({ aircraft, visible, onSelect }: AircraftLayerProps) {
  const { map, isLoaded } = useMap();
  const dataRef = useRef<Aircraft[]>(aircraft);
  const onSelectRef = useRef(onSelect);
  dataRef.current = aircraft;
  onSelectRef.current = onSelect;

  useEffect(() => {
    if (!map || !isLoaded) return;
    let cancelled = false;

    const handleClick = (event: { features?: Array<{ properties?: Record<string, unknown> }> }) => {
      const hex = String(event.features?.[0]?.properties?.hex ?? "");
      const match = dataRef.current.find((a) => a.hex === hex);
      if (match && onSelectRef.current) onSelectRef.current(match);
    };

    async function setup(instance: MapLibreMap) {
      for (const band of ALT_BANDS) {
        const id = `plane-${band.id}`;
        if (instance.hasImage(id)) continue;
        try {
          const image = await loadImage(planeDataUrl(band.color));
          if (cancelled || instance.hasImage(id)) continue;
          instance.addImage(id, image);
        } catch {
          /* skip band on icon failure */
        }
      }
      if (!instance.hasImage(EMERGENCY_IMAGE)) {
        try {
          const image = await loadImage(planeDataUrl("#ff3b5c"));
          if (!cancelled && !instance.hasImage(EMERGENCY_IMAGE)) {
            instance.addImage(EMERGENCY_IMAGE, image);
          }
        } catch {
          /* ignore */
        }
      }
      if (cancelled || instance.getSource(SOURCE_ID)) return;

      instance.addSource(SOURCE_ID, { type: "geojson", data: EMPTY });

      const symbolLayout = (image: string) => ({
        "icon-image": image,
        "icon-size": ["interpolate", ["linear"], ["zoom"], 0, 0.3, 3, 0.45, 6, 0.75, 10, 1.15],
        "icon-rotate": ["coalesce", ["get", "heading"], 0],
        "icon-rotation-alignment": "map" as const,
        "icon-pitch-alignment": "map" as const,
        "icon-allow-overlap": true,
        "icon-ignore-placement": true,
        "icon-anchor": "center" as const,
      });

      for (const band of ALT_BANDS) {
        const layerId = `aircraft-${band.id}`;
        if (instance.getLayer(layerId)) continue;
        instance.addLayer({
          id: layerId,
          type: "symbol",
          source: SOURCE_ID,
          filter: [
            "all",
            ["!=", ["get", "isEmg"], true],
            [">=", ["coalesce", ["get", "alt"], 0], band.min],
            ["<", ["coalesce", ["get", "alt"], 0], band.max],
          ],
          layout: { ...symbolLayout(`plane-${band.id}`), visibility: visible ? "visible" : "none" } as never,
          paint: { "icon-opacity": 0.95 },
        });
        instance.on("click", layerId, handleClick);
        instance.on("mouseenter", layerId, () => {
          instance.getCanvas().style.cursor = "pointer";
        });
        instance.on("mouseleave", layerId, () => {
          instance.getCanvas().style.cursor = "";
        });
      }

      const emgLayer = "aircraft-emergency";
      if (!instance.getLayer(emgLayer)) {
        instance.addLayer({
          id: emgLayer,
          type: "symbol",
          source: SOURCE_ID,
          filter: ["==", ["get", "isEmg"], true],
          layout: { ...symbolLayout(EMERGENCY_IMAGE), visibility: visible ? "visible" : "none" } as never,
          paint: { "icon-opacity": 1 },
        });
        instance.on("click", emgLayer, handleClick);
        instance.on("mouseenter", emgLayer, () => {
          instance.getCanvas().style.cursor = "pointer";
        });
        instance.on("mouseleave", emgLayer, () => {
          instance.getCanvas().style.cursor = "";
        });
      }

      const source = instance.getSource(SOURCE_ID) as GeoJSONSource | undefined;
      source?.setData(toGeoJson(dataRef.current));
    }

    void setup(map);

    return () => {
      cancelled = true;
      const layers = [...ALT_BANDS.map((b) => `aircraft-${b.id}`), "aircraft-emergency"];
      for (const id of layers) {
        map.off("click", id, handleClick);
        if (map.getLayer(id)) map.removeLayer(id);
      }
      if (map.getSource(SOURCE_ID)) map.removeSource(SOURCE_ID);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, isLoaded]);

  useEffect(() => {
    if (!map || !isLoaded) return;
    const source = map.getSource(SOURCE_ID) as GeoJSONSource | undefined;
    source?.setData(toGeoJson(aircraft));
  }, [map, isLoaded, aircraft]);

  useEffect(() => {
    if (!map || !isLoaded) return;
    const layers = [...ALT_BANDS.map((b) => `aircraft-${b.id}`), "aircraft-emergency"];
    for (const id of layers) {
      if (map.getLayer(id)) {
        map.setLayoutProperty(id, "visibility", visible ? "visible" : "none");
      }
    }
  }, [map, isLoaded, visible]);

  return null;
}

export { emergencyLabel };

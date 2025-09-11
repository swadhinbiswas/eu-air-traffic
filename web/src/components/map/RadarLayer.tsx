import { useEffect, useState } from "react";
import { useMap } from "@/components/ui/map";

const RADAR_META_URL = "https://api.rainviewer.com/public/weather-maps.json";
const SOURCE_ID = "radar-src";
const LAYER_ID = "radar-layer";

/** Highest zoom RainViewer's public radar tiles actually serve. */
const RAINVIEWER_MAX_ZOOM = 7;
/** Hide the overscaled radar once it stops being legible. */
const RAINVIEWER_VISIBLE_ZOOM = 9;

interface RadarMeta {
  host: string;
  radar?: { past?: Array<{ time: number; path: string }> };
}

/** RainViewer precipitation radar as a raster layer on the globe. */
export function RadarLayer({ visible, opacity = 0.6 }: { visible: boolean; opacity?: number }) {
  const { map, isLoaded } = useMap();
  const [tileUrl, setTileUrl] = useState<string | null>(null);

  // Refresh the latest radar frame every 5 minutes.
  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const res = await fetch(RADAR_META_URL);
        if (!res.ok) return;
        const meta = (await res.json()) as RadarMeta;
        const past = meta.radar?.past ?? [];
        const latest = past[past.length - 1];
        if (!latest || !alive) return;
        setTileUrl(`${meta.host}${latest.path}/256/{z}/{x}/{y}/2/1_1.png`);
      } catch {
        /* radar optional */
      }
    }
    void load();
    const id = setInterval(load, 300_000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  useEffect(() => {
    if (!map || !isLoaded || !tileUrl) return;
    const instance = map;

    if (!instance.getSource(SOURCE_ID)) {
      instance.addSource(SOURCE_ID, {
        type: "raster",
        tiles: [tileUrl],
        tileSize: 256,
        // RainViewer's public radar tiles only cover zoom 0–7. At z≥8 it serves a
        // placeholder image containing "Zoom Level Not Supported", so cap the
        // source here and let MapLibre overscale the z7 tiles instead.
        minzoom: 0,
        maxzoom: RAINVIEWER_MAX_ZOOM,
        attribution: "© RainViewer",
      });
    }
    if (!instance.getLayer(LAYER_ID)) {
      instance.addLayer(
        {
          id: LAYER_ID,
          type: "raster",
          source: SOURCE_ID,
          paint: { "raster-opacity": opacity, "raster-fade-duration": 300 },
          // Beyond ~z9 the overscaled radar is just mush; hide the layer so it
          // never obscures street-level detail.
          maxzoom: RAINVIEWER_VISIBLE_ZOOM,
          layout: { visibility: visible ? "visible" : "none" },
        },
        // Place radar beneath the aircraft/airport/weather layers when possible.
        instance.getLayer("weather-glow") ? "weather-glow" : undefined
      );
    }
    return () => {
      if (instance.getLayer(LAYER_ID)) instance.removeLayer(LAYER_ID);
      if (instance.getSource(SOURCE_ID)) instance.removeSource(SOURCE_ID);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, isLoaded, tileUrl]);

  useEffect(() => {
    if (!map || !isLoaded || !map.getLayer(LAYER_ID)) return;
    map.setLayoutProperty(LAYER_ID, "visibility", visible ? "visible" : "none");
  }, [map, isLoaded, visible]);

  return null;
}

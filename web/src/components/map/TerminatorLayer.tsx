import { useEffect, useState } from "react";
import { useMap } from "@/components/ui/map";
import type { GeoJSONSource } from "maplibre-gl";

const SOURCE_ID = "terminator-src";
const FILL_ID = "terminator-fill";

const REFRESH_MS = 60_000;

function dayOfYear(date: Date): number {
  const start = Date.UTC(date.getUTCFullYear(), 0, 0);
  return Math.floor((date.getTime() - start) / 86_400_000);
}

/**
 * Night-side polygon (terminator) as GeoJSON, computed from solar declination
 * and the subsolar longitude. Purely astronomical — no API needed.
 */
function nightPolygon(date: Date): GeoJSON.FeatureCollection {
  const deg = Math.PI / 180;
  const doy = dayOfYear(date);
  const declination = 23.44 * Math.sin(deg * (360 * (doy - 81)) / 365);
  const utcHours = date.getUTCHours() + date.getUTCMinutes() / 60;
  const subsolarLon = -15 * (utcHours - 12);

  const coords: [number, number][] = [];
  for (let lon = -180; lon <= 180; lon += 2) {
    const hourAngle = (lon - subsolarLon) * deg;
    const lat = Math.atan(-Math.cos(hourAngle) / Math.tan(declination * deg)) / deg;
    coords.push([lon, lat]);
  }
  // Close the ring around the pole that is in darkness.
  const darkPole = declination > 0 ? -90 : 90;
  coords.push([180, darkPole], [-180, darkPole], coords[0]);

  return {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        properties: {},
        geometry: { type: "Polygon", coordinates: [coords] },
      },
    ],
  };
}

/** Day/night terminator overlay — the dark side of the globe. */
export function TerminatorLayer({ visible }: { visible: boolean }) {
  const { map, isLoaded } = useMap();
  const [data, setData] = useState<GeoJSON.FeatureCollection>(() => nightPolygon(new Date()));

  useEffect(() => {
    const id = setInterval(() => setData(nightPolygon(new Date())), REFRESH_MS);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    if (!map || !isLoaded) return;
    const instance = map;

    if (!instance.getSource(SOURCE_ID)) {
      instance.addSource(SOURCE_ID, { type: "geojson", data });
    }
    if (!instance.getLayer(FILL_ID)) {
      instance.addLayer({
        id: FILL_ID,
        type: "fill",
        source: SOURCE_ID,
        // Past regional zoom the night polygon becomes a full-screen overdraw
        // that hides nothing useful; stop drawing it.
        maxzoom: 7,
        paint: { "fill-color": "#000000", "fill-opacity": 0.42 },
        layout: { visibility: visible ? "visible" : "none" },
      });
    }
    return () => {
      if (instance.getLayer(FILL_ID)) instance.removeLayer(FILL_ID);
      if (instance.getSource(SOURCE_ID)) instance.removeSource(SOURCE_ID);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, isLoaded]);

  useEffect(() => {
    if (!map || !isLoaded) return;
    const source = map.getSource(SOURCE_ID) as GeoJSONSource | undefined;
    source?.setData(data);
  }, [map, isLoaded, data]);

  useEffect(() => {
    if (!map || !isLoaded || !map.getLayer(FILL_ID)) return;
    map.setLayoutProperty(FILL_ID, "visibility", visible ? "visible" : "none");
  }, [map, isLoaded, visible]);

  return null;
}

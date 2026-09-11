import { useEffect, useRef } from "react";
import { Map as MapView, MapControls, useMap } from "@/components/ui/map";
import type { MapMouseEvent } from "maplibre-gl";
import { AircraftLayer } from "./AircraftLayer";
import { AirportsLayer } from "./AirportsLayer";
import { RoutesLayer } from "./RoutesLayer";
import { WeatherLayer } from "./WeatherLayer";
import { RadarLayer } from "./RadarLayer";
import { TerminatorLayer } from "./TerminatorLayer";
import { ALT_BANDS } from "./planeIcons";
import type { MapSelection } from "./types";
import type { Airport } from "@/lib/bundle";
import type { Aircraft } from "@/lib/fleet";
import type { WeatherStationView } from "@/lib/weather";
import type { RouteDatum } from "@/lib/bundle";

export type LayerId =
  | "aircraft"
  | "airports"
  | "routes"
  | "weather"
  | "radar"
  | "terminator";

const DARK_STYLE = "https://basemaps.cartocdn.com/gl/dark-matter-nolabels-gl-style/style.json";

const INTERACTIVE_LAYERS = [
  "airports-dot",
  "weather-dot",
  ...ALT_BANDS.map((b) => `aircraft-${b.id}`),
  "aircraft-emergency",
];

interface WorldMapProps {
  visibleLayers: Set<LayerId>;
  aircraft: Aircraft[];
  airports: Airport[];
  weather: WeatherStationView[];
  routes: RouteDatum[];
  autoRotate: boolean;
  onSelect: (selection: MapSelection | null) => void;
}

/** Gentle longitude drift that pauses while the user interacts. */
function AutoRotate({ enabled }: { enabled: boolean }) {
  const { map, isLoaded } = useMap();
  const enabledRef = useRef(enabled);
  const pausedRef = useRef(false);
  const lastRef = useRef(0);
  enabledRef.current = enabled;

  useEffect(() => {
    if (!map || !isLoaded) return;
    const pause = () => {
      pausedRef.current = true;
    };
    const resume = () => {
      pausedRef.current = false;
    };
    map.on("mousedown", pause);
    map.on("touchstart", pause);
    map.on("mouseup", resume);
    map.on("touchend", resume);
    map.on("wheel", pause);
    map.on("dragend", resume);

    let raf = 0;
    const tick = (time: number) => {
      if (
        enabledRef.current &&
        !pausedRef.current &&
        !map.isMoving() &&
        time - lastRef.current > 60
      ) {
        lastRef.current = time;
        const center = map.getCenter();
        map.setCenter([center.lng + 0.05, center.lat]);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(raf);
      map.off("mousedown", pause);
      map.off("mouseup", resume);
      map.off("touchstart", pause);
      map.off("touchend", resume);
      map.off("wheel", pause);
      map.off("dragend", resume);
    };
  }, [map, isLoaded]);

  return null;
}

/** Clears the selection when the user clicks empty space (no feature hit). */
/**
 * Globe projection does not support cursor-anchored zoom
 * ("Easing around a point is not supported under globe projection").
 * Anchor wheel + pinch zoom at the map centre — the documented behaviour that
 * globe supports — so zooming over Europe stays smooth and predictable.
 */
function GlobeInteraction() {
  const { map, isLoaded } = useMap();

  useEffect(() => {
    if (!map || !isLoaded) return;
    try {
      // `enable()` is a no-op when already enabled, so re-enable with the
      // centre-anchored option.
      map.scrollZoom.disable();
      map.scrollZoom.enable({ around: "center" });
      map.touchZoomRotate.disable();
      map.touchZoomRotate.enable({ around: "center" });
    } catch {
      /* older MapLibre builds fall back to defaults */
    }
  }, [map, isLoaded]);

  return null;
}

function BackgroundClick({ onClear }: { onClear: () => void }) {
  const { map, isLoaded } = useMap();
  const onClearRef = useRef(onClear);
  onClearRef.current = onClear;

  useEffect(() => {
    if (!map || !isLoaded) return;
    const handler = (event: MapMouseEvent) => {
      const layers = INTERACTIVE_LAYERS.filter((id) => map.getLayer(id));
      const hits = layers.length ? map.queryRenderedFeatures(event.point, { layers }) : [];
      if (!hits.length) onClearRef.current();
    };
    map.on("click", handler);
    return () => {
      map.off("click", handler);
    };
  }, [map, isLoaded]);

  return null;
}

/**
 * The God's Eye View: a MapLibre globe (dark CARTO Earth tiles) with live
 * aircraft, airports, route arcs, aviation weather, precipitation radar and a
 * day/night terminator.
 */
export function WorldMap({
  visibleLayers,
  aircraft,
  airports,
  weather,
  routes,
  autoRotate,
  onSelect,
}: WorldMapProps) {
  return (
    <MapView
      theme="dark"
      projection={{ type: "globe" }}
      center={[12, 50]}
      zoom={1.7}
      styles={{ dark: DARK_STYLE, light: DARK_STYLE }}
      className="h-full w-full"
    >
      <AutoRotate enabled={autoRotate} />
      <GlobeInteraction />
      <MapControls showZoom showCompass position="bottom-right" />
      <BackgroundClick onClear={() => onSelect(null)} />
      <RadarLayer visible={visibleLayers.has("radar")} />
      <TerminatorLayer visible={visibleLayers.has("terminator")} />
      <RoutesLayer routes={routes} airports={airports} visible={visibleLayers.has("routes")} />
      <AircraftLayer
        aircraft={aircraft}
        visible={visibleLayers.has("aircraft")}
        onSelect={(plane: Aircraft) => onSelect({ kind: "aircraft", data: plane })}
      />
      <AirportsLayer
        airports={airports}
        visible={visibleLayers.has("airports")}
        onSelect={(airport: Airport) => onSelect({ kind: "airport", data: airport })}
      />
      <WeatherLayer
        stations={weather}
        visible={visibleLayers.has("weather")}
        onSelect={(station: WeatherStationView) => onSelect({ kind: "weather", data: station })}
      />
    </MapView>
  );
}

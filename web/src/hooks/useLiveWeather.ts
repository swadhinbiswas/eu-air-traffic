import { useEffect, useState } from "react";
import { loadBundle, tryLiveApi } from "../lib/bundle";
import type { LiveSnapshot, WeatherStation } from "../lib/bundle";
import { fromMetar, fromOpenMeteo, type WeatherStationView } from "../lib/weather";

export interface LiveWeatherState {
  stations: WeatherStationView[];
  source: "metar" | "open-meteo" | "none";
  lastUpdated: number | null;
}

/**
 * Live aviation weather (METAR with flight category) from the VPS collector's
 * `/live/snapshot`, falling back to the bundled Open-Meteo forecast.
 */
export function useLiveWeather(): LiveWeatherState {
  const [state, setState] = useState<LiveWeatherState>({
    stations: [],
    source: "none",
    lastUpdated: null,
  });

  useEffect(() => {
    let alive = true;

    // Seed from the bundled Open-Meteo forecast immediately.
    loadBundle<WeatherStation[]>("weather.json")
      .then((rows) => {
        if (!alive || !rows?.length) return;
        setState((s) =>
          s.stations.length
            ? s
            : { stations: rows.map(fromOpenMeteo), source: "open-meteo", lastUpdated: Date.now() }
        );
      })
      .catch(() => undefined);

    async function poll() {
      const snap = await tryLiveApi<LiveSnapshot>("/live/snapshot");
      if (alive && snap?.weather?.metar?.length) {
        setState({
          stations: snap.weather.metar.map(fromMetar),
          source: "metar",
          lastUpdated: Date.now(),
        });
      }
    }

    void poll();
    const id = setInterval(poll, 60_000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  return state;
}

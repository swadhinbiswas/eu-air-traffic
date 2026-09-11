import type { Aircraft } from "@/lib/fleet";
import type { WeatherStationView } from "@/lib/weather";
import type { Airport } from "@/lib/bundle";

export interface SelectedAircraft {
  kind: "aircraft";
  data: Aircraft;
}

export interface SelectedAirport {
  kind: "airport";
  data: Airport;
}

export interface SelectedWeather {
  kind: "weather";
  data: WeatherStationView;
}

export type MapSelection = SelectedAircraft | SelectedAirport | SelectedWeather;

/**
 * ICAO airline designators → names.
 *
 * An ADS-B callsign is the 3-letter ICAO airline designator followed by a
 * flight number (e.g. `DLH123` → Lufthansa). We use this to attribute live
 * traffic to operators without any extra API.
 */
export const AIRLINE_NAMES: Record<string, string> = {
  DLH: "Lufthansa",
  CLH: "Lufthansa CityLine",
  EWG: "Eurowings",
  GWI: "Germanwings",
  BAW: "British Airways",
  EZY: "easyJet",
  EXS: "Jet2",
  LOG: "Loganair",
  TOM: "TUI Airways",
  AFR: "Air France",
  TVF: "Transavia France",
  RYR: "Ryanair",
  RUK: "Ryanair UK",
  MAL: "Maleth-Aero",
  KLM: "KLM",
  TRA: "Transavia",
  KLC: "KLM Cityhopper",
  IBE: "Iberia",
  ANE: "Air Nostrum",
  VLG: "Vueling",
  AEA: "Air Europa",
  ITY: "ITA Airways",
  DLA: "Air Dolomiti",
  SWR: "Swiss",
  AUA: "Austrian Airlines",
  LXZ: "Swiss",
  SAS: "SAS",
  NAX: "Norwegian",
  WZZ: "Wizz Air",
  ROT: "Tarom",
  LOT: "LOT Polish Airlines",
  CSA: "Czech Airlines",
  THY: "Turkish Airlines",
  PGT: "Pegasus",
  AEE: "Aegean",
  OAL: "Olympic Air",
  EIN: "Aer Lingus",
  RYR_IE: "Ryanair",
  FIN: "Finnair",
  TAP: "TAP Air Portugal",
  PGA: "TAP Express",
  TAR: "Tunisair",
  RAM: "Royal Air Maroc",
  AAN: "Aegean",
  BCY: "CityJet",
  EAI: "Emerald Airlines",
  VOE: "Volotea",
  TVS: "Smartwings",
  WMT: "Wizz Air Malta",
  LDA: "Lauda Europe",
  CFG: "Condor",
  SXS: "SunExpress",
  CAI: "Corendon Airlines",
  QTR: "Qatar Airways",
  UAE: "Emirates",
  ETD: "Etihad",
  SVA: "Saudia",
  THA: "Thai Airways",
  SIA: "Singapore Airlines",
  ANA: "All Nippon Airways",
  JAL: "Japan Airlines",
  DAL: "Delta Air Lines",
  AAL: "American Airlines",
  UAL: "United Airlines",
  ACA: "Air Canada",
  SWA: "Southwest",
  FDX: "FedEx",
  UPS: "UPS",
  GTI: "Atlas Air",
  CLX: "Cargolux",
  BCS: "European Air Transport",
  DHK: "DHL Air",
};

/** Derive the operating airline from a callsign's 3-letter designator. */
export function airlineFromCallsign(callsign: string | null | undefined): {
  icao: string;
  name: string;
} | null {
  if (!callsign) return null;
  const code = callsign.trim().toUpperCase().slice(0, 3);
  if (!/^[A-Z]{3}$/.test(code)) return null;
  return { icao: code, name: AIRLINE_NAMES[code] ?? code };
}

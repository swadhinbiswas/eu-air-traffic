/**
 * Aircraft icons for the MapLibre globe.
 *
 * Planes are rendered as real rotated silhouettes (airliner top-down view),
 * one colored glyph per altitude band so the symbol layer can pick the right
 * colour with a simple filter.
 */

export interface AltBand {
  id: string;
  color: string;
  min: number;
  max: number;
}

/** Altitude bands in feet, low → high (matches the HUD legend). */
export const ALT_BANDS: AltBand[] = [
  { id: "gnd", color: "#94a3b8", min: -1_000_000, max: 1_000 },
  { id: "low", color: "#22d3ee", min: 1_000, max: 10_000 },
  { id: "mid", color: "#34d399", min: 10_000, max: 25_000 },
  { id: "high", color: "#fbbf24", min: 25_000, max: 38_000 },
  { id: "upper", color: "#f87171", min: 38_000, max: 1_000_000 },
];

// Top-down airliner silhouette, nose pointing north (heading 0°).
const PLANE_PATH =
  "M32 2C29.8 2 28 4.4 28 8.5L27 23.5L4 35.5L4 41.5L27 34L26 47L17.5 53L17.5 57.5L32 53.5L46.5 57.5L46.5 53L38 47L37 34L60 41.5L60 35.5L37 23.5L36 8.5C36 4.4 34.2 2 32 2Z";

export function planeDataUrl(color: string): string {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64"><path d="${PLANE_PATH}" fill="${color}" stroke="#000000" stroke-width="1.4" stroke-linejoin="round"/></svg>`;
  return `data:image/svg+xml;base64,${btoa(svg)}`;
}

export function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image(64, 64);
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("Failed to load map icon"));
    image.src = url;
  });
}

export function labelForAlt(alt: number | null | undefined): string {
  if (alt === null || alt === undefined || alt <= 0) return "GND";
  return `FL${Math.round(alt / 100)}`;
}

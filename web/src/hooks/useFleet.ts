import { useEffect, useRef, useState } from "react";
import { LIVE_BASE, liveGatewayConfigured, loadBundle, tryLiveApi } from "../lib/bundle";
import type { LiveSnapshot, Position } from "../lib/bundle";
import {
  advance,
  fromCanonical,
  fromSnapshot,
  type Aircraft,
  type FleetSource,
} from "../lib/fleet";

const POLL_MS = 5000;
// Dead-reckoning creates a fresh object per aircraft; 1s is smooth without
// churning thousands of allocations faster than the map can consume them.
const TICK_MS = 1000;
// Never extrapolate further than this past a fix: a stale contact should sit
// still rather than fly off on a tangent.
const MAX_EXTRAPOLATION_S = 90;

export interface FleetState {
  aircraft: Aircraft[];
  source: FleetSource;
  status: "connecting" | "live" | "snapshot";
  lastUpdated: number | null;
  error: string | null;
}

/**
 * Live fleet from the VPS collector's `/live/snapshot`.
 *
 * Dead-reckoning is anchored to each position's *fix time* (`collected_at`),
 * not to when the browser received it. The collector polls every ~15s while the
 * client polls every 5s, so the same fix arrives up to three times; treating the
 * receipt time as "now" made aircraft glide forward then snap back on every
 * poll. Keeping the newest fix per airframe removes that oscillation.
 */
export function useFleet(): FleetState {
  const [state, setState] = useState<FleetState>({
    aircraft: [],
    source: "snapshot",
    status: "connecting",
    lastUpdated: null,
    error: null,
  });

  // hex → newest known fix. Replaced only when a genuinely newer fix arrives.
  const fixesRef = useRef<Map<string, Aircraft>>(new Map());
  const lastLiveRef = useRef<number>(0);
  const warnedRef = useRef(false);

  /** Project every known fix to `now`. Safe to call from any effect: refs only. */
  const materialise = (now: number): Aircraft[] => {
    const out: Aircraft[] = [];
    for (const plane of fixesRef.current.values()) {
      const elapsed = Math.min(Math.max((now - plane.fixMs) / 1000, 0), MAX_EXTRAPOLATION_S);
      out.push(advance(plane, elapsed));
    }
    return out;
  };

  // Explain an offline feed once, in the console, with the actionable cause.
  useEffect(() => {
    if (!liveGatewayConfigured()) {
      console.warn(
        "[live] VITE_LIVE_URL is not set in this build — no live feed. " +
          "Set it (e.g. https://live.example.com) and rebuild; Vite inlines env at build time."
      );
    }
  }, []);

  // Seed immediately from the runtime snapshot endpoint.
  useEffect(() => {
    let alive = true;
    loadBundle<Position[]>("positions.json")
      .then((rows) => {
        if (!alive || fixesRef.current.size) return;
        for (const row of rows) {
          const plane = fromSnapshot(row);
          fixesRef.current.set(plane.hex, plane);
        }
        setState((s) => ({ ...s, aircraft: [...fixesRef.current.values()] }));
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  // Poll the live snapshot.
  useEffect(() => {
    let alive = true;

    async function poll() {
      const snap = await tryLiveApi<LiveSnapshot>("/live/snapshot?slim=1");
      if (alive && snap?.positions?.length) {
        const seen = new Set<string>();
        for (const canonical of snap.positions) {
          const plane = fromCanonical(canonical);
          if (!Number.isFinite(plane.lat) || !Number.isFinite(plane.lon)) continue;
          seen.add(plane.hex);
          const previous = fixesRef.current.get(plane.hex);
          // Only advance the anchor when the fix itself is newer — re-polling an
          // unchanged fix must not reset the dead-reckoning clock.
          if (!previous || plane.fixMs > previous.fixMs) {
            fixesRef.current.set(plane.hex, plane);
          }
        }
        // Drop contacts the collector has aged out of its window.
        for (const hex of fixesRef.current.keys()) {
          if (!seen.has(hex)) fixesRef.current.delete(hex);
        }
        lastLiveRef.current = Date.now();
        setState((s) => ({
          ...s,
          aircraft: materialise(Date.now()),
          source: "live",
          status: "live",
          lastUpdated: Date.now(),
          error: null,
        }));
        return;
      }

      if (alive && Date.now() - lastLiveRef.current > 25_000) {
        setState((s) => ({ ...s, status: "snapshot", error: null }));
        if (!warnedRef.current && liveGatewayConfigured()) {
          warnedRef.current = true;
          const securePage = window.location.protocol === "https:";
          const insecureApi = LIVE_BASE.startsWith("http://");
          console.warn(
            `[live] no snapshot from ${LIVE_BASE}/live/snapshot.` +
              (securePage && insecureApi
                ? " This page is HTTPS but the API is HTTP — the browser blocks mixed content. " +
                  "Serve the API over HTTPS (Cloudflare Tunnel / Caddy) and rebuild with that URL."
                : " Check the collector is reachable from this browser and that the URL is correct.")
          );
        }
      }
    }

    void poll();
    const id = setInterval(poll, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  // Extrapolate each aircraft from its own fix time.
  useEffect(() => {
    const id = setInterval(() => {
      if (document.hidden || !fixesRef.current.size) return;
      setState((s) => ({ ...s, aircraft: materialise(Date.now()) }));
    }, TICK_MS);
    return () => clearInterval(id);
  }, []);

  return state;
}

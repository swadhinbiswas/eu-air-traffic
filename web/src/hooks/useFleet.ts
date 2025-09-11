import { useEffect, useRef, useState } from "react";
import { loadBundle, tryLiveApi } from "../lib/bundle";
import type { LiveSnapshot, Position } from "../lib/bundle";
import {
  advance,
  fromCanonical,
  fromSnapshot,
  type Aircraft,
  type FleetSource,
} from "../lib/fleet";

const POLL_MS = 5000;
const TICK_MS = 1000;

export interface FleetState {
  aircraft: Aircraft[];
  source: FleetSource;
  status: "connecting" | "live" | "snapshot";
  lastUpdated: number | null;
  error: string | null;
}

/**
 * Live fleet from the VPS collector's single `/live/snapshot` endpoint,
 * falling back to the bundled snapshot. Between polls, aircraft are
 * dead-reckoned using ground speed + track so they glide smoothly.
 */
export function useFleet(): FleetState {
  const [state, setState] = useState<FleetState>({
    aircraft: [],
    source: "snapshot",
    status: "connecting",
    lastUpdated: null,
    error: null,
  });

  const baseRef = useRef<Aircraft[]>([]);
  const baseTimeRef = useRef<number>(Date.now());
  const lastLiveRef = useRef<number>(0);

  // Seed immediately from the static bundle.
  useEffect(() => {
    let alive = true;
    loadBundle<Position[]>("positions.json")
      .then((rows) => {
        if (!alive || baseRef.current.length) return;
        baseRef.current = rows.map(fromSnapshot);
        baseTimeRef.current = Date.now();
        setState((s) => ({ ...s, aircraft: baseRef.current }));
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
      const snap = await tryLiveApi<LiveSnapshot>("/live/snapshot");
      if (alive && snap?.positions?.length) {
        const aircraft = snap.positions
          .map(fromCanonical)
          .filter((a) => Number.isFinite(a.lat) && Number.isFinite(a.lon));
        baseRef.current = aircraft;
        baseTimeRef.current = Date.now();
        lastLiveRef.current = Date.now();
        setState({
          aircraft,
          source: "live",
          status: "live",
          lastUpdated: Date.now(),
          error: null,
        });
        return;
      }
      if (alive && Date.now() - lastLiveRef.current > 25_000) {
        setState((s) => ({ ...s, status: "snapshot", error: null }));
      }
    }

    void poll();
    const id = setInterval(poll, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  // Smooth interpolation between polls.
  useEffect(() => {
    const id = setInterval(() => {
      if (!baseRef.current.length) return;
      const elapsed = (Date.now() - baseTimeRef.current) / 1000;
      const advanced = baseRef.current.map((a) => advance(a, elapsed));
      setState((s) => ({ ...s, aircraft: advanced }));
    }, TICK_MS);
    return () => clearInterval(id);
  }, []);

  return state;
}

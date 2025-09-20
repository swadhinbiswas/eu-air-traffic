import { useEffect, useRef, useState } from "react";
import {
  loadBundle,
  type Airport,
  type Analytics,
  type Catalog,
  type Kpis,
  type Metar,
  type Ops,
  type Position,
  type Story,
  type BundleManifest,
} from "../lib/bundle";
import type { BatchFreshness } from "../lib/tursoData";

/**
 * Load a dashboard dataset at runtime (warehouse API / collector), refreshing
 * periodically so the site stays current without a rebuild.
 *
 * Turso bills rows read, so hidden tabs must not keep polling: the interval
 * skips while `document.hidden`, and returning to the tab refreshes once. The
 * intervals themselves mirror the lake cadence (15 minutes) rather than
 * hammering for data that cannot have changed.
 */
function useRuntimeData<T>(file: string, refreshMs = 300_000) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const dataRef = useRef<T | null>(null);

  useEffect(() => {
    let alive = true;

    async function load() {
      try {
        const d = await loadBundle<T>(file);
        if (alive) {
          dataRef.current = d;
          setData(d);
          setError(null);
        }
      } catch (e: unknown) {
        // A transient refresh failure must not blank a page that already has
        // data; keep the stale frame and retry on the next tick.
        if (alive && !dataRef.current) setError(e instanceof Error ? e.message : "Load failed");
      } finally {
        if (alive) setLoading(false);
      }
    }

    const refreshIfVisible = () => {
      if (!document.hidden) void load();
    };

    void load();
    const id = refreshMs > 0 ? setInterval(refreshIfVisible, refreshMs) : null;
    document.addEventListener("visibilitychange", refreshIfVisible);
    return () => {
      alive = false;
      if (id) clearInterval(id);
      document.removeEventListener("visibilitychange", refreshIfVisible);
    };
  }, [file, refreshMs]);

  return { data, error, loading };
}

export function useManifest() {
  return useRuntimeData<BundleManifest>("manifest.json");
}
export function useAirports() {
  return useRuntimeData<Airport[]>("airports.json", 600_000);
}
export function useMetars() {
  return useRuntimeData<Metar[]>("metars.json");
}
export function useAnalytics() {
  return useRuntimeData<Analytics>("analytics.json");
}
export function useCatalog() {
  return useRuntimeData<Catalog>("catalog.json", 600_000);
}
export function useStories() {
  return useRuntimeData<Story[]>("stories.json", 900_000);
}
export function useOps() {
  return useRuntimeData<Ops>("ops.json");
}
export function useKpis() {
  return useRuntimeData<Kpis>("kpis.json");
}

/** Warehouse freshness + whether flight history exists (drives empty states). */
export function useBatchStatus() {
  return useRuntimeData<BatchFreshness>("freshness.json");
}

/** Live aircraft positions (runtime bundle; the map uses the live snapshot). */
export function usePositions() {
  const { data, loading } = useRuntimeData<Position[]>("positions.json", 30_000);
  return { data, live: false, loading };
}

export { useRuntimeData };

import { useEffect, useState } from "react";
import {
  loadBundle,
  type Airport,
  type Analytics,
  type Kpis,
  type Metar,
  type Ops,
  type Position,
  type Story,
  type BundleManifest,
} from "../lib/bundle";
import type { BatchFreshness } from "../lib/motherduckData";

/**
 * Load a dashboard dataset at runtime (warehouse API / collector), refreshing
 * periodically so the site stays current without a rebuild.
 */
function useRuntimeData<T>(file: string, refreshMs = 60_000) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;

    async function load() {
      try {
        const d = await loadBundle<T>(file);
        if (alive) {
          setData(d);
          setError(null);
        }
      } catch (e: unknown) {
        if (alive) setError(e instanceof Error ? e.message : "Load failed");
      } finally {
        if (alive) setLoading(false);
      }
    }

    void load();
    const id = refreshMs > 0 ? setInterval(load, refreshMs) : null;
    return () => {
      alive = false;
      if (id) clearInterval(id);
    };
  }, [file, refreshMs]);

  return { data, error, loading };
}

export function useManifest() {
  return useRuntimeData<BundleManifest>("manifest.json");
}
export function useAirports() {
  return useRuntimeData<Airport[]>("airports.json");
}
export function useMetars() {
  return useRuntimeData<Metar[]>("metars.json");
}
export function useAnalytics() {
  return useRuntimeData<Analytics>("analytics.json");
}
export function useCatalog() {
  const { data, error, loading } = useRuntimeData<Analytics>("analytics.json");
  return { data: data?.catalog ?? null, error, loading };
}
export function useStories() {
  return useRuntimeData<Story[]>("stories.json");
}
export function useOps() {
  return useRuntimeData<Ops>("ops.json");
}
export function useKpis() {
  return useRuntimeData<Kpis>("kpis.json", 30_000);
}

/** Warehouse freshness + whether flight history exists (drives empty states). */
export function useBatchStatus() {
  return useRuntimeData<BatchFreshness>("freshness.json", 60_000);
}

/** Live aircraft positions (runtime bundle; the map uses the live snapshot). */
export function usePositions() {
  const { data, loading } = useRuntimeData<Position[]>("positions.json", 30_000);
  return { data, live: false, loading };
}

export { useRuntimeData };

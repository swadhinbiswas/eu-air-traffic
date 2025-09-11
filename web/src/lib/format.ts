export function nf(value: unknown, digits = 0): string {
  const n = Number(value);
  if (value === null || value === undefined || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}

export function compact(value: unknown): string {
  const n = Number(value);
  if (value === null || value === undefined || Number.isNaN(n)) return "—";
  return new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(n);
}

export function pct(value: unknown, digits = 1): string {
  const n = Number(value);
  if (value === null || value === undefined || Number.isNaN(n)) return "—";
  return `${(n * 100).toFixed(digits)}%`;
}

export function delayTone(minutes: unknown): string {
  const n = Number(minutes);
  if (Number.isNaN(n)) return "text-zinc-400";
  if (n <= 5) return "text-emerald-400";
  if (n <= 15) return "text-amber-400";
  return "text-red-400";
}

export function altBand(alt: number | null): { color: string; label: string } {
  if (alt === null || alt <= 0) return { color: "#64748b", label: "GND" };
  if (alt < 10000) return { color: "#22d3ee", label: "LOW" };
  if (alt < 25000) return { color: "#34d399", label: "MID" };
  if (alt < 38000) return { color: "#fbbf24", label: "HIGH" };
  return { color: "#f87171", label: "UPPER" };
}

export function utcClock(date = new Date()): string {
  return `${date.toISOString().slice(11, 19)}Z`;
}

export function relative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const diff = Math.max(0, Date.now() - then) / 1000;
  if (diff < 60) return `${Math.round(diff)}s ago`;
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
  return `${Math.round(diff / 86400)}d ago`;
}

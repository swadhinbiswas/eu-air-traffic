import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/utils";

export function Panel({
  className,
  children,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn("panel p-4", className)} {...props}>
      {children}
    </div>
  );
}

export function SectionHeader({
  icon,
  title,
  subtitle,
  right,
}: {
  icon?: ReactNode;
  title: string;
  subtitle?: string;
  right?: ReactNode;
}) {
  return (
    <div className="mb-5 flex items-end justify-between gap-4">
      <div className="flex items-center gap-3">
        {icon && (
          <span className="grid h-9 w-9 place-items-center rounded-lg border border-emerald-500/20 bg-emerald-500/10 text-emerald-400">
            {icon}
          </span>
        )}
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-zinc-100">{title}</h1>
          {subtitle && <p className="text-xs text-zinc-500">{subtitle}</p>}
        </div>
      </div>
      {right}
    </div>
  );
}

export function StatCard({
  label,
  value,
  hint,
  tone = "default",
  icon,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  tone?: "default" | "emerald" | "amber" | "red" | "cyan";
  icon?: ReactNode;
}) {
  const tones: Record<string, string> = {
    default: "text-zinc-100",
    emerald: "text-emerald-400",
    amber: "text-amber-400",
    red: "text-red-400",
    cyan: "text-cyan-400",
  };
  return (
    <div className="panel panel-hover flex flex-col gap-1 p-4">
      <div className="flex items-center justify-between">
        <span className="hud-label">{label}</span>
        {icon && <span className="text-zinc-600">{icon}</span>}
      </div>
      <span className={cn("mono text-2xl font-semibold tabular-nums", tones[tone])}>{value}</span>
      {hint && <span className="text-[11px] text-zinc-500">{hint}</span>}
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-3 py-20 text-sm text-zinc-500">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-zinc-700 border-t-emerald-400" />
      {label}…
    </div>
  );
}

export function ErrorState({ message }: { message: string }) {
  return (
    <div className="panel border-red-500/20 bg-red-500/5 p-6 text-sm text-red-300">
      <p className="font-medium">Could not load data</p>
      <p className="mt-1 text-red-300/70">{message}</p>
    </div>
  );
}

export function EmptyState({ message }: { message: string }) {
  return (
    <div className="panel grid place-items-center p-10 text-sm text-zinc-500">{message}</div>
  );
}

export function ProgressBar({ value, tone = "emerald" }: { value: number; tone?: string }) {
  const width = Math.max(0, Math.min(100, value * 100));
  const colors: Record<string, string> = {
    emerald: "bg-emerald-500",
    amber: "bg-amber-500",
    red: "bg-red-500",
    cyan: "bg-cyan-500",
  };
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/5">
      <div className={cn("h-full rounded-full", colors[tone] ?? colors.emerald)} style={{ width: `${width}%` }} />
    </div>
  );
}

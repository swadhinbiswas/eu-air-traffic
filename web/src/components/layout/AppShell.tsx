import { useEffect, useState, Suspense } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import {
  Activity,
  BarChart3,
  BookOpen,
  Database,
  GitBranch,
  Globe2,
  Radio,
  Sparkles,
  Table2,
  Terminal,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useManifest } from "@/hooks/useBundle";
import { utcClock } from "@/lib/format";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { Spinner } from "@/components/shared";

const NAV = [
  { to: "/", label: "Overview", icon: Globe2, end: true },
  { to: "/analytics", label: "Analytics", icon: BarChart3 },
  { to: "/stories", label: "Stories", icon: Sparkles },
  { to: "/catalog", label: "Catalog", icon: Database },
  { to: "/explorer", label: "Explorer", icon: Table2 },
  { to: "/sql", label: "SQL", icon: Terminal },
  { to: "/ops", label: "Ops", icon: Activity },
  { to: "/docs", label: "Docs", icon: BookOpen },
];

function Clock() {
  const [now, setNow] = useState(utcClock());
  useEffect(() => {
    const id = setInterval(() => setNow(utcClock()), 1000);
    return () => clearInterval(id);
  }, []);
  return <span className="mono text-xs tabular-nums text-zinc-400">{now}</span>;
}

export function AppShell() {
  const location = useLocation();
  const isGlobe = location.pathname === "/";
  const { data: manifest } = useManifest();

  return (
    <div className="flex h-full flex-col overflow-hidden bg-black">
      <header className="z-50 flex h-14 flex-none items-center justify-between border-b border-white/10 bg-black/70 px-4 backdrop-blur-xl">
        <div className="flex items-center gap-3">
          <div className="relative grid h-8 w-8 place-items-center rounded-lg bg-emerald-500/15 text-emerald-400">
            <Radio className="h-4 w-4" />
          </div>
          <div className="leading-tight">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold tracking-tight text-zinc-100">
                EU Air Traffic
              </span>
              <span className="hidden rounded border border-emerald-500/30 bg-emerald-500/10 px-1.5 py-px text-[9px] font-semibold uppercase tracking-wider text-emerald-400 sm:block">
                God&apos;s Eye
              </span>
            </div>
            <span className="hidden text-[10px] text-zinc-500 md:block">
              Medallion · Bronze → Silver → Gold
            </span>
          </div>
        </div>

        <nav className="hidden items-center gap-0.5 rounded-lg border border-white/5 bg-zinc-900/50 p-0.5 lg:flex">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs transition-colors",
                  isActive
                    ? "bg-zinc-700/50 text-white"
                    : "text-zinc-400 hover:text-zinc-100"
                )
              }
            >
              <Icon className="h-3.5 w-3.5" />
              {label}
            </NavLink>
          ))}
        </nav>

        <div className="flex items-center gap-3">
          <span className="hidden items-center gap-1.5 text-xs text-zinc-500 xl:flex">
            <GitBranch className="h-3.5 w-3.5" />
            {manifest ? `${Object.values(manifest.counts).reduce((a, b) => a + b, 0).toLocaleString()} records` : "connecting…"}
          </span>
          <Clock />
          <a
            href="https://github.com/swadhinbiswas/air-traffic"
            target="_blank"
            rel="noreferrer"
            className="grid h-7 w-7 place-items-center rounded-md border border-white/10 text-zinc-500 transition-colors hover:text-zinc-100"
            aria-label="GitHub"
          >
            <Database className="h-3.5 w-3.5" />
          </a>
        </div>
      </header>

      {/* mobile nav */}
      <nav className="flex flex-none items-center gap-1 overflow-x-auto border-b border-white/10 bg-black/60 px-2 py-1.5 lg:hidden">
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              cn(
                "flex flex-none items-center gap-1.5 rounded-md px-2.5 py-1 text-[11px]",
                isActive ? "bg-zinc-700/50 text-white" : "text-zinc-400"
              )
            }
          >
            <Icon className="h-3 w-3" />
            {label}
          </NavLink>
        ))}
      </nav>

      <main
        className={cn(
          "relative flex-1 overflow-hidden",
          !isGlobe && "overflow-y-auto"
        )}
      >
        <ErrorBoundary>
          <Suspense fallback={<Spinner label="Loading view" />}>
            {isGlobe ? (
              <Outlet />
            ) : (
              <div className="mx-auto w-full max-w-7xl px-4 py-6 md:px-6">
                <Outlet />
              </div>
            )}
          </Suspense>
        </ErrorBoundary>
      </main>
    </div>
  );
}

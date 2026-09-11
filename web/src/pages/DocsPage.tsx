import { BookOpen, Database, GitBranch, Layers, Ruler } from "lucide-react";
import { Panel, SectionHeader } from "@/components/shared";
import { useCatalog } from "@/hooks/useBundle";

const LAYERS = [
  {
    name: "Bronze",
    tone: "text-amber-300 border-amber-500/20 bg-amber-500/5",
    body: "Immutable raw payloads landed 1:1 from source APIs as JSONL, consolidated to Parquet partitioned by ingestion date. Nothing is transformed here; schema versions and ingestion metadata are preserved for replay.",
  },
  {
    name: "Silver",
    tone: "text-blue-300 border-blue-500/20 bg-blue-500/5",
    body: "Cleaned, validated and deduplicated records. Timestamps normalised to UTC, natural keys used for idempotent dedup, and every rejected row routed to the Quarantine dead-letter queue with a reason.",
  },
  {
    name: "Gold",
    tone: "text-emerald-300 border-emerald-500/20 bg-emerald-500/5",
    body: "Business-ready marts modelled in dbt (staging → intermediate → marts), tested and documented. Registered as gold_* views in MotherDuck and read live by the dashboard — no static export.",
  },
];

const SOURCES = [
  ["adsb.lol", "Live ADS-B aircraft telemetry (alt, Mach, OAT, wind, squawk) via the VPS collector"],
  ["aviationweather.gov", "METAR + TAF with flight category (VFR/MVFR/IFR/LIFR)"],
  ["Open-Meteo", "Keyless weather forecast: current + 24h hourly per airport"],
  ["OpenSky Network", "Flight movements (arrivals/departures) + positions fallback"],
  ["RainViewer", "Live precipitation radar tiles"],
  ["AviationStack", "Jet-fuel prices"],
  ["OpenFlights / OurAirports", "Airport, route and fleet reference, EU-filtered to 1,658 airports"],
  ["ICAO methodology", "Per-type fuel burn / CO₂ emission factors (live + batch)"],
];

const GLOSSARY: [string, string][] = [
  ["OTP (On-Time Performance)", "Share of flights arriving within 15 minutes of schedule; used as the punctuality threshold network-wide."],
  ["ICAO24", "24-bit hexadecimal transponder address uniquely identifying an aircraft airframe."],
  ["ADS-B", "Automatic Dependent Surveillance–Broadcast; aircraft broadcast position/velocity received by ground stations."],
  ["METAR", "Routine aerodrome weather report (wind, visibility, temperature, pressure)."],
  ["NOTAM", "Notice to Airmen; time-critical operational information about airspace or facilities."],
  ["EU261/2004", "EU regulation on flight delay/cancellation compensation, with weather classed as extraordinary circumstances."],
  ["Star schema", "Dimensional model of fact tables (events) surrounded by dimension tables (context)."],
  ["Medallion architecture", "Layered lakehouse pattern: Bronze (raw) → Silver (validated) → Gold (business-ready)."],
  ["Quarantine / DLQ", "Dead-letter store holding rows that failed validation, with the rejection reason."],
  ["SCD Type 1", "Slowly Changing Dimension strategy that overwrites values in place (no history)."],
  ["Incremental model", "dbt materialisation that only processes new/changed rows on each run."],
  ["Freshness", "Age of the latest record for a source, used to detect stale pipelines."],
];

export function DocsPage() {
  const { data: catalog } = useCatalog();
  const marts = catalog?.lineage.nodes.filter((n) => n.layer === "marts") ?? [];

  return (
    <div className="space-y-6">
      <SectionHeader
        icon={<BookOpen className="h-4 w-4" />}
        title="Documentation"
        subtitle="Architecture, definitions, business glossary and model catalogue"
      />

      <Panel>
        <div className="mb-3 flex items-center gap-2">
          <Layers className="h-4 w-4 text-emerald-400" />
          <h2 className="text-sm font-semibold text-zinc-200">Medallion architecture</h2>
        </div>
        <div className="grid gap-3 md:grid-cols-3">
          {LAYERS.map((l, i) => (
            <div key={l.name} className={`rounded-lg border p-4 ${l.tone}`}>
              <div className="mb-1 flex items-center gap-2">
                <span className="mono text-xs opacity-60">0{i + 1}</span>
                <span className="text-sm font-semibold">{l.name}</span>
              </div>
              <p className="text-xs leading-relaxed text-zinc-400">{l.body}</p>
            </div>
          ))}
        </div>
        <div className="mt-4 rounded-lg border border-white/5 bg-white/[0.02] p-4">
          <div className="hud-label mb-2">End-to-end flow</div>
          <pre className="mono overflow-x-auto text-[11px] leading-relaxed text-zinc-400">
{`VPS collector (24/7) → adsb.lol / OpenSky / aviationweather / Open-Meteo / AviationStack
      → Kafka (eu-positions, eu-flights, eu-weather, eu-fuel, eu-reference)
      → GET /live/snapshot → MapLibre globe / Analytics / Stories
      → GitHub Actions sink → Bronze/Silver Parquet → Hugging Face
      → dbt (staging → intermediate → marts → reports) → MotherDuck (Gold)
      → static bundle + SQL API → Analytics / Catalog / Explorer / SQL / Ops`}
          </pre>
        </div>
        <div className="mt-3 rounded-lg border border-white/5 bg-white/[0.02] p-4">
          <div className="hud-label mb-2">Map stack</div>
          <p className="text-xs leading-relaxed text-zinc-400">
            The globe and all analytical maps use{" "}
            <span className="text-emerald-300">mapcn</span> (MapLibre GL + shadcn/ui) with the
            globe projection over dark CARTO vector tiles. Aircraft render as real rotated
            silhouettes; airports and Open-Meteo stations are clickable vector layers.
          </p>
        </div>
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel>
          <div className="mb-3 flex items-center gap-2">
            <Database className="h-4 w-4 text-cyan-400" />
            <h2 className="text-sm font-semibold text-zinc-200">Gold models</h2>
            <span className="hud-label ml-auto">{marts.length} models</span>
          </div>
          <ul className="space-y-2">
            {marts.map((m) => (
              <li key={m.id} className="rounded-md border border-white/5 bg-white/[0.02] p-3">
                <div className="flex items-center justify-between">
                  <span className="mono text-xs text-zinc-200">{m.id}</span>
                  <span className="rounded bg-white/[0.04] px-1.5 py-px text-[9px] text-zinc-500">
                    {m.materialized}
                  </span>
                </div>
                {m.description && <p className="mt-1 text-[11px] text-zinc-500">{m.description}</p>}
                {!m.description && m.depends_on.length > 0 && (
                  <p className="mt-1 text-[11px] text-zinc-600">
                    depends on {m.depends_on.map((d) => d.replace("source.", "")).join(", ")}
                  </p>
                )}
              </li>
            ))}
            {!marts.length && <li className="text-xs text-zinc-600">No dbt models discovered.</li>}
          </ul>
        </Panel>

        <Panel>
          <div className="mb-3 flex items-center gap-2">
            <Ruler className="h-4 w-4 text-violet-400" />
            <h2 className="text-sm font-semibold text-zinc-200">Business glossary</h2>
          </div>
          <dl className="space-y-2.5">
            {GLOSSARY.map(([term, def]) => (
              <div key={term} className="border-b border-white/[0.03] pb-2.5 last:border-0">
                <dt className="text-xs font-medium text-zinc-200">{term}</dt>
                <dd className="text-[11px] leading-relaxed text-zinc-500">{def}</dd>
              </div>
            ))}
          </dl>
        </Panel>
      </div>

      <Panel>
        <div className="mb-3 flex items-center gap-2">
          <GitBranch className="h-4 w-4 text-emerald-400" />
          <h2 className="text-sm font-semibold text-zinc-200">Data sources</h2>
        </div>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {SOURCES.map(([name, desc]) => (
            <div key={name} className="rounded-md border border-white/5 bg-white/[0.02] p-3">
              <div className="text-xs font-medium text-zinc-200">{name}</div>
              <div className="mt-0.5 text-[11px] text-zinc-500">{desc}</div>
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}

"""Generate a self-contained, offline HTML analytics dashboard.

The dashboard is a single HTML file (no external CDNs, no server runtime
besides the API) that embeds the warehouse data as JSON and renders it with a
small dependency-free JavaScript renderer. It reads directly from the DuckDB
warehouse file and can be regenerated any time the warehouse is rebuilt.

Usage:
    python -m scripts.build_dashboard [--out path/to/dashboard.html]

The FastAPI app serves this file at ``GET /dashboard``.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any

import duckdb

from config.logging import logger
from config.settings import settings


def _rows(con: duckdb.DuckDBPyConnection, sql: str) -> list[dict[str, Any]]:
    try:
        return con.execute(sql).fetch_df().to_dict(orient="records")
    except duckdb.Error:
        return []


def collect_data() -> dict[str, Any]:
    """Query the warehouse for every dashboard panel."""
    if not settings.duckdb_path.exists():
        return {"error": "Warehouse not built. Run `make run` (pipeline) first."}

    with duckdb.connect(str(settings.duckdb_path), read_only=True) as con:
        kpis = _rows(
            con,
            """
            SELECT
                (SELECT COUNT(*) FROM fact_flights) AS total_flights,
                (SELECT COUNT(*) FROM fact_flights WHERE status = 'cancelled') AS cancelled,
                (SELECT ROUND(AVG(delay_minutes), 1) FROM fact_flights WHERE status != 'cancelled')
                    AS avg_delay,
                (SELECT COUNT(*) FROM dim_airport) AS airports,
                (SELECT COUNT(*) FROM dim_airline) AS airlines,
                (SELECT COUNT(*) FROM dim_date) AS days_covered
            """,
        )
        top_airports = _rows(
            con,
            """
            SELECT airport_icao, total_flights, avg_delay_minutes, on_time_rate
            FROM gold_airport_metrics
            ORDER BY total_flights DESC LIMIT 10
            """,
        )
        airlines = _rows(
            con,
            """
            SELECT airline_icao, airline_name, total_flights, avg_delay_minutes, on_time_rate
            FROM gold_airline_rankings
            ORDER BY total_flights DESC LIMIT 10
            """,
        )
        delays = _rows(
            con, "SELECT status, flight_count, avg_delay_minutes FROM gold_delay_analysis"
        )
        weather = _rows(
            con,
            """
            SELECT weather_condition, flight_count, avg_delay_minutes, avg_temperature_c
            FROM gold_weather_impact ORDER BY flight_count DESC LIMIT 8
            """,
        )
        seasonal = _rows(
            con,
            """
            SELECT flight_date, hour_of_day, flight_count, avg_delay_minutes
            FROM gold_seasonal_trends ORDER BY flight_date, hour_of_day
            """,
        )
        fuel = _rows(
            con,
            "SELECT date, region, price_per_litre FROM gold_fuel_price_series ORDER BY date",
        )

    return {
        "generated_at": _dt.datetime.now(_dt.UTC).isoformat(),
        "kpis": kpis[0] if kpis else {},
        "top_airports": top_airports,
        "airlines": airlines,
        "delays": delays,
        "weather": weather,
        "seasonal": seasonal,
        "fuel": fuel,
    }


# ── HTML template (vanilla JS + inline SVG, zero dependencies) ───────────────
_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Air Traffic Analytics — Self-Contained Dashboard</title>
<style>
  :root { --bg:#0f172a; --panel:#1e293b; --panel2:#263449; --text:#e2e8f0;
          --muted:#94a3b8; --accent:#38bdf8; --good:#34d399; --warn:#fbbf24; --bad:#f87171; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; padding: 24px; }
  header { display: flex; align-items: baseline; justify-content: space-between; flex-wrap: wrap; gap: 8px; margin-bottom: 20px; }
  h1 { font-size: 22px; font-weight: 700; }
  h1 span { color: var(--accent); }
  .sub { color: var(--muted); font-size: 13px; }
  .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 14px; margin-bottom: 20px; }
  .kpi { background: var(--panel); border-radius: 12px; padding: 16px; border: 1px solid #334155; }
  .kpi .v { font-size: 26px; font-weight: 800; }
  .kpi .l { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .05em; margin-top: 4px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap: 14px; }
  .panel { background: var(--panel); border-radius: 12px; padding: 16px; border: 1px solid #334155; }
  .panel h2 { font-size: 14px; color: var(--muted); text-transform: uppercase; letter-spacing: .06em; margin-bottom: 12px; }
  .bar-row { display: flex; align-items: center; gap: 10px; margin: 7px 0; }
  .bar-row .label { width: 64px; font-size: 12px; color: var(--muted); text-align: right; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .bar-track { flex: 1; background: var(--panel2); border-radius: 6px; height: 18px; overflow: hidden; }
  .bar-fill { height: 100%; border-radius: 6px; background: linear-gradient(90deg, var(--accent), #818cf8); }
  .bar-val { width: 56px; font-size: 12px; text-align: right; }
  .legend { display: flex; gap: 14px; flex-wrap: wrap; margin-top: 10px; color: var(--muted); font-size: 12px; }
  .dot { display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 5px; }
  canvas { width: 100%; height: 180px; }
  .empty { color: var(--muted); font-size: 13px; padding: 12px; text-align: center; }
  footer { margin-top: 20px; color: var(--muted); font-size: 12px; text-align: center; }
  @media (max-width: 700px){ .grid { grid-template-columns: 1fr; } }
</style>
</head>
<body>
  <header>
    <h1>✈️ Air Traffic <span>Analytics</span></h1>
    <div class="sub">Medallion warehouse · DuckDB + Polars + dbt · generated <span id="gen"></span></div>
  </header>

  <section class="kpis" id="kpis"></section>

  <section class="grid">
    <div class="panel"><h2>Top Airports by Volume</h2><div id="topAirports"></div></div>
    <div class="panel"><h2>Airline Ranking</h2><div id="airlines"></div></div>
    <div class="panel"><h2>Flight Status Breakdown</h2><canvas id="delayChart"></canvas><div class="legend" id="delayLegend"></div></div>
    <div class="panel"><h2>Weather Impact on Delay</h2><div id="weather"></div></div>
    <div class="panel"><h2>Daily Flight Volume & Delay Trend</h2><canvas id="trendChart"></canvas></div>
    <div class="panel"><h2>Fuel Price Per Region</h2><div id="fuel"></div></div>
  </section>

  <footer>Generated by the Air Traffic Platform pipeline — single-file, no external dependencies.</footer>

<script>
const DASH = __DATA__;
document.getElementById('gen').textContent = new Date(DASH.generated_at).toLocaleString();

if (DASH.error) {
  document.body.insertAdjacentHTML('afterbegin', `<div class="empty">${DASH.error}</div>`);
  throw new Error(DASH.error);
}

const fmt = n => n == null ? '—' : Number(n).toLocaleString(undefined, {maximumFractionDigits: 1});

// ── KPI cards ────────────────────────────────────────────────────────────────
const k = DASH.kpis || {};
const kpi = [
  ['Total Flights', fmt(k.total_flights), 'var(--accent)'],
  ['Avg Delay (min)', fmt(k.avg_delay), k.avg_delay > 60 ? 'var(--bad)' : 'var(--warn)'],
  ['Cancelled', fmt(k.cancelled), 'var(--bad)'],
  ['Airports', fmt(k.airports), 'var(--good)'],
  ['Airlines', fmt(k.airlines), 'var(--good)'],
  ['Days Covered', fmt(k.days_covered), 'var(--muted)'],
];
document.getElementById('kpis').innerHTML = kpi.map(([l, v, c]) =>
  `<div class="kpi"><div class="v" style="color:${c}">${v}</div><div class="l">${l}</div></div>`).join('');

// ── Horizontal bars ──────────────────────────────────────────────────────────
function bars(container, rows, labelKey, valueKey, maxValue, extraKey) {
  const el = document.getElementById(container);
  if (!rows || !rows.length) { el.innerHTML = '<div class="empty">No data yet.</div>'; return; }
  const max = maxValue || Math.max(...rows.map(r => Number(r[valueKey]) || 0)) || 1;
  el.innerHTML = rows.map(r => {
    const pct = (Number(r[valueKey]) || 0) / max * 100;
    const extra = extraKey && r[extraKey] != null
      ? `<span class="bar-val">${fmt(r[valueKey])} · ${Number(r[extraKey]*100).toFixed(0)}% OTP</span>`
      : `<span class="bar-val">${fmt(r[valueKey])}</span>`;
    return `<div class="bar-row"><span class="label" title="${r[labelKey] || ''}">${r[labelKey] || ''}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${pct}%"></div></div>${extra}</div>`;
  }).join('');
}

bars('topAirports', DASH.top_airports, 'airport_icao', 'total_flights', null, 'on_time_rate');
bars('airlines', DASH.airlines, 'airline_icao', 'total_flights', null, 'on_time_rate');
bars('weather', DASH.weather, 'weather_condition', 'avg_delay_minutes', null, null);

// ── Donut: flight status ─────────────────────────────────────────────────────
(function donut(){
  const canvas = document.getElementById('delayChart');
  const ctx = canvas.getContext('2d');
  const rows = DASH.delays || [];
  if (!rows.length) return;
  const colors = ['#38bdf8', '#fbbf24', '#f87171', '#34d399'];
  const total = rows.reduce((s, r) => s + r.flight_count, 0);
  const legend = document.getElementById('delayLegend');
  legend.innerHTML = rows.map((r, i) =>
    `<span><span class="dot" style="background:${colors[i % colors.length]}"></span>${r.status}: ${fmt(r.flight_count)}</span>`).join('');

  const cx = canvas.width / 2, cy = canvas.height / 2 + 10, R = 62;
  let angle = -Math.PI / 2;
  rows.forEach((r, i) => {
    const frac = r.flight_count / total;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.arc(cx, cy, R, angle, angle + frac * Math.PI * 2);
    ctx.closePath();
    ctx.fillStyle = colors[i % colors.length];
    ctx.fill();
    angle += frac * Math.PI * 2;
  });
  ctx.fillStyle = '#e2e8f0';
  ctx.font = '700 18px sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText(fmt(total), cx, cy + 6);
})();

// ── Line chart: daily trend ──────────────────────────────────────────────────
(function trend(){
  const canvas = document.getElementById('trendChart');
  const ctx = canvas.getContext('2d');
  const rows = DASH.seasonal || [];
  if (!rows.length) return;
  // aggregate per day
  const byDay = {};
  rows.forEach(r => { byDay[r.flight_date] = (byDay[r.flight_date] || 0) + r.flight_count; });
  const days = Object.keys(byDay).sort().slice(-14);
  const vals = days.map(d => byDay[d]);
  const max = Math.max(...vals) || 1;
  const w = canvas.width, h = canvas.height, pad = 26, bw = (w - pad * 2) / days.length;

  ctx.strokeStyle = 'rgba(148,163,184,0.4)'; ctx.fillStyle = '#94a3b8'; ctx.font = '11px sans-serif';
  for (let i = 0; i <= 3; i++) {
    const y = pad + (h - pad * 2) * i / 3;
    ctx.beginPath(); ctx.moveTo(pad, y); ctx.lineTo(w - pad, y); ctx.stroke();
    ctx.fillText(Math.round(max * (1 - i / 3)), 4, y + 4);
  }
  ctx.beginPath();
  vals.forEach((v, i) => {
    const x = pad + i * bw + bw / 2;
    const y = pad + (h - pad * 2) * (1 - v / max);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.strokeStyle = '#38bdf8'; ctx.lineWidth = 2.5; ctx.lineJoin = 'round'; ctx.stroke();
  ctx.fillStyle = '#94a3b8'; ctx.font = '11px sans-serif'; ctx.textAlign = 'center';
  vals.forEach((v, i) => {
    const x = pad + i * bw + bw / 2;
    ctx.fillText(days[i].slice(5), x, h - 8);
  });
  // area fill
  ctx.lineTo(pad + (vals.length - 1) * bw + bw / 2, h - pad);
  ctx.lineTo(pad + bw / 2, h - pad);
  ctx.closePath();
  const grad = ctx.createLinearGradient(0, pad, 0, h - pad);
  grad.addColorStop(0, 'rgba(56,189,248,0.35)'); grad.addColorStop(1, 'rgba(56,189,248,0)');
  ctx.fillStyle = grad; ctx.fill();
})();

// ── Fuel line chart ──────────────────────────────────────────────────────────
(function fuel(){
  const el = document.getElementById('fuel');
  const rows = DASH.fuel || [];
  if (!rows.length) { el.innerHTML = '<div class="empty">No data yet.</div>'; return; }
  const series = {};
  rows.forEach(r => {
    const key = r.region || 'EU';
    (series[key] = series[key] || []).push({ d: r.date, p: r.price_per_litre });
  });
  const out = Object.entries(series).map(([region, pts]) => {
    const latest = pts[pts.length - 1];
    return `<div class="bar-row"><span class="label" title="${region}">${region}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${Math.min(100, latest.p * 30)}%"></div></div>
      <span class="bar-val">€${fmt(latest.p)}</span></div>`;
  });
  el.innerHTML = out.join('');
})();
</script>
</body>
</html>
"""


def build_dashboard(out: Path | None = None) -> Path:
    """Query the warehouse and write the self-contained dashboard HTML."""
    data = collect_data()
    html = _TEMPLATE.replace("__DATA__", json.dumps(data))
    out = out or settings.warehouse_dir / "dashboard.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    logger.info("[dashboard] written → %s (%d bytes)", out, len(html))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the offline HTML dashboard.")
    parser.add_argument("--out", type=Path, default=None, help="Output HTML path")
    args = parser.parse_args()
    path = build_dashboard(args.out)
    print(f"Dashboard generated at {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";

const PALETTE = ["#34d399", "#22d3ee", "#60a5fa", "#a78bfa", "#fbbf24", "#fb7185", "#f97316", "#4ade80"];

const AXIS = { stroke: "#52525b", fontSize: 10, tickLine: false, axisLine: false };
const GRID = { stroke: "rgba(255,255,255,0.06)", strokeDasharray: "3 3" };

function NoData({ height, label = "No data in this window" }: { height: number; label?: string }) {
  return (
    <div
      className="grid place-items-center rounded-md border border-dashed border-white/10 text-xs text-zinc-600"
      style={{ height }}
    >
      {label}
    </div>
  );
}

const TOOLTIP_STYLE = {
  contentStyle: {
    background: "rgba(9,11,18,0.95)",
    border: "1px solid rgba(255,255,255,0.1)",
    borderRadius: "8px",
    fontSize: "12px",
    color: "#e4e4e7",
  },
  labelStyle: { color: "#a1a1aa" },
  itemStyle: { color: "#e4e4e7" },
};

export function BarSeries({
  data,
  x,
  y,
  height = 220,
  color = "#34d399",
}: {
  data: Array<Record<string, string | number>>;
  x: string;
  y: string;
  height?: number;
  color?: string;
}) {
  if (!data.length) return <NoData height={height} />;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -18 }}>
        <CartesianGrid {...GRID} vertical={false} />
        <XAxis dataKey={x} {...AXIS} interval={0} angle={-25} textAnchor="end" height={48} />
        <YAxis {...AXIS} width={46} />
        <Tooltip {...TOOLTIP_STYLE} cursor={{ fill: "rgba(255,255,255,0.04)" }} />
        <Bar dataKey={y} radius={[3, 3, 0, 0]}>
          {data.map((_, i) => (
            <Cell key={i} fill={PALETTE[i % PALETTE.length] ?? color} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

export function LineSeries({
  data,
  x,
  y,
  height = 220,
  color = "#22d3ee",
  area = false,
}: {
  data: Array<Record<string, string | number>>;
  x: string;
  y: string;
  height?: number;
  color?: string;
  area?: boolean;
}) {
  if (!data.length) return <NoData height={height} />;
  if (area) {
    return (
      <ResponsiveContainer width="100%" height={height}>
        <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -18 }}>
          <defs>
            <linearGradient id={`grad-${y}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.45} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid {...GRID} vertical={false} />
          <XAxis dataKey={x} {...AXIS} />
          <YAxis {...AXIS} width={46} />
          <Tooltip {...TOOLTIP_STYLE} />
          <Area type="monotone" dataKey={y} stroke={color} strokeWidth={2} fill={`url(#grad-${y})`} />
        </AreaChart>
      </ResponsiveContainer>
    );
  }
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -18 }}>
        <CartesianGrid {...GRID} vertical={false} />
        <XAxis dataKey={x} {...AXIS} />
        <YAxis {...AXIS} width={46} />
        <Tooltip {...TOOLTIP_STYLE} />
        <Line type="monotone" dataKey={y} stroke={color} strokeWidth={2} dot={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function DonutSeries({
  data,
  nameKey = "label",
  valueKey = "value",
  height = 220,
}: {
  data: Array<Record<string, string | number>>;
  nameKey?: string;
  valueKey?: string;
  height?: number;
}) {
  if (!data.length) return <NoData height={height} />;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <PieChart>
        <Tooltip {...TOOLTIP_STYLE} />
        <Pie
          data={data}
          dataKey={valueKey}
          nameKey={nameKey}
          innerRadius="55%"
          outerRadius="80%"
          paddingAngle={2}
          stroke="none"
        >
          {data.map((_, i) => (
            <Cell key={i} fill={PALETTE[i % PALETTE.length]} />
          ))}
        </Pie>
      </PieChart>
    </ResponsiveContainer>
  );
}

export function ScatterSeries({
  data,
  x,
  y,
  height = 240,
  color = "#a78bfa",
}: {
  data: Array<Record<string, string | number>>;
  x: string;
  y: string;
  height?: number;
  color?: string;
}) {
  if (!data.length) return <NoData height={height} />;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ScatterChart margin={{ top: 8, right: 8, bottom: 0, left: -18 }}>
        <CartesianGrid {...GRID} />
        <XAxis dataKey={x} name={x} {...AXIS} type="number" />
        <YAxis dataKey={y} name={y} {...AXIS} width={46} type="number" />
        <ZAxis range={[40, 120]} />
        <Tooltip {...TOOLTIP_STYLE} cursor={{ strokeDasharray: "3 3" }} />
        <Scatter data={data} fill={color} fillOpacity={0.75} />
      </ScatterChart>
    </ResponsiveContainer>
  );
}

export { PALETTE };

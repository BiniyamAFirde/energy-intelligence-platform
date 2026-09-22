import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

export interface LineSeriesConfig {
  dataKey: string
  name: string
  color: string
  dashed?: boolean
}

interface TimeSeriesChartProps<T extends object> {
  data: T[]
  xKey: string
  lines: LineSeriesConfig[]
  height?: number
  xFormatter?: (value: string) => string
  yFormatter?: (value: number) => string
  highlight?: { x: string | number; y: number; label: string }
}

export function TimeSeriesChart<T extends object>({
  data,
  xKey,
  lines,
  height = 280,
  xFormatter,
  yFormatter,
  highlight,
}: TimeSeriesChartProps<T>) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
        <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey={xKey}
          tickFormatter={xFormatter}
          stroke="var(--color-text-tertiary)"
          tick={{ fontSize: 11 }}
          minTickGap={24}
        />
        <YAxis stroke="var(--color-text-tertiary)" tick={{ fontSize: 11 }} tickFormatter={yFormatter} width={48} />
        <Tooltip
          contentStyle={{
            background: 'var(--color-bg-elevated)',
            border: '1px solid var(--color-border-strong)',
            borderRadius: 8,
            fontSize: 12,
          }}
          labelFormatter={(value) => (xFormatter ? xFormatter(String(value)) : String(value))}
          formatter={(value, name) => [
            yFormatter && typeof value === 'number' ? yFormatter(value) : String(value ?? '—'),
            String(name),
          ]}
        />
        {lines.length > 1 && <Legend wrapperStyle={{ fontSize: 12 }} />}
        {lines.map((line) => (
          <Line
            key={line.dataKey}
            type="monotone"
            dataKey={line.dataKey}
            name={line.name}
            stroke={line.color}
            strokeWidth={2}
            strokeDasharray={line.dashed ? '5 4' : undefined}
            dot={false}
            connectNulls
            isAnimationActive={false}
          />
        ))}
        {highlight && (
          <ReferenceDot
            x={highlight.x}
            y={highlight.y}
            r={6}
            fill="var(--color-severity-high)"
            stroke="none"
            label={{ value: highlight.label, position: 'top', fill: 'var(--color-severity-high)', fontSize: 11 }}
          />
        )}
      </LineChart>
    </ResponsiveContainer>
  )
}

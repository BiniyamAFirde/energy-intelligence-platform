import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

export interface BarSeriesConfig {
  dataKey: string
  name: string
  color: string
  stackId?: string
}

interface CategoryBarChartProps<T extends object> {
  data: T[]
  categoryKey: string
  bars: BarSeriesConfig[]
  height?: number
  /** 'horizontal-bars' puts categories on the Y axis (long labels read better);
   *  'vertical-bars' (default) is the usual upright bar chart. */
  orientation?: 'vertical-bars' | 'horizontal-bars'
  valueFormatter?: (value: number) => string
  onCategoryClick?: (category: string) => void
}

export function CategoryBarChart<T extends object>({
  data,
  categoryKey,
  bars,
  height = 280,
  orientation = 'vertical-bars',
  valueFormatter,
  onCategoryClick,
}: CategoryBarChartProps<T>) {
  const isHorizontal = orientation === 'horizontal-bars'

  function handleBarClick(_entry: unknown, index: number) {
    if (!onCategoryClick) return
    const row = data[index] as Record<string, unknown> | undefined
    if (row) onCategoryClick(String(row[categoryKey]))
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart
        data={data}
        layout={isHorizontal ? 'vertical' : 'horizontal'}
        margin={{ top: 8, right: 16, left: isHorizontal ? 8 : 0, bottom: 8 }}
      >
        <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" horizontal={!isHorizontal} vertical={isHorizontal} />
        {isHorizontal ? (
          <>
            <XAxis type="number" stroke="var(--color-text-tertiary)" tick={{ fontSize: 11 }} tickFormatter={valueFormatter} />
            <YAxis
              type="category"
              dataKey={categoryKey}
              stroke="var(--color-text-tertiary)"
              tick={{ fontSize: 11 }}
              width={140}
            />
          </>
        ) : (
          <>
            <XAxis dataKey={categoryKey} stroke="var(--color-text-tertiary)" tick={{ fontSize: 11 }} minTickGap={16} />
            <YAxis stroke="var(--color-text-tertiary)" tick={{ fontSize: 11 }} tickFormatter={valueFormatter} width={48} />
          </>
        )}
        <Tooltip
          contentStyle={{
            background: 'var(--color-bg-elevated)',
            border: '1px solid var(--color-border-strong)',
            borderRadius: 8,
            fontSize: 12,
          }}
          formatter={(value, name) => [
            valueFormatter && typeof value === 'number' ? valueFormatter(value) : String(value ?? '—'),
            String(name),
          ]}
          cursor={{ fill: 'var(--color-bg-card-hover)' }}
        />
        {bars.length > 1 && <Legend wrapperStyle={{ fontSize: 12 }} />}
        {bars.map((bar) => (
          <Bar
            key={bar.dataKey}
            dataKey={bar.dataKey}
            name={bar.name}
            fill={bar.color}
            stackId={bar.stackId}
            isAnimationActive={false}
            radius={isHorizontal ? [0, 3, 3, 0] : [3, 3, 0, 0]}
            cursor={onCategoryClick ? 'pointer' : undefined}
            onClick={onCategoryClick ? handleBarClick : undefined}
          />
        ))}
      </BarChart>
    </ResponsiveContainer>
  )
}

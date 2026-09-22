import { useMemo, useState } from 'react'
import { getBuildingForecast } from '../api/forecast'
import type { ForecastPoint } from '../api/types'
import { BuildingSelector } from '../components/BuildingSelector'
import { TimeSeriesChart } from '../components/charts/TimeSeriesChart'
import { DataTable, type DataTableColumn } from '../components/DataTable'
import { DateRangePicker } from '../components/DateRangePicker'
import { KpiCard, KpiCardSkeleton } from '../components/KpiCard'
import { Panel } from '../components/Panel'
import { EmptyState, ErrorState, LoadingState } from '../components/StateViews'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import { useApi } from '../hooks/useApi'
import { formatDateTime, formatKwh, formatShortDate } from '../lib/format'
import './Forecasting.css'

// Phase 7's Random Forest predictions were backfilled only over the
// VALIDATION+TEST window (2017-07-01 .. 2018-01-01) -- see
// docs/anomalies.md's Limitations section. Defaulting the range picker
// here avoids a confusing empty chart on first load.
const DEFAULT_START = '2017-07-01'
const DEFAULT_END = '2017-07-14'

export function Forecasting() {
  useDocumentTitle('Forecasting')
  const [buildingId, setBuildingId] = useState<number | undefined>(1)
  const [range, setRange] = useState<{ start: string | undefined; end: string | undefined }>({
    start: DEFAULT_START,
    end: DEFAULT_END,
  })

  const forecast = useApi(
    () =>
      buildingId
        ? getBuildingForecast(buildingId, {
            start: range.start ? `${range.start}T00:00:00` : undefined,
            end: range.end ? `${range.end}T23:59:59` : undefined,
            limit: 2000,
          })
        : Promise.resolve([]),
    [buildingId, range.start, range.end],
  )

  const stats = useMemo(() => computeStats(forecast.data ?? []), [forecast.data])

  const columns: DataTableColumn<ForecastPoint>[] = [
    { key: 'target_ts', header: 'Target time', render: (p) => formatDateTime(p.target_ts) },
    { key: 'horizon', header: 'Horizon', render: (p) => (p.horizon !== null ? `+${p.horizon}h` : '—'), align: 'right' },
    { key: 'generated_at', header: 'Forecast generated', render: (p) => formatDateTime(p.generated_at) },
    { key: 'predicted', header: 'Forecast', render: (p) => formatKwh(p.predicted_kwh), align: 'right' },
    { key: 'actual', header: 'Actual', render: (p) => formatKwh(p.actual_kwh), align: 'right' },
    { key: 'residual', header: 'Residual', render: (p) => formatKwh(p.residual), align: 'right' },
  ]

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Forecasting</h1>
          <p>
            Day-ahead hourly forecasts from Phase 7's Random Forest model, backtested against real historical
            consumption. These are <strong>historical, backfilled predictions</strong> -- each point was generated
            from data available at its own origin hour, not computed live just now.
          </p>
        </div>
      </div>

      <div className="controls-row">
        <label className="control-label-inline">
          Building
          <BuildingSelector value={buildingId} onChange={setBuildingId} />
        </label>
        <DateRangePicker start={range.start} end={range.end} onChange={setRange} />
      </div>

      <div className="kpi-grid">
        {forecast.loading ? (
          Array.from({ length: 4 }).map((_, i) => <KpiCardSkeleton key={i} />)
        ) : (
          <>
            <KpiCard label="Points shown" value={String(stats.count)} />
            <KpiCard label="Mean |residual|" value={stats.meanAbsResidual !== null ? formatKwh(stats.meanAbsResidual) : '—'} />
            <KpiCard label="Model" value={stats.model ?? '—'} sublabel={stats.modelVersion ? `version ${stats.modelVersion}` : undefined} />
            <KpiCard label="Horizon range" value={stats.horizonRange ?? '—'} sublabel="Hours ahead of origin" />
          </>
        )}
      </div>

      <Panel title="Forecast vs. actual" subtitle="Dashed = forecast, solid = actual">
        {forecast.loading ? (
          <LoadingState />
        ) : forecast.error ? (
          <ErrorState error={forecast.error} />
        ) : !forecast.data || forecast.data.length === 0 ? (
          <EmptyState message="No forecast data in this range. Predictions were backfilled for 2017-07-01 through 2018-01-01 only." />
        ) : (
          <TimeSeriesChart
            data={forecast.data}
            xKey="target_ts"
            xFormatter={formatShortDate}
            yFormatter={(v) => v.toLocaleString()}
            lines={[
              { dataKey: 'actual_kwh', name: 'Actual', color: 'var(--color-actual)' },
              { dataKey: 'predicted_kwh', name: 'Forecast', color: 'var(--color-forecast)', dashed: true },
            ]}
          />
        )}
      </Panel>

      <Panel title="Forecast points">
        {forecast.loading ? (
          <LoadingState />
        ) : (
          <DataTable
            columns={columns}
            rows={(forecast.data ?? []).slice(0, 200)}
            rowKey={(p) => `${p.sensor_id}-${p.target_ts}`}
            emptyMessage="No forecast points in this range."
          />
        )}
      </Panel>

      <Panel title="Methodology">
        <div className="methodology">
          <p>
            <strong>Day-ahead, direct multi-horizon.</strong> Each forecast is made from a local-midnight origin,
            predicting the entire next 24 hours (horizons 1-24) in one pass -- horizon is an explicit input feature,
            not a recursive 24-step chain.
          </p>
          <p>
            <strong>Random Forest, selected over seasonal-naive baselines.</strong> Ridge, Random Forest, and
            HistGradientBoosting were each evaluated against 24h- and 168h-seasonal-naive baselines. Random Forest had
            the lowest validation RMSE among models that beat both baselines, cutting RMSE from the best baseline's
            65.9 kWh to 26.1 kWh on validation.
          </p>
          <p>
            <strong>Leakage-safe chronological split.</strong> Train (2016-01 -- 2017-06) / validation (2017-07 --
            09) / test (2017-10 -- 12) are split by forecast <em>origin</em>, not target timestamp, so a single
            24-hour forecast never straddles two splits. Lag and rolling features use only data at-or-before their
            own origin, verified by a dedicated leakage test suite.
          </p>
        </div>
      </Panel>
    </div>
  )
}

function computeStats(points: ForecastPoint[]) {
  const withResidual = points.filter((p) => p.residual !== null) as (ForecastPoint & { residual: number })[]
  const meanAbsResidual =
    withResidual.length > 0
      ? withResidual.reduce((sum, p) => sum + Math.abs(p.residual), 0) / withResidual.length
      : null
  const horizons = points.map((p) => p.horizon).filter((h): h is number => h !== null)
  const horizonRange = horizons.length > 0 ? `${Math.min(...horizons)}-${Math.max(...horizons)}h` : null

  return {
    count: points.length,
    meanAbsResidual,
    model: points[0]?.model_name ?? null,
    modelVersion: points[0]?.model_version ?? null,
    horizonRange,
  }
}

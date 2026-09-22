import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { compareBuildings, getBuildingProfile } from '../api/analytics'
import { getAlertsSummary } from '../api/alerts'
import { listSensors } from '../api/sensors'
import { getBuildingEnergyAggregate } from '../api/energy'
import type { Granularity } from '../api/types'
import { BuildingSelector } from '../components/BuildingSelector'
import { CategoryBarChart } from '../components/charts/CategoryBarChart'
import { TimeSeriesChart } from '../components/charts/TimeSeriesChart'
import { KpiCard, KpiCardSkeleton } from '../components/KpiCard'
import { Panel } from '../components/Panel'
import { EmptyState, ErrorState, LoadingState } from '../components/StateViews'
import { useAnomalyMonthlyTrend } from '../hooks/useAnomalyMonthlyTrend'
import { useApi } from '../hooks/useApi'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import { formatKwh, formatNumber, formatShortDate } from '../lib/format'

const GRANULARITIES: Granularity[] = ['hourly', 'daily', 'weekly', 'monthly']
const DETECTOR_COLORS: Record<string, string> = {
  data_quality: 'var(--color-detector-a)',
  behavioral: 'var(--color-detector-b)',
  forecast_residual: 'var(--color-detector-c)',
  isolation_forest: 'var(--color-detector-d)',
}
const DETECTOR_LABELS: Record<string, string> = {
  data_quality: 'Data quality',
  behavioral: 'Behavioral',
  forecast_residual: 'Forecast residual',
  isolation_forest: 'Isolation Forest',
}

export function Dashboard() {
  useDocumentTitle('Dashboard')
  const navigate = useNavigate()
  const [granularity, setGranularity] = useState<Granularity>('daily')
  const [selectedBuildingId, setSelectedBuildingId] = useState<number | undefined>(undefined)

  const compare = useApi(() => compareBuildings(), [])
  const sensorsTotal = useApi(() => listSensors({ limit: 1 }), [])
  const alertsSummary = useApi(() => getAlertsSummary(), [])
  const anomalyTrend = useAnomalyMonthlyTrend()

  const sortedByConsumption = useMemo(
    () => (compare.data ? [...compare.data].sort((a, b) => (b.total_kwh ?? 0) - (a.total_kwh ?? 0)) : []),
    [compare.data],
  )
  const topBuildings = sortedByConsumption.slice(0, 12)

  // The "currently inspected" building (Energy Trend + Consumption Profile
  // panels) defaults to the top consumer once comparison data loads, but
  // stays a user override once one is made. Derived directly during render
  // rather than synced via a useEffect+setState -- that pattern causes an
  // extra render pass for no benefit here since the value is a pure
  // function of existing state.
  const effectiveBuildingId = selectedBuildingId ?? sortedByConsumption[0]?.building_id

  const energyTrend = useApi(
    () =>
      effectiveBuildingId
        ? getBuildingEnergyAggregate(effectiveBuildingId, granularity, { limit: 800 })
        : Promise.resolve([]),
    [effectiveBuildingId, granularity],
  )
  const profile = useApi(
    () => (effectiveBuildingId ? getBuildingProfile(effectiveBuildingId) : Promise.resolve(null)),
    [effectiveBuildingId],
  )

  const totalKwh = compare.data?.reduce((sum, b) => sum + (b.total_kwh ?? 0), 0)
  const avgKwh = compare.data && compare.data.length > 0
    ? compare.data.reduce((sum, b) => sum + (b.mean_kwh ?? 0), 0) / compare.data.length
    : undefined

  const selectedBuildingCode = sortedByConsumption.find((b) => b.building_id === effectiveBuildingId)?.building_code

  const detectorBarData = alertsSummary.data
    ? Object.entries(alertsSummary.data.counts_by_method).map(([method, count]) => ({
        method: DETECTOR_LABELS[method] ?? method,
        count,
        fill: DETECTOR_COLORS[method] ?? 'var(--color-accent)',
      }))
    : []

  const severityBarData = alertsSummary.data
    ? (['high', 'medium', 'low'] as const)
        .filter((s) => alertsSummary.data!.counts_by_severity[s] !== undefined)
        .map((s) => ({ severity: s, count: alertsSummary.data!.counts_by_severity[s] ?? 0 }))
    : []

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Dashboard</h1>
          <p>Portfolio overview across the loaded BDG2 subset -- 3 sites, 60 buildings, 2016-2018.</p>
        </div>
      </div>

      <div className="kpi-grid">
        {compare.loading || !compare.data ? (
          Array.from({ length: 7 }).map((_, i) => <KpiCardSkeleton key={i} />)
        ) : (
          <>
            <KpiCard label="Total consumption" value={formatKwh(totalKwh, 0)} sublabel="All buildings, full history" />
            <KpiCard label="Avg. building load" value={formatKwh(avgKwh)} sublabel="Mean across buildings" />
            <KpiCard label="Active buildings" value={formatNumber(compare.data.length)} />
            <KpiCard
              label="Active sensors"
              value={sensorsTotal.data ? formatNumber(sensorsTotal.data.total) : '—'}
            />
            <KpiCard
              label="Anomalies"
              value={alertsSummary.data ? formatNumber(alertsSummary.data.total_alerts) : '—'}
              sublabel={
                alertsSummary.data ? `${formatNumber(alertsSummary.data.unresolved_alerts)} unresolved` : undefined
              }
            />
            <KpiCard
              label="High-severity"
              value={alertsSummary.data ? formatNumber(alertsSummary.data.counts_by_severity.high ?? 0) : '—'}
              accent="danger"
            />
            <KpiCard label="Forecast horizon" value="24h" sublabel="Day-ahead, Random Forest" />
          </>
        )}
      </div>

      <div className="panel-grid-2">
        <Panel
          title="Energy trend"
          subtitle={selectedBuildingCode ? `Building ${selectedBuildingCode}` : undefined}
          actions={
            <div className="controls-row">
              <BuildingSelector value={effectiveBuildingId} onChange={setSelectedBuildingId} />
              <select
                className="control-select"
                value={granularity}
                onChange={(e) => setGranularity(e.target.value as Granularity)}
                aria-label="Aggregation granularity"
              >
                {GRANULARITIES.map((g) => (
                  <option key={g} value={g}>
                    {g}
                  </option>
                ))}
              </select>
            </div>
          }
        >
          {energyTrend.loading ? (
            <LoadingState />
          ) : energyTrend.error ? (
            <ErrorState error={energyTrend.error} />
          ) : !energyTrend.data || energyTrend.data.length === 0 ? (
            <EmptyState message="No consumption data for this building yet." />
          ) : (
            <TimeSeriesChart
              data={energyTrend.data}
              xKey="period_start"
              xFormatter={formatShortDate}
              yFormatter={(v) => v.toLocaleString()}
              lines={[{ dataKey: 'total_kwh', name: 'Total kWh', color: 'var(--color-actual)' }]}
            />
          )}
        </Panel>

        <Panel title="Top buildings by consumption" subtitle="Click a bar to inspect that building">
          {compare.loading ? (
            <LoadingState />
          ) : compare.error ? (
            <ErrorState error={compare.error} />
          ) : (
            <CategoryBarChart
              data={topBuildings}
              categoryKey="building_code"
              orientation="horizontal-bars"
              height={340}
              bars={[{ dataKey: 'total_kwh', name: 'Total kWh', color: 'var(--color-accent)' }]}
              valueFormatter={(v) => v.toLocaleString()}
              onCategoryClick={(buildingCode) => {
                const row = topBuildings.find((b) => b.building_code === buildingCode)
                if (row) navigate(`/buildings/${row.building_id}`)
              }}
            />
          )}
        </Panel>
      </div>

      <div className="panel-grid-2">
        <Panel
          title="Consumption profile"
          subtitle={selectedBuildingCode ? `Building ${selectedBuildingCode}, hour of day (local time)` : undefined}
        >
          {profile.loading ? (
            <LoadingState />
          ) : profile.error ? (
            <ErrorState error={profile.error} />
          ) : !profile.data ? (
            <LoadingState />
          ) : (
            <CategoryBarChart
              data={profile.data.hour_of_day}
              categoryKey="hour_of_day"
              bars={[{ dataKey: 'mean_kwh', name: 'Mean kWh', color: 'var(--color-accent)' }]}
              valueFormatter={(v) => v.toFixed(1)}
            />
          )}
        </Panel>

        <Panel title="Anomalies by detector &amp; severity" actions={<Link to="/anomalies">View all &rarr;</Link>}>
          {alertsSummary.loading ? (
            <LoadingState />
          ) : alertsSummary.error ? (
            <ErrorState error={alertsSummary.error} />
          ) : (
            <div className="panel-subgrid-2">
              <CategoryBarChart
                data={detectorBarData}
                categoryKey="method"
                height={220}
                orientation="horizontal-bars"
                bars={[{ dataKey: 'count', name: 'Alerts', color: 'var(--color-accent)' }]}
                valueFormatter={(v) => v.toLocaleString()}
              />
              <CategoryBarChart
                data={severityBarData}
                categoryKey="severity"
                height={220}
                bars={[{ dataKey: 'count', name: 'Alerts', color: 'var(--color-severity-medium)' }]}
                valueFormatter={(v) => v.toLocaleString()}
              />
            </div>
          )}
        </Panel>
      </div>

      <Panel title="Anomaly trend" subtitle="Monthly alert volume, real production detection run">
        {anomalyTrend.error ? (
          <ErrorState error={anomalyTrend.error} />
        ) : !anomalyTrend.data ? (
          <LoadingState />
        ) : (
          <CategoryBarChart
            data={anomalyTrend.data}
            categoryKey="month"
            bars={[{ dataKey: 'count', name: 'Alerts', color: 'var(--color-accent)' }]}
            valueFormatter={(v) => v.toLocaleString()}
          />
        )}
      </Panel>
    </div>
  )
}

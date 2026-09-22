import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { listBuildingAlerts } from '../api/alerts'
import { getBuildingAnalyticsSummary, getBuildingPeaks, getBuildingProfile } from '../api/analytics'
import { getBuilding } from '../api/buildings'
import { getBuildingEnergyAggregate } from '../api/energy'
import type { Alert, Building, Granularity } from '../api/types'
import { DetectorBadge, ResolvedBadge, SeverityBadge } from '../components/Badges'
import { CategoryBarChart } from '../components/charts/CategoryBarChart'
import { TimeSeriesChart } from '../components/charts/TimeSeriesChart'
import { DataTable, type DataTableColumn } from '../components/DataTable'
import { DateRangePicker } from '../components/DateRangePicker'
import { KpiCard, KpiCardSkeleton } from '../components/KpiCard'
import { MetadataGrid } from '../components/MetadataGrid'
import { Panel } from '../components/Panel'
import { EmptyState, ErrorState, LoadingState } from '../components/StateViews'
import { useApi } from '../hooks/useApi'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import { formatDateTime, formatKwh, formatNumber, formatPercent, formatShortDate } from '../lib/format'
import './BuildingDetail.css'

const GRANULARITIES: Granularity[] = ['hourly', 'daily', 'weekly', 'monthly']

export function BuildingDetail() {
  const { buildingId: buildingIdParam } = useParams()
  const buildingId = Number(buildingIdParam)
  const navigate = useNavigate()

  const [granularity, setGranularity] = useState<Granularity>('daily')
  const [range, setRange] = useState<{ start: string | undefined; end: string | undefined }>({
    start: undefined,
    end: undefined,
  })

  const building = useApi(() => getBuilding(buildingId), [buildingId])
  useDocumentTitle(building.data?.building_code ?? 'Building')
  const summary = useApi(() => getBuildingAnalyticsSummary(buildingId), [buildingId])
  const peaks = useApi(() => getBuildingPeaks(buildingId, 1), [buildingId])
  const profile = useApi(() => getBuildingProfile(buildingId), [buildingId])
  const alertsTotal = useApi(() => listBuildingAlerts(buildingId, { limit: 1 }), [buildingId])
  const alertsUnresolved = useApi(() => listBuildingAlerts(buildingId, { limit: 1, resolved: false }), [buildingId])
  const recentAlerts = useApi(
    () => listBuildingAlerts(buildingId, { limit: 20 }),
    [buildingId],
  )
  const trend = useApi(
    () =>
      getBuildingEnergyAggregate(buildingId, granularity, {
        start: range.start ? `${range.start}T00:00:00` : undefined,
        end: range.end ? `${range.end}T23:59:59` : undefined,
        limit: 1200,
      }),
    [buildingId, granularity, range.start, range.end],
  )

  if (Number.isNaN(buildingId)) {
    return <ErrorState error={new Error('Invalid building id.')} />
  }

  if (building.error) {
    return <ErrorState error={building.error} />
  }

  const alertColumns: DataTableColumn<Alert>[] = [
    { key: 'ts', header: 'Time', render: (a) => formatDateTime(a.ts) },
    { key: 'method', header: 'Detector', render: (a) => <DetectorBadge method={a.method} /> },
    { key: 'anomaly_type', header: 'Type', render: (a) => a.anomaly_type },
    { key: 'severity', header: 'Severity', render: (a) => <SeverityBadge severity={a.severity} /> },
    {
      key: 'value',
      header: 'Actual / expected',
      render: (a) => `${formatKwh(a.actual_value)} / ${formatKwh(a.expected_value)}`,
    },
    { key: 'resolved', header: 'Status', render: (a) => <ResolvedBadge resolved={a.resolved} /> },
  ]

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <Link to="/" className="back-link">
            &larr; Dashboard
          </Link>
          <h1>{building.loading ? 'Loading...' : building.data?.building_code}</h1>
          {building.data && (
            <p>
              {building.data.primary_use ?? 'Unknown use'} &middot; {formatNumber(building.data.area_sqm ?? null)} m&sup2;
              &middot; Site {building.data.site_id}
            </p>
          )}
        </div>
      </div>

      <div className="kpi-grid">
        {summary.loading || !summary.data ? (
          Array.from({ length: 5 }).map((_, i) => <KpiCardSkeleton key={i} />)
        ) : (
          <>
            <KpiCard label="Total consumption" value={formatKwh(summary.data.total_kwh, 0)} />
            <KpiCard label="Mean hourly" value={formatKwh(summary.data.mean_kwh)} />
            <KpiCard
              label="Peak demand"
              value={peaks.data ? formatKwh(peaks.data.peak_kwh) : '—'}
              sublabel={peaks.data ? formatDateTime(peaks.data.peak_timestamp) : undefined}
            />
            <KpiCard
              label="Missing observations"
              value={formatNumber(summary.data.missing_observations)}
              sublabel={formatPercent((summary.data.missing_observations / summary.data.total_observations) * 100)}
            />
            <KpiCard
              label="Anomalies"
              value={alertsTotal.data ? formatNumber(alertsTotal.data.total) : '—'}
              sublabel={alertsUnresolved.data ? `${formatNumber(alertsUnresolved.data.total)} unresolved` : undefined}
              accent={alertsUnresolved.data && alertsUnresolved.data.total > 0 ? 'danger' : 'default'}
            />
          </>
        )}
      </div>

      <Panel title="Building metadata">
        {building.loading || !building.data ? (
          <LoadingState />
        ) : (
          <BuildingMetadataGrid building={building.data} />
        )}
      </Panel>

      <Panel
        title="Historical consumption"
        actions={
          <div className="controls-row">
            <DateRangePicker start={range.start} end={range.end} onChange={setRange} />
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
        {trend.loading ? (
          <LoadingState />
        ) : trend.error ? (
          <ErrorState error={trend.error} />
        ) : !trend.data || trend.data.length === 0 ? (
          <EmptyState message="No consumption data in this range." />
        ) : (
          <TimeSeriesChart
            data={trend.data}
            xKey="period_start"
            xFormatter={formatShortDate}
            yFormatter={(v) => v.toLocaleString()}
            lines={[{ dataKey: 'total_kwh', name: 'Total kWh', color: 'var(--color-actual)' }]}
          />
        )}
      </Panel>

      <Panel title="Hourly profile" subtitle="Mean consumption by hour of day, local time">
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

      <Panel title="Recent anomalies" subtitle="Most recent 20, all detectors" actions={<Link to="/anomalies">View all &rarr;</Link>}>
        {recentAlerts.loading ? (
          <LoadingState />
        ) : recentAlerts.error ? (
          <ErrorState error={recentAlerts.error} />
        ) : (
          <DataTable
            columns={alertColumns}
            rows={recentAlerts.data?.items ?? []}
            rowKey={(a) => a.id}
            onRowClick={(a) => navigate(`/anomalies/${a.id}`)}
            emptyMessage="No anomalies detected for this building."
          />
        )}
      </Panel>
    </div>
  )
}

function BuildingMetadataGrid({ building }: { building: Building }) {
  const fields: [string, string | number | null][] = [
    ['Sub-use', building.sub_primary_use],
    ['Industry', building.industry],
    ['Sub-industry', building.subindustry],
    ['Year built', building.year_built],
    ['Floors', building.number_of_floors],
    ['Occupants', building.occupants],
    ['Heating type', building.heating_type],
    ['LEED level', building.leed_level],
    ['Energy Star rating', building.energy_star_rating],
    ['Latitude', building.latitude],
    ['Longitude', building.longitude],
  ]
  const populated = fields.filter(([, v]) => v !== null && v !== undefined)

  if (populated.length === 0) {
    return <p>No extended metadata retained for this building.</p>
  }

  return <MetadataGrid fields={populated.map(([label, value]) => ({ label, value }))} />
}

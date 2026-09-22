import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getAlertsSummary, listAlerts, listBuildingAlerts } from '../api/alerts'
import type { Alert, DetectorMethod, Severity } from '../api/types'
import { BuildingSelector } from '../components/BuildingSelector'
import { DetectorBadge, ResolvedBadge, SeverityBadge } from '../components/Badges'
import { CategoryBarChart } from '../components/charts/CategoryBarChart'
import { DataTable, type DataTableColumn } from '../components/DataTable'
import { DateRangePicker } from '../components/DateRangePicker'
import { KpiCard, KpiCardSkeleton } from '../components/KpiCard'
import { Panel } from '../components/Panel'
import { ErrorState, LoadingState } from '../components/StateViews'
import { useAnomalyMonthlyTrend } from '../hooks/useAnomalyMonthlyTrend'
import { useApi } from '../hooks/useApi'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import { formatDateTime, formatKwh, formatNumber } from '../lib/format'
import './AnomalyMonitoring.css'

const PAGE_SIZE = 25

const DETECTOR_LABELS: Record<string, string> = {
  data_quality: 'Data quality',
  behavioral: 'Behavioral',
  forecast_residual: 'Forecast residual',
  isolation_forest: 'Isolation Forest',
}

interface Filters {
  buildingId: number | undefined
  method: DetectorMethod | undefined
  anomalyType: string | undefined
  severity: Severity | undefined
  resolved: boolean | undefined
  start: string | undefined
  end: string | undefined
}

const EMPTY_FILTERS: Filters = {
  buildingId: undefined,
  method: undefined,
  anomalyType: undefined,
  severity: undefined,
  resolved: undefined,
  start: undefined,
  end: undefined,
}

export function AnomalyMonitoring() {
  useDocumentTitle('Anomalies')
  const navigate = useNavigate()
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS)
  const [offset, setOffset] = useState(0)

  const summary = useApi(() => getAlertsSummary(), [])
  const trend = useAnomalyMonthlyTrend()

  const alerts = useApi(() => {
    const params = {
      method: filters.method,
      anomaly_type: filters.anomalyType,
      severity: filters.severity,
      resolved: filters.resolved,
      start: filters.start ? `${filters.start}T00:00:00` : undefined,
      end: filters.end ? `${filters.end}T23:59:59` : undefined,
      limit: PAGE_SIZE,
      offset,
    }
    return filters.buildingId ? listBuildingAlerts(filters.buildingId, params) : listAlerts(params)
  }, [filters, offset])

  function updateFilters(patch: Partial<Filters>) {
    setOffset(0)
    setFilters((prev) => ({ ...prev, ...patch }))
  }

  const detectorBarData = summary.data
    ? Object.entries(summary.data.counts_by_method).map(([method, count]) => ({
        method: DETECTOR_LABELS[method] ?? method,
        count,
      }))
    : []
  const severityBarData = summary.data
    ? (['high', 'medium', 'low'] as const)
        .filter((s) => summary.data!.counts_by_severity[s] !== undefined)
        .map((s) => ({ severity: s, count: summary.data!.counts_by_severity[s] ?? 0 }))
    : []
  const anomalyTypeOptions = summary.data ? Object.keys(summary.data.counts_by_anomaly_type).sort() : []

  const columns: DataTableColumn<Alert>[] = [
    { key: 'ts', header: 'Time', render: (a) => formatDateTime(a.ts) },
    { key: 'sensor', header: 'Sensor', render: (a) => `#${a.sensor_id}` },
    { key: 'method', header: 'Detector', render: (a) => <DetectorBadge method={a.method} /> },
    { key: 'anomaly_type', header: 'Type', render: (a) => a.anomaly_type },
    { key: 'severity', header: 'Severity', render: (a) => <SeverityBadge severity={a.severity} /> },
    { key: 'score', header: 'Score', render: (a) => a.score.toFixed(2), align: 'right' },
    { key: 'actual', header: 'Actual', render: (a) => formatKwh(a.actual_value), align: 'right' },
    { key: 'expected', header: 'Expected', render: (a) => formatKwh(a.expected_value), align: 'right' },
    { key: 'resolved', header: 'Status', render: (a) => <ResolvedBadge resolved={a.resolved} /> },
  ]

  const total = alerts.data?.total ?? 0
  const page = Math.floor(offset / PAGE_SIZE) + 1
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Anomalies</h1>
          <p>All alerts raised by the four Phase 8 detectors across the loaded portfolio.</p>
        </div>
      </div>

      <div className="kpi-grid">
        {summary.loading || !summary.data ? (
          Array.from({ length: 4 }).map((_, i) => <KpiCardSkeleton key={i} />)
        ) : (
          <>
            <KpiCard label="Total alerts" value={formatNumber(summary.data.total_alerts)} />
            <KpiCard
              label="Unresolved"
              value={formatNumber(summary.data.unresolved_alerts)}
              accent={summary.data.unresolved_alerts > 0 ? 'danger' : 'success'}
            />
            <KpiCard label="High severity" value={formatNumber(summary.data.counts_by_severity.high ?? 0)} accent="danger" />
            <KpiCard label="Detectors active" value={String(Object.keys(summary.data.counts_by_method).length)} sublabel="of 4" />
          </>
        )}
      </div>

      <div className="panel-grid-2">
        <Panel title="By detector">
          {summary.loading ? (
            <LoadingState />
          ) : summary.error ? (
            <ErrorState error={summary.error} />
          ) : (
            <CategoryBarChart
              data={detectorBarData}
              categoryKey="method"
              orientation="horizontal-bars"
              height={220}
              bars={[{ dataKey: 'count', name: 'Alerts', color: 'var(--color-accent)' }]}
              valueFormatter={(v) => v.toLocaleString()}
            />
          )}
        </Panel>
        <Panel title="By severity">
          {summary.loading ? (
            <LoadingState />
          ) : summary.error ? (
            <ErrorState error={summary.error} />
          ) : (
            <CategoryBarChart
              data={severityBarData}
              categoryKey="severity"
              height={220}
              bars={[{ dataKey: 'count', name: 'Alerts', color: 'var(--color-severity-medium)' }]}
              valueFormatter={(v) => v.toLocaleString()}
            />
          )}
        </Panel>
      </div>

      <Panel title="Anomaly timeline" subtitle="Monthly alert volume across the full loaded history">
        {trend.error ? (
          <ErrorState error={trend.error} />
        ) : !trend.data ? (
          <LoadingState />
        ) : (
          <CategoryBarChart
            data={trend.data}
            categoryKey="month"
            bars={[{ dataKey: 'count', name: 'Alerts', color: 'var(--color-accent)' }]}
            valueFormatter={(v) => v.toLocaleString()}
          />
        )}
      </Panel>

      <Panel
        title="Alerts"
        subtitle={`${formatNumber(total)} matching alert${total === 1 ? '' : 's'}`}
        actions={
          <div className="controls-row">
            <select
              className="control-select"
              value={filters.method ?? ''}
              onChange={(e) => updateFilters({ method: (e.target.value || undefined) as DetectorMethod | undefined })}
              aria-label="Filter by detector"
            >
              <option value="">All detectors</option>
              {Object.keys(DETECTOR_LABELS).map((m) => (
                <option key={m} value={m}>
                  {DETECTOR_LABELS[m]}
                </option>
              ))}
            </select>
            <select
              className="control-select"
              value={filters.anomalyType ?? ''}
              onChange={(e) => updateFilters({ anomalyType: e.target.value || undefined })}
              aria-label="Filter by anomaly type"
            >
              <option value="">All types</option>
              {anomalyTypeOptions.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <select
              className="control-select"
              value={filters.severity ?? ''}
              onChange={(e) => updateFilters({ severity: (e.target.value || undefined) as Severity | undefined })}
              aria-label="Filter by severity"
            >
              <option value="">All severities</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
            <select
              className="control-select"
              value={filters.resolved === undefined ? '' : String(filters.resolved)}
              onChange={(e) =>
                updateFilters({ resolved: e.target.value === '' ? undefined : e.target.value === 'true' })
              }
              aria-label="Filter by resolved status"
            >
              <option value="">Resolved + unresolved</option>
              <option value="false">Unresolved only</option>
              <option value="true">Resolved only</option>
            </select>
          </div>
        }
      >
        <div className="controls-row anomaly-filters-row2">
          <BuildingSelectorWithClear value={filters.buildingId} onChange={(id) => updateFilters({ buildingId: id })} />
          <DateRangePicker
            start={filters.start}
            end={filters.end}
            onChange={(r) => updateFilters({ start: r.start, end: r.end })}
          />
          {(filters.buildingId ||
            filters.method ||
            filters.anomalyType ||
            filters.severity ||
            filters.resolved !== undefined ||
            filters.start ||
            filters.end) && (
            <button type="button" className="clear-filters-btn" onClick={() => updateFilters(EMPTY_FILTERS)}>
              Clear filters
            </button>
          )}
        </div>

        {alerts.loading ? (
          <LoadingState />
        ) : alerts.error ? (
          <ErrorState error={alerts.error} />
        ) : (
          <>
            <DataTable
              columns={columns}
              rows={alerts.data?.items ?? []}
              rowKey={(a) => a.id}
              onRowClick={(a) => navigate(`/anomalies/${a.id}`)}
              emptyMessage="No alerts match these filters."
            />
            {total > PAGE_SIZE && (
              <div className="pagination-row">
                <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                  &larr; Prev
                </button>
                <span>
                  Page {page} of {pageCount}
                </span>
                <button
                  type="button"
                  disabled={offset + PAGE_SIZE >= total}
                  onClick={() => setOffset(offset + PAGE_SIZE)}
                >
                  Next &rarr;
                </button>
              </div>
            )}
          </>
        )}
      </Panel>
    </div>
  )
}

function BuildingSelectorWithClear({
  value,
  onChange,
}: {
  value: number | undefined
  onChange: (buildingId: number | undefined) => void
}) {
  return (
    <div className="building-filter">
      <BuildingSelector value={value} onChange={onChange} />
      {value !== undefined && (
        <button type="button" className="clear-building-btn" onClick={() => onChange(undefined)} aria-label="Clear building filter">
          &times;
        </button>
      )}
    </div>
  )
}

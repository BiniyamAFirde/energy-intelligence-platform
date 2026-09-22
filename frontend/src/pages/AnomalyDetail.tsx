import { Link, useParams } from 'react-router-dom'
import { getAlert } from '../api/alerts'
import { getBuilding } from '../api/buildings'
import { getBuildingEnergy } from '../api/energy'
import { getSensor } from '../api/sensors'
import { DetectorBadge, ResolvedBadge, SeverityBadge } from '../components/Badges'
import { TimeSeriesChart } from '../components/charts/TimeSeriesChart'
import { KpiCard } from '../components/KpiCard'
import { MetadataGrid } from '../components/MetadataGrid'
import { Panel } from '../components/Panel'
import { EmptyState, ErrorState, LoadingState } from '../components/StateViews'
import { useApi } from '../hooks/useApi'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import { formatDateTime, formatKwh } from '../lib/format'
import './AnomalyDetail.css'

const CONTEXT_WINDOW_HOURS = 36

export function AnomalyDetail() {
  const { alertId: alertIdParam } = useParams()
  const alertId = Number(alertIdParam)
  useDocumentTitle(`Alert #${alertIdParam ?? ''}`)

  const alert = useApi(() => getAlert(alertId), [alertId])
  const sensor = useApi(
    () => (alert.data ? getSensor(alert.data.sensor_id) : Promise.resolve(null)),
    [alert.data?.sensor_id],
  )
  const building = useApi(
    () => (sensor.data ? getBuilding(sensor.data.building_id) : Promise.resolve(null)),
    [sensor.data?.building_id],
  )

  const contextSeries = useApi(() => {
    if (!alert.data || !sensor.data) return Promise.resolve([])
    const center = new Date(alert.data.ts)
    const start = new Date(center.getTime() - CONTEXT_WINDOW_HOURS * 3_600_000)
    const end = new Date(center.getTime() + CONTEXT_WINDOW_HOURS * 3_600_000)
    return getBuildingEnergy(sensor.data.building_id, { start: start.toISOString(), end: end.toISOString(), limit: 5000 })
  }, [alert.data?.ts, sensor.data?.building_id])

  if (Number.isNaN(alertId)) {
    return <ErrorState error={new Error('Invalid alert id.')} />
  }
  if (alert.error) {
    return <ErrorState error={alert.error} />
  }

  const a = alert.data

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <Link to="/anomalies" className="back-link">
            &larr; Anomalies
          </Link>
          <h1>Alert #{alertIdParam}</h1>
          {a && (
            <div className="detail-badges">
              <SeverityBadge severity={a.severity} />
              <DetectorBadge method={a.method} />
              <ResolvedBadge resolved={a.resolved} />
            </div>
          )}
        </div>
      </div>

      {alert.loading || !a ? (
        <LoadingState />
      ) : (
        <>
          <div className="kpi-grid">
            <KpiCard label="Actual" value={formatKwh(a.actual_value)} />
            <KpiCard label="Expected" value={formatKwh(a.expected_value)} />
            <KpiCard label="Residual" value={formatKwh(a.residual)} />
            <KpiCard label="Score" value={a.score.toFixed(3)} />
          </div>

          <div className="panel-grid-2">
            <Panel title="Details">
              <MetadataGrid
                fields={[
                  {
                    label: 'Building',
                    value: building.data ? (
                      <Link to={`/buildings/${building.data.building_id}`}>{building.data.building_code}</Link>
                    ) : building.loading ? (
                      '...'
                    ) : (
                      '—'
                    ),
                  },
                  { label: 'Sensor', value: `#${a.sensor_id}` },
                  { label: 'Timestamp', value: formatDateTime(a.ts) },
                  { label: 'Anomaly type', value: a.anomaly_type },
                  { label: 'Detector version', value: a.detector_version },
                  { label: 'Alert created', value: formatDateTime(a.created_at) },
                ]}
              />
            </Panel>

            <Panel title="Explanation">
              <p className="explanation-text">{a.explanation}</p>
            </Panel>
          </div>

          <Panel
            title="Surrounding consumption"
            subtitle={`${CONTEXT_WINDOW_HOURS}h before/after the flagged point`}
          >
            {contextSeries.loading ? (
              <LoadingState />
            ) : contextSeries.error ? (
              <ErrorState error={contextSeries.error} />
            ) : !contextSeries.data || contextSeries.data.length === 0 ? (
              <EmptyState message="No surrounding consumption data available." />
            ) : (
              <TimeSeriesChart
                data={contextSeries.data}
                xKey="ts"
                xFormatter={formatDateTime}
                yFormatter={(v) => v.toLocaleString()}
                lines={[{ dataKey: 'consumption_kwh', name: 'Consumption', color: 'var(--color-actual)' }]}
                highlight={
                  a.actual_value !== null ? { x: a.ts, y: a.actual_value, label: 'Flagged' } : undefined
                }
              />
            )}
          </Panel>
        </>
      )}
    </div>
  )
}

import type { Severity } from '../api/types'
import './Badges.css'

export function SeverityBadge({ severity }: { severity: Severity }) {
  return <span className={`badge badge-severity-${severity}`}>{severity}</span>
}

export function ResolvedBadge({ resolved }: { resolved: boolean }) {
  return (
    <span className={`badge ${resolved ? 'badge-resolved' : 'badge-unresolved'}`}>
      {resolved ? 'resolved' : 'unresolved'}
    </span>
  )
}

const DETECTOR_LABELS: Record<string, string> = {
  data_quality: 'Data quality',
  behavioral: 'Behavioral',
  forecast_residual: 'Forecast residual',
  isolation_forest: 'Isolation Forest',
}

export function DetectorBadge({ method }: { method: string }) {
  return <span className={`badge badge-detector badge-detector-${method}`}>{DETECTOR_LABELS[method] ?? method}</span>
}

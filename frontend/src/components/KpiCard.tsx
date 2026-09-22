import './KpiCard.css'

interface KpiCardProps {
  label: string
  value: string
  sublabel?: string
  accent?: 'default' | 'danger' | 'success'
}

export function KpiCard({ label, value, sublabel, accent = 'default' }: KpiCardProps) {
  return (
    <div className={`kpi-card kpi-card-${accent}`}>
      <div className="kpi-card-label">{label}</div>
      <div className="kpi-card-value numeric">{value}</div>
      {sublabel && <div className="kpi-card-sublabel">{sublabel}</div>}
    </div>
  )
}

export function KpiCardSkeleton() {
  return (
    <div className="kpi-card kpi-card-skeleton" aria-hidden="true">
      <div className="kpi-card-skeleton-line kpi-card-skeleton-label" />
      <div className="kpi-card-skeleton-line kpi-card-skeleton-value" />
    </div>
  )
}

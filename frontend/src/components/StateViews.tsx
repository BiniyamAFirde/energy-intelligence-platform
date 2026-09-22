import { ApiError } from '../api/client'
import './StateViews.css'

export function LoadingState({ label = 'Loading...' }: { label?: string }) {
  return (
    <div className="state-view state-view-loading" role="status" aria-live="polite">
      <div className="state-skeleton" aria-hidden="true" />
      <span className="visually-hidden">{label}</span>
    </div>
  )
}

export function EmptyState({ message }: { message: string }) {
  return (
    <div className="state-view state-view-empty" role="status">
      <p>{message}</p>
    </div>
  )
}

export function ErrorState({ error }: { error: Error }) {
  const detail = error instanceof ApiError && error.detail ? error.detail : error.message
  return (
    <div className="state-view state-view-error" role="alert">
      <p>Couldn't load this data.</p>
      <p className="state-view-error-detail">{detail}</p>
    </div>
  )
}

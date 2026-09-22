import { useEffect, useState } from 'react'
import { listAlerts } from '../api/alerts'

// The loaded BDG2 subset's known real date range (verified against the
// live alerts table) -- used to bucket the anomaly trend chart by month
// via lightweight `limit=1` requests that read only each response's
// `total` count, never downloading the underlying alert rows.
const DATASET_START = new Date(Date.UTC(2016, 0, 1))
const DATASET_END = new Date(Date.UTC(2018, 0, 1))

function monthBuckets(): { start: string; end: string; label: string }[] {
  const buckets: { start: string; end: string; label: string }[] = []
  const cursor = new Date(DATASET_START)
  while (cursor < DATASET_END) {
    const start = new Date(cursor)
    const end = new Date(Date.UTC(cursor.getUTCFullYear(), cursor.getUTCMonth() + 1, 1))
    buckets.push({
      start: start.toISOString(),
      end: end.toISOString(),
      label: start.toLocaleDateString(undefined, { month: 'short', year: '2-digit', timeZone: 'UTC' }),
    })
    cursor.setUTCMonth(cursor.getUTCMonth() + 1)
  }
  return buckets
}

export function useAnomalyMonthlyTrend() {
  const [data, setData] = useState<{ month: string; count: number }[] | null>(null)
  const [error, setError] = useState<Error | null>(null)

  useEffect(() => {
    let cancelled = false
    const buckets = monthBuckets()
    Promise.all(buckets.map((b) => listAlerts({ start: b.start, end: b.end, limit: 1 })))
      .then((results) => {
        if (cancelled) return
        setData(results.map((r, i) => ({ month: buckets[i].label, count: r.total })))
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err : new Error(String(err)))
      })
    return () => {
      cancelled = true
    }
  }, [])

  return { data, error }
}

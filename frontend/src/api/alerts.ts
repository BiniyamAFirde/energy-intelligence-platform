import { apiGet } from './client'
import type { Alert, AlertSummary, DetectorMethod, PaginatedResponse, Severity } from './types'

export interface AlertListParams {
  sensor_id?: number
  method?: DetectorMethod
  anomaly_type?: string
  severity?: Severity
  resolved?: boolean
  start?: string
  end?: string
  limit?: number
  offset?: number
}

export function listAlerts(params?: AlertListParams): Promise<PaginatedResponse<Alert>> {
  return apiGet('/api/v1/alerts', { ...params })
}

export function getAlert(alertId: number): Promise<Alert> {
  return apiGet(`/api/v1/alerts/${alertId}`)
}

export function getAlertsSummary(params?: { start?: string; end?: string }): Promise<AlertSummary> {
  return apiGet('/api/v1/alerts/summary', params)
}

export function listBuildingAlerts(
  buildingId: number,
  params?: AlertListParams,
): Promise<PaginatedResponse<Alert>> {
  return apiGet(`/api/v1/buildings/${buildingId}/alerts`, { ...params })
}

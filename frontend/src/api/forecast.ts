import { apiGet } from './client'
import type { ForecastPoint } from './types'

export function getBuildingForecast(
  buildingId: number,
  params?: { start?: string; end?: string; limit?: number },
): Promise<ForecastPoint[]> {
  return apiGet(`/api/v1/buildings/${buildingId}/forecast`, params)
}

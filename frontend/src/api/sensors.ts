import { apiGet } from './client'
import type { PaginatedResponse, Sensor } from './types'

export function listSensors(params?: {
  building_id?: number
  limit?: number
  offset?: number
}): Promise<PaginatedResponse<Sensor>> {
  return apiGet('/api/v1/sensors', params)
}

export function getSensor(sensorId: number): Promise<Sensor> {
  return apiGet(`/api/v1/sensors/${sensorId}`)
}

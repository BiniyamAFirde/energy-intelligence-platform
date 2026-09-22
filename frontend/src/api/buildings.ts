import { apiGet } from './client'
import type { Building, PaginatedResponse } from './types'

export function listBuildings(params?: {
  site_id?: number
  primary_use?: string
  limit?: number
  offset?: number
}): Promise<PaginatedResponse<Building>> {
  return apiGet('/api/v1/buildings', params)
}

export function getBuilding(buildingId: number): Promise<Building> {
  return apiGet(`/api/v1/buildings/${buildingId}`)
}

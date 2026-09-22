import { apiGet } from './client'
import type { BuildingAnalyticsSummary, BuildingComparisonRow, BuildingPeaks, BuildingProfile } from './types'

export function compareBuildings(params?: {
  site_id?: number
  primary_use?: string
}): Promise<BuildingComparisonRow[]> {
  return apiGet('/api/v1/buildings/compare', params)
}

export function getBuildingAnalyticsSummary(buildingId: number): Promise<BuildingAnalyticsSummary> {
  return apiGet(`/api/v1/buildings/${buildingId}/analytics/summary`)
}

export function getBuildingProfile(buildingId: number): Promise<BuildingProfile> {
  return apiGet(`/api/v1/buildings/${buildingId}/analytics/profile`)
}

export function getBuildingPeaks(buildingId: number, topN?: number): Promise<BuildingPeaks> {
  return apiGet(`/api/v1/buildings/${buildingId}/analytics/peaks`, { top_n: topN })
}

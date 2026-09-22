import { apiGet } from './client'
import type { BuildingSummary, EnergyAggregatePoint, EnergyMeasurement, Granularity } from './types'

export function getBuildingEnergy(
  buildingId: number,
  params?: { start?: string; end?: string; limit?: number },
): Promise<EnergyMeasurement[]> {
  return apiGet(`/api/v1/buildings/${buildingId}/energy`, params)
}

export function getBuildingEnergyAggregate(
  buildingId: number,
  granularity: Granularity,
  params?: { start?: string; end?: string; limit?: number },
): Promise<EnergyAggregatePoint[]> {
  return apiGet(`/api/v1/buildings/${buildingId}/energy/aggregate`, { granularity, ...params })
}

export function getBuildingSummary(buildingId: number): Promise<BuildingSummary> {
  return apiGet(`/api/v1/buildings/${buildingId}/summary`)
}

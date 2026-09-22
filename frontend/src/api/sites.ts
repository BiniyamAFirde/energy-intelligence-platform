import { apiGet } from './client'
import type { PaginatedResponse, Site } from './types'

export function listSites(params?: { limit?: number; offset?: number }): Promise<PaginatedResponse<Site>> {
  return apiGet('/api/v1/sites', params)
}

export function getSite(siteId: number): Promise<Site> {
  return apiGet(`/api/v1/sites/${siteId}`)
}

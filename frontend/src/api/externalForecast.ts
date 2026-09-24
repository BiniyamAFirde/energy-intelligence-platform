import { apiPost } from './client'
import type { ExternalForecastRequest, ExternalForecastResponse } from './types'

export function postExternalForecast(request: ExternalForecastRequest): Promise<ExternalForecastResponse> {
  return apiPost('/api/v1/forecast', request)
}

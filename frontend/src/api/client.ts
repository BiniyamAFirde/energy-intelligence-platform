// Base URL is browser-side (the app runs in the user's browser, not inside
// the Docker network), so it must be the host-mapped backend port -- see
// docker-compose.yml's VITE_API_BASE_URL comment.
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? 'http://localhost:8000'

export class ApiError extends Error {
  status: number | null
  detail: string | null

  constructor(message: string, status: number | null, detail: string | null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

export type QueryParams = Record<string, string | number | boolean | undefined | null>

function buildQuery(params?: QueryParams): string {
  if (!params) return ''
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null) continue
    search.set(key, String(value))
  }
  const qs = search.toString()
  return qs ? `?${qs}` : ''
}

export async function apiGet<T>(path: string, params?: QueryParams): Promise<T> {
  const url = `${API_BASE_URL}${path}${buildQuery(params)}`
  let response: Response
  try {
    response = await fetch(url)
  } catch {
    throw new ApiError(`Network error reaching ${url}`, null, null)
  }

  if (!response.ok) {
    let detail: string | null = null
    try {
      const body = (await response.json()) as { detail?: string }
      detail = body.detail ?? null
    } catch {
      // response body wasn't JSON (or was empty) -- detail stays null
    }
    throw new ApiError(
      detail ?? `Request to ${path} failed with status ${response.status}`,
      response.status,
      detail,
    )
  }

  try {
    return (await response.json()) as T
  } catch {
    throw new ApiError(`Response from ${path} was not valid JSON`, response.status, null)
  }
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const url = `${API_BASE_URL}${path}`
  let response: Response
  try {
    response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  } catch {
    throw new ApiError(`Network error reaching ${url}`, null, null)
  }

  if (!response.ok) {
    let detail: string | null = null
    try {
      const errorBody = (await response.json()) as { detail?: string }
      detail = errorBody.detail ?? null
    } catch {
      // response body wasn't JSON (or was empty) -- detail stays null
    }
    throw new ApiError(
      detail ?? `Request to ${path} failed with status ${response.status}`,
      response.status,
      detail,
    )
  }

  try {
    return (await response.json()) as T
  } catch {
    throw new ApiError(`Response from ${path} was not valid JSON`, response.status, null)
  }
}

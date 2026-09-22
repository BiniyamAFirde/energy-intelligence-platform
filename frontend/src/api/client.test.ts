import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiGet, ApiError } from './client'

function mockFetchOnce(response: Partial<Response> & { json?: () => Promise<unknown> }) {
  const fullResponse = {
    ok: true,
    status: 200,
    json: async () => ({}),
    ...response,
  } as Response
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(fullResponse))
  return fullResponse
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('apiGet', () => {
  it('returns parsed JSON on a 2xx response', async () => {
    mockFetchOnce({ ok: true, status: 200, json: async () => ({ hello: 'world' }) })
    const result = await apiGet<{ hello: string }>('/api/v1/sites')
    expect(result).toEqual({ hello: 'world' })
  })

  it('builds a query string from params, skipping undefined/null values', async () => {
    mockFetchOnce({ ok: true, status: 200, json: async () => [] })
    await apiGet('/api/v1/alerts', { severity: 'high', limit: 10, method: undefined, resolved: null })

    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>
    const calledUrl = fetchMock.mock.calls[0][0] as string
    expect(calledUrl).toContain('severity=high')
    expect(calledUrl).toContain('limit=10')
    expect(calledUrl).not.toContain('method=')
    expect(calledUrl).not.toContain('resolved=')
  })

  it('omits the query string entirely when no params are given', async () => {
    mockFetchOnce({ ok: true, status: 200, json: async () => [] })
    await apiGet('/api/v1/sites')

    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>
    const calledUrl = fetchMock.mock.calls[0][0] as string
    expect(calledUrl.endsWith('/api/v1/sites')).toBe(true)
  })

  it('throws an ApiError carrying the backend detail message on a non-2xx response', async () => {
    mockFetchOnce({ ok: false, status: 404, json: async () => ({ detail: 'building 999 not found' }) })

    await expect(apiGet('/api/v1/buildings/999')).rejects.toMatchObject({
      name: 'ApiError',
      status: 404,
      detail: 'building 999 not found',
      message: 'building 999 not found',
    })
  })

  it('throws an ApiError with a fallback message when the error body is not JSON', async () => {
    mockFetchOnce({
      ok: false,
      status: 500,
      json: async () => {
        throw new Error('not json')
      },
    })

    await expect(apiGet('/api/v1/sites')).rejects.toMatchObject({
      status: 500,
      detail: null,
    })
  })

  it('throws an ApiError on a network failure (fetch rejects)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    const error = await apiGet('/api/v1/sites').catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBeNull()
  })
})

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import type { ExternalForecastResponse } from '../api/types'
import { ExternalForecast } from './ExternalForecast'

vi.mock('../api/externalForecast', () => ({
  postExternalForecast: vi.fn(),
}))

import { postExternalForecast } from '../api/externalForecast'

const mockedPost = postExternalForecast as unknown as ReturnType<typeof vi.fn>

const SMALL_CSV = ['timestamp,energy_kwh', '2024-06-01 00:00:00,80.0', '2024-06-01 01:00:00,82.4', '2024-06-01 02:00:00,79.1'].join(
  '\n',
)

function makeResponse(overrides: Partial<ExternalForecastResponse> = {}): ExternalForecastResponse {
  return {
    building_code: 'external_company_001',
    model_name: 'random_forest',
    model_version: 'v1',
    forecast_origin: '2024-06-09T04:00:00Z',
    predictions: Array.from({ length: 24 }, (_, i) => ({
      target_ts: new Date(Date.UTC(2024, 5, 9, 5 + i)).toISOString(),
      horizon: i + 1,
      predicted_kwh: 80 + i,
    })),
    warnings: [],
    ...overrides,
  }
}

async function uploadCsv(text: string = SMALL_CSV) {
  const file = new File([text], 'energy.csv', { type: 'text/csv' })
  const input = document.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(input, { target: { files: [file] } })
  // "detected" only appears in the post-parse status line (formatCsvStatus)
  // -- unlike "observation", it doesn't also match the static "About this
  // forecast" disclaimer text present from the very first render, so this
  // genuinely waits for the async FileReader parse to finish.
  await waitFor(() => expect(screen.getByText(/detected/i)).toBeInTheDocument())
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('ExternalForecast page', () => {
  it('renders the page with its title and purpose text', () => {
    render(<ExternalForecast />)
    expect(screen.getByRole('heading', { name: 'External Forecast' })).toBeInTheDocument()
    expect(screen.getByText(/Upload a building's recent hourly energy history/)).toBeInTheDocument()
  })

  it('shows all required building metadata fields', () => {
    render(<ExternalForecast />)
    expect(screen.getByText('Building code')).toBeInTheDocument()
    expect(screen.getByText('Area (m²)')).toBeInTheDocument()
    expect(screen.getByText('Number of floors')).toBeInTheDocument()
    expect(screen.getByText('Occupants')).toBeInTheDocument()
    expect(screen.getByText('Primary use')).toBeInTheDocument()
    expect(screen.getByText('Timezone')).toBeInTheDocument()
  })

  it('accepts a CSV upload', async () => {
    render(<ExternalForecast />)
    await uploadCsv()
    expect(screen.getByText('energy.csv')).toBeInTheDocument()
  })

  it('displays the detected observation count after a CSV upload', async () => {
    render(<ExternalForecast />)
    await uploadCsv()
    expect(screen.getByText(/3 observations detected/)).toBeInTheDocument()
  })

  it('calls the API client with the parsed building and energy data when Generate is clicked', async () => {
    mockedPost.mockResolvedValue(makeResponse())
    render(<ExternalForecast />)
    await uploadCsv()

    fireEvent.click(screen.getByRole('button', { name: /generate 24h forecast/i }))

    await waitFor(() => expect(mockedPost).toHaveBeenCalledTimes(1))
    const request = mockedPost.mock.calls[0][0]
    expect(request.building.building_code).toBe('external_company_001')
    expect(request.energy).toHaveLength(3)
    expect(request.energy[0]).toEqual({ timestamp: '2024-06-01 00:00:00', energy_kwh: 80.0 })
  })

  it('shows a loading state while the request is in flight', async () => {
    let resolvePromise: (value: ExternalForecastResponse) => void = () => {}
    mockedPost.mockReturnValue(new Promise((resolve) => (resolvePromise = resolve)))
    render(<ExternalForecast />)
    await uploadCsv()

    fireEvent.click(screen.getByRole('button', { name: /generate 24h forecast/i }))
    // Both the panel title and the visually-hidden status text read
    // "Generating forecast..." -- assert presence via getAllByText rather
    // than a single-match query.
    await waitFor(() => expect(screen.getAllByText('Generating forecast...').length).toBeGreaterThan(0))
    expect(screen.getByRole('status')).toBeInTheDocument()

    resolvePromise(makeResponse())
    await waitFor(() => expect(screen.getByText('24-hour forecast')).toBeInTheDocument())
  })

  it('renders a successful 24-point forecast result', async () => {
    mockedPost.mockResolvedValue(makeResponse())
    render(<ExternalForecast />)
    await uploadCsv()
    fireEvent.click(screen.getByRole('button', { name: /generate 24h forecast/i }))

    expect(await screen.findByText('24-hour forecast')).toBeInTheDocument()
    expect(screen.getByText('external_company_001')).toBeInTheDocument()
    expect(screen.getByText('random_forest')).toBeInTheDocument()
    expect(screen.getByText('24')).toBeInTheDocument() // Predictions KPI
  })

  it('displays an API warning (e.g. unknown primary_use) without hiding it', async () => {
    mockedPost.mockResolvedValue(
      makeResponse({
        warnings: ["primary_use 'LaboratoryX' was not observed during model training and may reduce forecast reliability."],
      }),
    )
    render(<ExternalForecast />)
    await uploadCsv()
    fireEvent.click(screen.getByRole('button', { name: /generate 24h forecast/i }))

    expect(await screen.findByText(/LaboratoryX/)).toBeInTheDocument()
    expect(screen.getByText(/Warning:/)).toBeInTheDocument()
  })

  it('displays a clean, actionable message on an API error instead of a stack trace', async () => {
    mockedPost.mockRejectedValue(
      new ApiError(
        'External inference requires at least 168 consecutive hourly observations before the first forecast origin; got 3.',
        422,
        'External inference requires at least 168 consecutive hourly observations before the first forecast origin; got 3.',
      ),
    )
    render(<ExternalForecast />)
    await uploadCsv()
    fireEvent.click(screen.getByRole('button', { name: /generate 24h forecast/i }))

    expect(await screen.findByText(/at least 168 consecutive hourly observations/)).toBeInTheDocument()
    expect(screen.queryByText(/Traceback/)).not.toBeInTheDocument()
  })

  it('renders all 24 rows in the prediction table', async () => {
    mockedPost.mockResolvedValue(makeResponse())
    render(<ExternalForecast />)
    await uploadCsv()
    fireEvent.click(screen.getByRole('button', { name: /generate 24h forecast/i }))

    await screen.findByText('24-hour forecast')
    const rows = screen.getAllByText(/^\+\d{1,2}h$/)
    expect(rows).toHaveLength(24)
  })
})

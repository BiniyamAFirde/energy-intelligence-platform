import { useMemo, useState } from 'react'
import { postExternalForecast } from '../api/externalForecast'
import { ApiError } from '../api/client'
import type { ExternalForecastResponse } from '../api/types'
import '../components/Controls.css'
import { Panel } from '../components/Panel'
import { KpiCard } from '../components/KpiCard'
import { DataTable, type DataTableColumn } from '../components/DataTable'
import { TimeSeriesChart } from '../components/charts/TimeSeriesChart'
import { LoadingState } from '../components/StateViews'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import { formatDateTime, formatKwh } from '../lib/format'
import { parseEnergyCsv } from '../lib/csv'
import type { ForecastPrediction } from '../api/types'
import './ExternalForecast.css'

// Mirrors backend/src/energy_platform/forecasting/features.py::MAX_LOOKBACK_HOURS
// -- the real, authoritative minimum is enforced server-side; this is only
// used to show a helpful hint before submitting, not to block the request.
const MIN_HISTORY_HOURS = 168

const PRIMARY_USE_SUGGESTIONS = [
  'Office',
  'Education',
  'Lodging/residential',
  'Entertainment/public assembly',
  'Public services',
  'Warehouse/storage',
  'Other',
]

const TIMEZONE_SUGGESTIONS = [
  'US/Eastern',
  'US/Central',
  'US/Mountain',
  'US/Pacific',
  'America/New_York',
  'America/Chicago',
  'America/Denver',
  'America/Los_Angeles',
  'Europe/London',
  'Europe/Berlin',
  'UTC',
]

interface BuildingForm {
  building_code: string
  area_sqm: string
  number_of_floors: string
  occupants: string
  primary_use: string
  timezone: string
}

const DEFAULT_FORM: BuildingForm = {
  building_code: 'external_company_001',
  area_sqm: '4200',
  number_of_floors: '5',
  occupants: '120',
  primary_use: 'Office',
  timezone: 'US/Eastern',
}

type Status = 'input' | 'loading' | 'result'

// A single string, not fragmented JSX interpolation -- keeps this
// (a) readable as one sentence in the DOM/accessibility tree and
// (b) queryable as one text node in tests, rather than several adjacent
// nodes a substring match can't span.
function formatCsvStatus(count: number, range: { start: string; end: string }): string {
  const prefix = count < MIN_HISTORY_HOURS ? '⚠' : '✓'
  const plural = count === 1 ? '' : 's'
  const warning = count < MIN_HISTORY_HOURS ? ` -- at least ${MIN_HISTORY_HOURS} are required` : ''
  return `${prefix} ${count.toLocaleString()} observation${plural} detected (${range.start} → ${range.end})${warning}`
}

export function ExternalForecast() {
  useDocumentTitle('External Forecast')

  const [form, setForm] = useState<BuildingForm>(DEFAULT_FORM)
  const [csvFileName, setCsvFileName] = useState<string | null>(null)
  const [csvRows, setCsvRows] = useState<{ timestamp: string; energy_kwh: number }[]>([])
  const [csvErrors, setCsvErrors] = useState<string[]>([])
  const [status, setStatus] = useState<Status>('input')
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [result, setResult] = useState<ExternalForecastResponse | null>(null)

  const observationRange = useMemo(() => {
    if (csvRows.length === 0) return null
    const timestamps = csvRows.map((r) => r.timestamp).sort()
    return { start: timestamps[0], end: timestamps[timestamps.length - 1] }
  }, [csvRows])

  function updateField(field: keyof BuildingForm, value: string) {
    setForm((prev) => ({ ...prev, [field]: value }))
  }

  function handleFile(file: File) {
    setCsvFileName(file.name)
    const reader = new FileReader()
    reader.onload = () => {
      const { rows, errors } = parseEnergyCsv(String(reader.result ?? ''))
      setCsvRows(rows)
      setCsvErrors(errors)
    }
    reader.readAsText(file)
  }

  const numericFieldsValid =
    form.area_sqm.trim() !== '' &&
    form.number_of_floors.trim() !== '' &&
    form.occupants.trim() !== '' &&
    !Number.isNaN(Number(form.area_sqm)) &&
    !Number.isNaN(Number(form.number_of_floors)) &&
    !Number.isNaN(Number(form.occupants))

  const canSubmit =
    form.building_code.trim() !== '' &&
    form.primary_use.trim() !== '' &&
    form.timezone.trim() !== '' &&
    numericFieldsValid &&
    csvRows.length > 0 &&
    status !== 'loading'

  async function handleSubmit() {
    setSubmitError(null)
    setStatus('loading')
    try {
      const response = await postExternalForecast({
        building: {
          building_code: form.building_code.trim(),
          area_sqm: Number(form.area_sqm),
          number_of_floors: Number(form.number_of_floors),
          occupants: Number(form.occupants),
          primary_use: form.primary_use.trim(),
          timezone: form.timezone.trim(),
        },
        energy: csvRows,
      })
      setResult(response)
      setStatus('result')
    } catch (err) {
      const message = err instanceof ApiError ? (err.detail ?? err.message) : 'Something went wrong generating the forecast.'
      setSubmitError(message)
      setStatus('input')
    }
  }

  function handleStartOver() {
    setResult(null)
    setStatus('input')
    setSubmitError(null)
  }

  const predictionColumns: DataTableColumn<ForecastPrediction>[] = [
    { key: 'target_ts', header: 'Target time', render: (p) => formatDateTime(p.target_ts) },
    { key: 'horizon', header: 'Horizon', render: (p) => `+${p.horizon}h`, align: 'right' },
    { key: 'predicted_kwh', header: 'Forecast', render: (p) => formatKwh(p.predicted_kwh), align: 'right' },
  ]

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>External Forecast</h1>
          <p>
            Upload a building's recent hourly energy history and generate a 24-hour forecast using the deployed
            energy forecasting model.
          </p>
        </div>
      </div>

      {status === 'result' && result ? (
        <ExternalForecastResult result={result} predictionColumns={predictionColumns} onStartOver={handleStartOver} />
      ) : (
        <>
          {submitError && (
            <div className="external-forecast-error" role="alert">
              <strong>Couldn't generate a forecast.</strong>
              <span>{submitError}</span>
            </div>
          )}

          <div className="panel-grid-2">
            <Panel title="Building information">
              <div className="external-forecast-form">
                <label className="control-label external-forecast-field">
                  <span>Building code</span>
                  <input
                    className="control-input"
                    value={form.building_code}
                    onChange={(e) => updateField('building_code', e.target.value)}
                    placeholder="e.g. acme_hq_01"
                  />
                </label>
                <label className="control-label external-forecast-field">
                  <span>Area (m&sup2;)</span>
                  <input
                    className="control-input"
                    type="number"
                    min="0"
                    value={form.area_sqm}
                    onChange={(e) => updateField('area_sqm', e.target.value)}
                  />
                </label>
                <label className="control-label external-forecast-field">
                  <span>Number of floors</span>
                  <input
                    className="control-input"
                    type="number"
                    min="0"
                    value={form.number_of_floors}
                    onChange={(e) => updateField('number_of_floors', e.target.value)}
                  />
                </label>
                <label className="control-label external-forecast-field">
                  <span>Occupants</span>
                  <input
                    className="control-input"
                    type="number"
                    min="0"
                    value={form.occupants}
                    onChange={(e) => updateField('occupants', e.target.value)}
                  />
                </label>
                <label className="control-label external-forecast-field">
                  <span>Primary use</span>
                  <input
                    className="control-input"
                    list="primary-use-suggestions"
                    value={form.primary_use}
                    onChange={(e) => updateField('primary_use', e.target.value)}
                  />
                  <datalist id="primary-use-suggestions">
                    {PRIMARY_USE_SUGGESTIONS.map((u) => (
                      <option key={u} value={u} />
                    ))}
                  </datalist>
                </label>
                <label className="control-label external-forecast-field">
                  <span>Timezone</span>
                  <input
                    className="control-input"
                    list="timezone-suggestions"
                    value={form.timezone}
                    onChange={(e) => updateField('timezone', e.target.value)}
                  />
                  <datalist id="timezone-suggestions">
                    {TIMEZONE_SUGGESTIONS.map((tz) => (
                      <option key={tz} value={tz} />
                    ))}
                  </datalist>
                </label>
              </div>
            </Panel>

            <Panel title="Energy history" subtitle="CSV with 'timestamp' and 'energy_kwh' columns">
              <div className="external-forecast-upload">
                <label className="external-forecast-file-label">
                  <input
                    type="file"
                    accept=".csv,text/csv"
                    onChange={(e) => {
                      const file = e.target.files?.[0]
                      if (file) handleFile(file)
                    }}
                  />
                  <span>{csvFileName ?? 'Choose a CSV file...'}</span>
                </label>

                {csvFileName && (
                  <div className="external-forecast-csv-status">
                    {csvErrors.length > 0 && (
                      <p className="external-forecast-csv-warning">
                        {csvErrors.length} row(s) couldn't be parsed and will be skipped.
                      </p>
                    )}
                    {csvRows.length > 0 && observationRange ? (
                      <p className={csvRows.length < MIN_HISTORY_HOURS ? 'external-forecast-csv-warning' : 'external-forecast-csv-ok'}>
                        {formatCsvStatus(csvRows.length, observationRange)}
                      </p>
                    ) : (
                      <p className="external-forecast-csv-warning">No usable rows found in this file.</p>
                    )}
                  </div>
                )}
              </div>
            </Panel>
          </div>

          <Panel title="About this forecast">
            <ul className="external-forecast-notes">
              <li>This forecast uses the existing trained Random Forest model -- it is not retrained on your data.</li>
              <li>Your data is used only for inference; nothing is written to the production database.</li>
              <li>At least {MIN_HISTORY_HOURS} consecutive hourly observations are required before a forecast can be produced.</li>
              <li>
                This demonstrates that the model can score an arbitrary building's data -- it is not a guarantee of
                production-grade accuracy, universal generalization, guaranteed savings, or anomaly detection for
                every possible building.
              </li>
            </ul>
          </Panel>

          <div className="external-forecast-submit-row">
            <button type="button" className="external-forecast-submit-btn" disabled={!canSubmit} onClick={handleSubmit}>
              {status === 'loading' ? 'Generating...' : 'Generate 24h Forecast'}
            </button>
          </div>

          {status === 'loading' && (
            <Panel title="Generating forecast...">
              <LoadingState label="Generating forecast..." />
            </Panel>
          )}
        </>
      )}
    </div>
  )
}

function ExternalForecastResult({
  result,
  predictionColumns,
  onStartOver,
}: {
  result: ExternalForecastResponse
  predictionColumns: DataTableColumn<ForecastPrediction>[]
  onStartOver: () => void
}) {
  return (
    <>
      <div className="external-forecast-result-header">
        <button type="button" className="external-forecast-secondary-btn" onClick={onStartOver}>
          &larr; New forecast
        </button>
      </div>

      {result.warnings.length > 0 && (
        <div className="external-forecast-warning-banner" role="alert">
          {result.warnings.map((w) => (
            <p key={w}>Warning: {w}</p>
          ))}
        </div>
      )}

      <div className="kpi-grid">
        <KpiCard label="Building" value={result.building_code} />
        <KpiCard label="Forecast origin" value={formatDateTime(result.forecast_origin)} />
        <KpiCard label="Model" value={result.model_name} sublabel={`version ${result.model_version}`} />
        <KpiCard label="Predictions" value={String(result.predictions.length)} sublabel="hourly horizons" />
      </div>

      <Panel title="24-hour forecast" subtitle="Predicted consumption for each hour ahead of the forecast origin">
        <TimeSeriesChart
          data={result.predictions}
          xKey="target_ts"
          xFormatter={formatDateTime}
          yFormatter={(v) => v.toLocaleString()}
          lines={[{ dataKey: 'predicted_kwh', name: 'Forecast', color: 'var(--color-forecast)' }]}
        />
      </Panel>

      <Panel title="Forecast points">
        <DataTable
          columns={predictionColumns}
          rows={result.predictions}
          rowKey={(p) => `${p.target_ts}-${p.horizon}`}
          emptyMessage="No forecast points."
        />
      </Panel>
    </>
  )
}

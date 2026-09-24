import type { ExternalEnergyObservationIn } from '../api/types'

export interface ParsedEnergyCsv {
  rows: ExternalEnergyObservationIn[]
  errors: string[]
}

/**
 * Minimal, deliberately small parser for the two-column
 * `timestamp,energy_kwh` contract (see docs/external_inference.md) --
 * not a general-purpose CSV parser (no quoted-field/embedded-comma
 * support), which this input never needs. All real validation (duplicate
 * timestamps, gaps, negative values, minimum history, ...) happens
 * server-side in the same code the CSV CLI uses; this only turns file text
 * into rows the API client can send, plus a few obvious per-row parse
 * problems so a malformed file doesn't produce silently-wrong requests.
 */
export function parseEnergyCsv(text: string): ParsedEnergyCsv {
  const lines = text.split(/\r\n|\n|\r/).filter((line) => line.trim().length > 0)
  if (lines.length === 0) {
    return { rows: [], errors: ['The file is empty.'] }
  }

  const header = lines[0].split(',').map((h) => h.trim().toLowerCase())
  const tsIndex = header.indexOf('timestamp')
  const kwhIndex = header.indexOf('energy_kwh')
  if (tsIndex === -1 || kwhIndex === -1) {
    return { rows: [], errors: ["The CSV must have 'timestamp' and 'energy_kwh' columns."] }
  }

  const rows: ExternalEnergyObservationIn[] = []
  const errors: string[] = []

  for (let i = 1; i < lines.length; i++) {
    const cols = lines[i].split(',')
    const timestamp = cols[tsIndex]?.trim()
    const rawValue = cols[kwhIndex]?.trim()

    if (!timestamp || !rawValue) {
      errors.push(`Row ${i + 1}: missing timestamp or energy_kwh.`)
      continue
    }
    const energy_kwh = Number(rawValue)
    if (Number.isNaN(energy_kwh)) {
      errors.push(`Row ${i + 1}: energy_kwh '${rawValue}' is not numeric.`)
      continue
    }
    rows.push({ timestamp, energy_kwh })
  }

  return { rows, errors }
}

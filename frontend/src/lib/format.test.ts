import { describe, expect, it } from 'vitest'
import { formatKwh, formatNumber, formatPercent, toDateInputValue } from './format'

describe('formatKwh', () => {
  it('formats a number with the kWh suffix', () => {
    expect(formatKwh(1234.5)).toBe('1,234.5 kWh')
  })

  it('renders null and undefined as an em dash, not "null"/"NaN"', () => {
    expect(formatKwh(null)).toBe('—')
    expect(formatKwh(undefined)).toBe('—')
  })

  it('respects a custom digit count', () => {
    expect(formatKwh(1.23456, 3)).toBe('1.235 kWh')
  })
})

describe('formatNumber', () => {
  it('adds thousands separators', () => {
    expect(formatNumber(99651)).toBe('99,651')
  })

  it('renders null and undefined as an em dash', () => {
    expect(formatNumber(null)).toBe('—')
    expect(formatNumber(undefined)).toBe('—')
  })
})

describe('formatPercent', () => {
  it('formats with a percent sign and default one decimal place', () => {
    expect(formatPercent(3.14159)).toBe('3.1%')
  })

  it('renders null as an em dash', () => {
    expect(formatPercent(null)).toBe('—')
  })
})

describe('toDateInputValue', () => {
  it('truncates an ISO timestamp to just the date portion', () => {
    expect(toDateInputValue('2017-07-01T05:00:00Z')).toBe('2017-07-01')
  })

  it('passes through undefined', () => {
    expect(toDateInputValue(undefined)).toBeUndefined()
  })
})

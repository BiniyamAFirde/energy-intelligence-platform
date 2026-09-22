import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ApiError } from '../api/client'
import { EmptyState, ErrorState, LoadingState } from './StateViews'

describe('LoadingState', () => {
  it('renders a status role so it is announced to assistive tech', () => {
    render(<LoadingState label="Loading alerts..." />)
    expect(screen.getByRole('status')).toBeInTheDocument()
  })
})

describe('EmptyState', () => {
  it('renders the given message, visually distinct from an error', () => {
    render(<EmptyState message="No alerts match these filters." />)
    expect(screen.getByText('No alerts match these filters.')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})

describe('ErrorState', () => {
  it('renders a generic Error message', () => {
    render(<ErrorState error={new Error('boom')} />)
    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.getByText('boom')).toBeInTheDocument()
  })

  it('prefers an ApiError detail over its generic message', () => {
    render(<ErrorState error={new ApiError('Request failed with status 404', 404, 'building 999 not found')} />)
    expect(screen.getByText('building 999 not found')).toBeInTheDocument()
    expect(screen.queryByText('Request failed with status 404')).not.toBeInTheDocument()
  })
})

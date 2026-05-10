import { render, screen } from '@testing-library/react'
import RunStatusBadge from './RunStatusBadge'

describe('RunStatusBadge', () => {
  it('renders "running" text for running status', () => {
    render(<RunStatusBadge status="running" />)
    expect(screen.getByText('running')).toBeInTheDocument()
  })

  it('renders "success" text for success status', () => {
    render(<RunStatusBadge status="success" />)
    expect(screen.getByText('success')).toBeInTheDocument()
  })

  it('renders "failed" text for failed status', () => {
    render(<RunStatusBadge status="failed" />)
    expect(screen.getByText('failed')).toBeInTheDocument()
  })

  it('renders "skipped" text for skipped status', () => {
    render(<RunStatusBadge status="skipped" />)
    expect(screen.getByText('skipped')).toBeInTheDocument()
  })

  it('renders "pending" text for null status (check not yet complete)', () => {
    render(<RunStatusBadge status={null} />)
    expect(screen.getByText('pending')).toBeInTheDocument()
  })

  it('renders "passed" text for check passed status', () => {
    render(<RunStatusBadge status="passed" />)
    expect(screen.getByText('passed')).toBeInTheDocument()
  })

  it('applies green styling for success', () => {
    render(<RunStatusBadge status="success" />)
    const badge = screen.getByText('success')
    expect(badge.className).toMatch(/green|success/)
  })

  it('applies red styling for failed', () => {
    render(<RunStatusBadge status="failed" />)
    const badge = screen.getByText('failed')
    expect(badge.className).toMatch(/red|danger|failed/)
  })

  it('applies yellow/amber styling for running', () => {
    render(<RunStatusBadge status="running" />)
    const badge = screen.getByText('running')
    expect(badge.className).toMatch(/yellow|amber|running/)
  })

  it('applies muted/gray styling for skipped', () => {
    render(<RunStatusBadge status="skipped" />)
    const badge = screen.getByText('skipped')
    expect(badge.className).toMatch(/gray|muted|skipped/)
  })

  it('applies muted styling for pending (null)', () => {
    render(<RunStatusBadge status={null} />)
    const badge = screen.getByText('pending')
    expect(badge.className).toMatch(/gray|muted|pending/)
  })

  it('accepts an additional className prop', () => {
    render(<RunStatusBadge status="success" className="extra-class" />)
    const badge = screen.getByText('success')
    expect(badge.className).toContain('extra-class')
  })

  it('renders as an inline element (span or similar)', () => {
    render(<RunStatusBadge status="success" />)
    const badge = screen.getByText('success')
    expect(['SPAN', 'DIV', 'BADGE']).toContain(badge.tagName)
  })
})

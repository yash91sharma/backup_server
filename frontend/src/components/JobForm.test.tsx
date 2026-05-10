import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { BackupJob } from '../lib/types'
import JobForm from './JobForm'

const baseJob: BackupJob = {
  id: 'job-uuid',
  name: 'My Documents',
  source_label: 'documents',
  source_subpath: null,
  destination_label: 'main',
  restic_password: null,
  schedule_type: 'interval',
  schedule_value: '6h',
  enabled: true,
  retain_keep_last: null,
  retain_keep_hourly: null,
  retain_keep_daily: null,
  retain_keep_weekly: null,
  retain_keep_monthly: null,
  retain_keep_yearly: null,
  retain_keep_within: null,
  retain_keep_within_hourly: null,
  retain_keep_within_daily: null,
  retain_keep_within_weekly: null,
  retain_keep_within_monthly: null,
  retain_keep_within_yearly: null,
  exclude_patterns: null,
  exclude_caches: false,
  exclude_if_present: null,
  one_file_system: false,
  no_scan: false,
  tags: null,
  compression: null,
  pack_size: null,
  read_concurrency: null,
  timeout_hours: null,
  check_enabled: false,
  check_mode: null,
  check_subset_percent: null,
  check_timeout_hours: null,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
  next_run_time: null,
  last_run: null,
  has_successful_run: false,
}

describe('JobForm', () => {
  describe('create mode (no job prop)', () => {
    it('renders the form', () => {
      render(<JobForm onSubmit={vi.fn()} />)
      expect(screen.getByRole('form')).toBeInTheDocument()
    })

    it('shows required name field', () => {
      render(<JobForm onSubmit={vi.fn()} />)
      expect(screen.getByLabelText(/name/i)).toBeInTheDocument()
    })

    it('shows editable password field', () => {
      render(<JobForm onSubmit={vi.fn()} />)
      const pwField = screen.getByLabelText(/password/i)
      expect(pwField).not.toBeDisabled()
      expect(pwField).not.toHaveAttribute('readonly')
    })

    it('shows enabled checkbox checked by default', () => {
      render(<JobForm onSubmit={vi.fn()} />)
      expect(screen.getByLabelText(/enabled/i)).toBeChecked()
    })

    it('shows source and destination dropdowns', () => {
      render(<JobForm onSubmit={vi.fn()} />)
      expect(screen.getByLabelText(/source/i)).toBeInTheDocument()
      expect(screen.getByLabelText(/destination/i)).toBeInTheDocument()
    })

    it('shows schedule input', () => {
      render(<JobForm onSubmit={vi.fn()} />)
      expect(screen.getByTestId('schedule-input')).toBeInTheDocument()
    })

    it('submits with form data on submit', async () => {
      const onSubmit = vi.fn()
      const user = userEvent.setup()
      render(<JobForm onSubmit={onSubmit} />)
      await user.click(screen.getByRole('button', { name: /save|create|submit/i }))
      expect(onSubmit).toHaveBeenCalled()
    })
  })

  describe('edit mode — password lock', () => {
    it('shows editable password field when has_successful_run=false', () => {
      render(<JobForm job={{ ...baseJob, has_successful_run: false }} onSubmit={vi.fn()} />)
      const pwField = screen.getByLabelText(/password/i)
      expect(pwField).not.toBeDisabled()
    })

    it('shows note about changeability when has_successful_run=false', () => {
      render(<JobForm job={{ ...baseJob, has_successful_run: false }} onSubmit={vi.fn()} />)
      expect(screen.getByText(/no backups.*run yet|still change/i)).toBeInTheDocument()
    })

    it('shows locked password field when has_successful_run=true', () => {
      render(<JobForm job={{ ...baseJob, has_successful_run: true }} onSubmit={vi.fn()} />)
      const pwField = screen.getByLabelText(/password/i)
      expect(pwField).toBeDisabled()
    })

    it('shows lock icon when password is locked', () => {
      render(<JobForm job={{ ...baseJob, has_successful_run: true }} onSubmit={vi.fn()} />)
      expect(screen.getByText(/🔒|permanent|cannot change/i)).toBeInTheDocument()
    })

    it('shows tooltip about restic key rotation when locked', () => {
      render(<JobForm job={{ ...baseJob, has_successful_run: true }} onSubmit={vi.fn()} />)
      expect(screen.getByText(/restic key/i)).toBeInTheDocument()
    })
  })

  describe('edit mode — destination immutability', () => {
    it('shows destination field as read-only in edit mode', () => {
      render(<JobForm job={baseJob} onSubmit={vi.fn()} />)
      const destField = screen.getByLabelText(/destination/i)
      expect(destField).toBeDisabled()
    })

    it('shows explanation about destination immutability', () => {
      render(<JobForm job={baseJob} onSubmit={vi.fn()} />)
      expect(screen.getByText(/cannot be changed after creation/i)).toBeInTheDocument()
    })

    it('shows link to destinations rename tool', () => {
      render(<JobForm job={baseJob} onSubmit={vi.fn()} />)
      expect(screen.getByText(/remounted.*new label|rename tool/i)).toBeInTheDocument()
    })
  })

  describe('source label change warning', () => {
    it('shows amber warning banner when source label is changed', async () => {
      const user = userEvent.setup()
      render(<JobForm job={baseJob} onSubmit={vi.fn()} />)
      const sourceInput = screen.getByLabelText(/source/i)
      await user.clear(sourceInput)
      await user.type(sourceInput, 'photos')
      expect(screen.getByText(/changing.*source|redirect.*future backups/i)).toBeInTheDocument()
    })

    it('does not show warning banner initially', () => {
      render(<JobForm job={baseJob} onSubmit={vi.fn()} />)
      expect(screen.queryByText(/changing.*source/i)).not.toBeInTheDocument()
    })
  })

  describe('409 conflict banner', () => {
    it('shows conflict banner with link to conflicting job', () => {
      render(<JobForm onSubmit={vi.fn()} conflictingJob={{ id: 'other-id', name: 'Other Job' }} />)
      expect(screen.getByText(/already.*job|conflict/i)).toBeInTheDocument()
      expect(screen.getByRole('link', { name: /Other Job/i })).toBeInTheDocument()
    })
  })

  describe('check_enabled validation', () => {
    it('requires check_mode when check_enabled is toggled on', async () => {
      const onSubmit = vi.fn()
      const user = userEvent.setup()
      render(<JobForm onSubmit={onSubmit} />)
      await user.click(screen.getByLabelText(/enable.*check|check_enabled/i))
      await user.click(screen.getByRole('button', { name: /save|create|submit/i }))
      expect(
        screen.getByText(/check_mode.*required|verification mode required/i)
      ).toBeInTheDocument()
      expect(onSubmit).not.toHaveBeenCalled()
    })

    it('requires check_subset_percent when check_mode is subset', async () => {
      const onSubmit = vi.fn()
      const user = userEvent.setup()
      render(<JobForm onSubmit={onSubmit} />)
      await user.click(screen.getByLabelText(/enable.*check/i))
      await user.selectOptions(screen.getByLabelText(/check mode/i), 'subset')
      await user.click(screen.getByRole('button', { name: /save|create|submit/i }))
      expect(screen.getByText(/percent.*required|subset_percent/i)).toBeInTheDocument()
      expect(onSubmit).not.toHaveBeenCalled()
    })
  })

  describe('collapsible sections', () => {
    it('shows Basic section expanded by default', () => {
      render(<JobForm onSubmit={vi.fn()} />)
      expect(screen.getByText(/basic/i)).toBeInTheDocument()
      expect(screen.getByLabelText(/name/i)).toBeVisible()
    })

    it('shows Retention Policy section', () => {
      render(<JobForm onSubmit={vi.fn()} />)
      expect(screen.getByText(/retention policy/i)).toBeInTheDocument()
    })

    it('expands Retention Policy section on click', async () => {
      const user = userEvent.setup()
      render(<JobForm onSubmit={vi.fn()} />)
      await user.click(screen.getByText(/retention policy/i))
      expect(screen.getByLabelText(/keep last/i)).toBeVisible()
    })

    it('shows Backup Options section', () => {
      render(<JobForm onSubmit={vi.fn()} />)
      expect(screen.getByText(/backup options/i)).toBeInTheDocument()
    })

    it('shows Verification section', () => {
      render(<JobForm onSubmit={vi.fn()} />)
      expect(screen.getByText(/verification/i)).toBeInTheDocument()
    })
  })
})

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ScheduleInput from './ScheduleInput'

describe('ScheduleInput', () => {
  describe('mode toggle', () => {
    it('renders Cron and Interval mode buttons', () => {
      render(<ScheduleInput value={{ type: 'cron', value: '' }} onChange={vi.fn()} />)
      expect(screen.getByRole('button', { name: /cron/i })).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /interval/i })).toBeInTheDocument()
    })

    it('highlights the active mode button', () => {
      render(<ScheduleInput value={{ type: 'cron', value: '' }} onChange={vi.fn()} />)
      const cronBtn = screen.getByRole('button', { name: /cron/i })
      expect(cronBtn).toHaveAttribute('aria-pressed', 'true')
    })

    it('switches to interval mode on button click', async () => {
      const onChange = vi.fn()
      const user = userEvent.setup()
      render(<ScheduleInput value={{ type: 'cron', value: '0 2 * * *' }} onChange={onChange} />)
      await user.click(screen.getByRole('button', { name: /interval/i }))
      expect(onChange).toHaveBeenCalledWith({ type: 'interval', value: '' })
    })

    it('switches to cron mode on button click', async () => {
      const onChange = vi.fn()
      const user = userEvent.setup()
      render(<ScheduleInput value={{ type: 'interval', value: '6h' }} onChange={onChange} />)
      await user.click(screen.getByRole('button', { name: /cron/i }))
      expect(onChange).toHaveBeenCalledWith({ type: 'cron', value: '' })
    })

    it('clears the other mode value when switching', async () => {
      const onChange = vi.fn()
      const user = userEvent.setup()
      render(<ScheduleInput value={{ type: 'interval', value: '6h' }} onChange={onChange} />)
      await user.click(screen.getByRole('button', { name: /cron/i }))
      const call = onChange.mock.calls[0][0]
      expect(call.value).toBe('')
    })
  })

  describe('cron mode', () => {
    it('renders a text input for cron expression', () => {
      render(<ScheduleInput value={{ type: 'cron', value: '' }} onChange={vi.fn()} />)
      expect(screen.getByRole('textbox')).toBeInTheDocument()
    })

    it('shows human-readable next-run preview for valid expression', () => {
      render(<ScheduleInput value={{ type: 'cron', value: '0 2 * * *' }} onChange={vi.fn()} />)
      expect(screen.getByText(/next:/i)).toBeInTheDocument()
    })

    it('shows inline error for invalid cron expression', () => {
      render(<ScheduleInput value={{ type: 'cron', value: 'not valid' }} onChange={vi.fn()} />)
      expect(screen.getByText(/invalid cron/i)).toBeInTheDocument()
    })

    it('does not show error for empty value', () => {
      render(<ScheduleInput value={{ type: 'cron', value: '' }} onChange={vi.fn()} />)
      expect(screen.queryByText(/invalid cron/i)).not.toBeInTheDocument()
    })

    it('emits updated value when user types', async () => {
      const onChange = vi.fn()
      const user = userEvent.setup()
      render(<ScheduleInput value={{ type: 'cron', value: '' }} onChange={onChange} />)
      await user.type(screen.getByRole('textbox'), '0 3 * * *')
      expect(onChange).toHaveBeenLastCalledWith({ type: 'cron', value: '0 3 * * *' })
    })
  })

  describe('interval mode', () => {
    it('renders a text input for interval value', () => {
      render(<ScheduleInput value={{ type: 'interval', value: '' }} onChange={vi.fn()} />)
      expect(screen.getByRole('textbox')).toBeInTheDocument()
    })

    it('accepts valid Nh format', async () => {
      const onChange = vi.fn()
      const user = userEvent.setup()
      render(<ScheduleInput value={{ type: 'interval', value: '' }} onChange={onChange} />)
      await user.type(screen.getByRole('textbox'), '6h')
      expect(onChange).toHaveBeenLastCalledWith({ type: 'interval', value: '6h' })
    })

    it('accepts valid Nd format', async () => {
      const onChange = vi.fn()
      const user = userEvent.setup()
      render(<ScheduleInput value={{ type: 'interval', value: '' }} onChange={onChange} />)
      await user.type(screen.getByRole('textbox'), '1d')
      expect(onChange).toHaveBeenLastCalledWith({ type: 'interval', value: '1d' })
    })

    it('accepts valid Nm format', async () => {
      const onChange = vi.fn()
      const user = userEvent.setup()
      render(<ScheduleInput value={{ type: 'interval', value: '' }} onChange={onChange} />)
      await user.type(screen.getByRole('textbox'), '30m')
      expect(onChange).toHaveBeenLastCalledWith({ type: 'interval', value: '30m' })
    })

    it('shows error for invalid format like "6hours"', () => {
      render(<ScheduleInput value={{ type: 'interval', value: '6hours' }} onChange={vi.fn()} />)
      expect(screen.getByText(/use format/i)).toBeInTheDocument()
    })

    it('shows error for number-only format like "6"', () => {
      render(<ScheduleInput value={{ type: 'interval', value: '6' }} onChange={vi.fn()} />)
      expect(screen.getByText(/use format/i)).toBeInTheDocument()
    })

    it('does not show error for empty value', () => {
      render(<ScheduleInput value={{ type: 'interval', value: '' }} onChange={vi.fn()} />)
      expect(screen.queryByText(/use format/i)).not.toBeInTheDocument()
    })

    it('shows error for zero-value like "0h"', () => {
      render(<ScheduleInput value={{ type: 'interval', value: '0h' }} onChange={vi.fn()} />)
      expect(screen.getByText(/use format|invalid|minimum/i)).toBeInTheDocument()
    })

    it('shows error for negative value like "-6h"', () => {
      render(<ScheduleInput value={{ type: 'interval', value: '-6h' }} onChange={vi.fn()} />)
      expect(screen.getByText(/use format|invalid/i)).toBeInTheDocument()
    })

    it('shows human-readable preview for valid interval', () => {
      render(<ScheduleInput value={{ type: 'interval', value: '6h' }} onChange={vi.fn()} />)
      expect(screen.getByText(/every|6.*hour/i)).toBeInTheDocument()
    })
  })

  describe('accessibility', () => {
    it('cron text input has a label or aria-label', () => {
      render(<ScheduleInput value={{ type: 'cron', value: '' }} onChange={vi.fn()} />)
      const input = screen.getByRole('textbox')
      expect(
        (input.getAttribute('aria-label') ?? input.id)
          ? document.querySelector(`label[for="${input.id}"]`)
          : null
      ).toBeTruthy()
    })

    it('mode buttons have aria-pressed attribute', () => {
      render(<ScheduleInput value={{ type: 'interval', value: '' }} onChange={vi.fn()} />)
      const intervalBtn = screen.getByRole('button', { name: /interval/i })
      const cronBtn = screen.getByRole('button', { name: /cron/i })
      expect(intervalBtn).toHaveAttribute('aria-pressed', 'true')
      expect(cronBtn).toHaveAttribute('aria-pressed', 'false')
    })
  })
})

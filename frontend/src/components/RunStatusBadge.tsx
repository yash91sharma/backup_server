import type { CheckStatus, RunStatus } from '../lib/types'

const STATUS_CONFIG: Record<string, { label: string; className: string }> = {
  running: {
    label: 'running',
    className: 'badge-running bg-amber-100 text-amber-800 rounded px-2 py-0.5 text-sm font-medium',
  },
  success: {
    label: 'success',
    className: 'badge-success bg-green-100 text-green-800 rounded px-2 py-0.5 text-sm font-medium',
  },
  failed: {
    label: 'failed',
    className: 'badge-failed bg-red-100 text-red-800 rounded px-2 py-0.5 text-sm font-medium',
  },
  skipped: {
    label: 'skipped',
    className: 'badge-skipped bg-gray-100 text-gray-600 rounded px-2 py-0.5 text-sm font-medium',
  },
  passed: {
    label: 'passed',
    className: 'badge-success bg-green-100 text-green-800 rounded px-2 py-0.5 text-sm font-medium',
  },
  pending: {
    label: 'pending',
    className: 'badge-pending bg-gray-100 text-gray-500 rounded px-2 py-0.5 text-sm font-medium',
  },
}

export interface RunStatusBadgeProps {
  status: RunStatus | CheckStatus | null
  className?: string
}

export default function RunStatusBadge({ status, className = '' }: RunStatusBadgeProps) {
  const key = status ?? 'pending'
  const config = STATUS_CONFIG[key] ?? STATUS_CONFIG.pending
  return (
    <span className={`${config.className}${className ? ' ' + className : ''}`}>{config.label}</span>
  )
}

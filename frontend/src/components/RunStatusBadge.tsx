import type { CheckStatus, RunStatus } from '../lib/types'

export interface RunStatusBadgeProps {
  status: RunStatus | CheckStatus | null
  className?: string
}

export default function RunStatusBadge(_props: RunStatusBadgeProps) {
  return null
}

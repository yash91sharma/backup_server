import type { BackupJob } from '../lib/types'

export interface JobFormProps {
  job?: BackupJob
  onSubmit: (data: unknown) => void
  onCancel?: () => void
  conflictingJob?: { id: string; name: string }
}

export default function JobForm(_props: JobFormProps) {
  return null
}

import type { BackupJob } from '../lib/types'

export interface JobFormProps {
  job?: BackupJob
  onSubmit: (data: unknown) => void
  onCancel?: () => void
}

export default function JobForm(_props: JobFormProps) {
  return null
}

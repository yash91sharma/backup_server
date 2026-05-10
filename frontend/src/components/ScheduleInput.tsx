export interface ScheduleValue {
  type: 'cron' | 'interval'
  value: string
}

export interface ScheduleInputProps {
  value: ScheduleValue
  onChange: (v: ScheduleValue) => void
}

export default function ScheduleInput(_props: ScheduleInputProps) {
  return null
}

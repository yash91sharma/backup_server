import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import JobForm from '../components/JobForm'
import RunStatusBadge from '../components/RunStatusBadge'
import * as api from '../lib/api'
import type { BackupJob } from '../lib/types'

export default function Jobs() {
  const navigate = useNavigate()
  const [jobToDelete, setJobToDelete] = useState<BackupJob | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [runError, setRunError] = useState<string | null>(null)
  const [showCreateForm, setShowCreateForm] = useState(false)

  const {
    data: jobs,
    error: jobsError,
    refetch,
  } = useQuery({
    queryKey: ['jobs'],
    queryFn: api.listJobs,
    refetchOnWindowFocus: true,
  })

  if (jobsError) {
    return (
      <div className="p-6">
        <p className="text-red-600">Error: could not load jobs.</p>
      </div>
    )
  }

  async function handleRunNow(job: BackupJob) {
    setRunError(null)
    try {
      const result = await api.triggerRun(job.id)
      navigate(`/runs/${result.run_id}`)
    } catch (err: unknown) {
      const status = (err as { status?: number }).status
      if (status === 409) {
        setRunError('A run is already in progress for this job.')
      } else {
        setRunError('Failed to trigger run.')
      }
    }
  }

  async function handleToggleEnabled(job: BackupJob) {
    try {
      if (job.enabled) {
        await api.disableJob(job.id)
      } else {
        await api.enableJob(job.id)
      }
      refetch()
    } catch {
      // ignore toggle errors
    }
  }

  async function handleConfirmDelete() {
    if (!jobToDelete) return
    setDeleteError(null)
    try {
      await api.deleteJob(jobToDelete.id)
      setJobToDelete(null)
      refetch()
    } catch {
      setDeleteError('Error: failed to delete job.')
      setJobToDelete(null)
    }
  }

  return (
    <div className="p-6">
      <div className="flex justify-between items-center mb-4">
        <h1 className="text-2xl font-bold">Jobs</h1>
        <button
          className="bg-blue-600 text-white px-4 py-2 rounded"
          onClick={() => setShowCreateForm(true)}
        >
          Create Job
        </button>
      </div>

      {runError && <p className="text-red-600 mb-2">{runError}</p>}
      {deleteError && <p className="text-red-600 mb-2">{deleteError}</p>}

      {!jobToDelete && jobs?.length === 0 && (
        <p className="text-gray-500">No backup jobs configured yet.</p>
      )}

      {!jobToDelete && jobs && jobs.length > 0 && (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left border-b">
              <th className="py-2 pr-4">Name</th>
              <th className="py-2 pr-4">Source → Dest</th>
              <th className="py-2 pr-4">Schedule</th>
              <th className="py-2 pr-4">Last Run</th>
              <th className="py-2 pr-4">Next Run</th>
              <th className="py-2 pr-4">Status</th>
              <th className="py-2">Actions</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.id} className="border-b">
                <td className="py-2 pr-4">
                  <Link to={`/jobs/${job.id}`} className="text-blue-600 hover:underline">
                    {job.name}
                  </Link>
                </td>
                <td className="py-2 pr-4">
                  <span>{job.source_label}</span>
                  {' → '}
                  <span>{job.destination_label}</span>
                </td>
                <td className="py-2 pr-4">{job.schedule_value}</td>
                <td className="py-2 pr-4">
                  {job.last_run ? <RunStatusBadge status={job.last_run.status} /> : '—'}
                </td>
                <td className="py-2 pr-4">
                  {job.next_run_time ? new Date(job.next_run_time).toLocaleString() : '—'}
                </td>
                <td className="py-2 pr-4">
                  <input
                    type="checkbox"
                    checked={job.enabled}
                    onChange={() => handleToggleEnabled(job)}
                    aria-label="enabled"
                    className="mr-1"
                  />
                  {job.enabled ? 'Enabled' : 'Disabled'}
                </td>
                <td className="py-2 flex gap-2">
                  <button
                    className="text-sm text-blue-600 hover:underline"
                    onClick={() => handleRunNow(job)}
                  >
                    Run Now
                  </button>
                  <button
                    className="text-sm text-red-600 hover:underline"
                    onClick={() => setJobToDelete(job)}
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {jobToDelete && (
        <div
          role="dialog"
          className="fixed inset-0 bg-black/40 flex items-center justify-center z-50"
        >
          <div className="bg-white rounded p-6 max-w-sm w-full">
            <p className="mb-4">
              Are you sure you want to delete &ldquo;{jobToDelete.name}&rdquo;? This cannot be
              undone.
            </p>
            <div className="flex gap-2 justify-end">
              <button
                className="px-4 py-2 bg-red-600 text-white rounded"
                onClick={handleConfirmDelete}
              >
                Yes, Delete
              </button>
              <button className="px-4 py-2 border rounded" onClick={() => setJobToDelete(null)}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {showCreateForm && (
        <div className="mt-6">
          <JobForm
            onSubmit={async (data) => {
              await api.createJob(data)
              setShowCreateForm(false)
              refetch()
            }}
            onCancel={() => setShowCreateForm(false)}
          />
        </div>
      )}
    </div>
  )
}

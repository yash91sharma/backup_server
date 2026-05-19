# Implementation Plan — Backup Server

Central planning document. Tick items as they are completed. Break nothing up further — every task here is already the smallest meaningful unit of work.

---

## Legend

- `[x]` — done
- `[ ]` — not started

---

## 1. Database

### 1.1 Models

- [x] `BackupJob` ORM model (all fields: id, name, source_label, source_subpath, destination_label, restic_password, schedule_type, schedule_value, enabled, all 12 retention fields, all backup-behaviour flags, check fields, timestamps)
- [x] `BackupRun` ORM model (all fields: id, job_id, status, reason, started_at, finished_at, duration_seconds, snapshot_id, all stats fields, backup_output, error_output, prune_status, prune_error_output, check_status, check_error_output, triggered_by)
- [x] `Snapshot` ORM model (id, job_id, run_id, snapshot_id, snapshot_time, hostname, paths, tags, size_bytes, captured_at)
- [x] `AppSettings` ORM model (singleton id=1: ntfy config, tokens, notify flags, restic_version, default_job_timeout_hours)
- [x] All enums defined: `ScheduleType`, `RunStatus`, `RunReason`, `TriggeredBy`, `PruneStatus`, `CheckStatus`, `CheckMode`, `CompressionMode`
- [x] Cascade delete: BackupRun and Snapshot both cascade on BackupJob delete

### 1.2 Database engine

- [x] Async SQLAlchemy engine (`aiosqlite`)
- [x] WAL mode pragma applied at connect time
- [x] `async_sessionmaker` configured with `expire_on_commit=False`
- [x] `get_session()` FastAPI dependency

### 1.3 Migrations

- [x] Create initial Alembic migration (`alembic revision --autogenerate -m "initial schema"`)
- [x] Verify generated migration covers all four tables and all columns
- [x] Run `alembic upgrade head` inside container and confirm no errors

---

## 2. Backend — Core

### 2.1 Logging (`app/core/logging.py`)

- [x] `SENSITIVE_FIELDS` set (`restic_password`, `ntfy_token`)
- [x] `sanitize(data: dict) -> dict` — recursively redacts sensitive fields
- [x] `get_logger(name: str)` — named logger factory
- [x] `@log_call` decorator — logs function name, sanitized args, return value, exceptions
- [x] `setup_logging()` — reads `LOG_LEVEL` env var, calls `logging.basicConfig`
- [x] `RequestLoggingMiddleware` — logs method, path, status code for every request

### 2.2 Scheduler (`app/core/scheduler.py`)

- [x] `AsyncIOScheduler` instance with `MemoryJobStore` at module level
- [x] `build_trigger(schedule_type, schedule_value)` — parses interval (`Nh`, `Nd`, `Nm`) and cron expressions; raises `ValueError` on invalid input
- [x] `start_scheduler()` — seeds AppSettings on first boot, detects restic version, cleans stale `running` rows (`status=failed`, `reason=container_restart`, `prune_status=skipped`, `check_status=skipped`), cleans null `check_status` rows (sets to `skipped`), registers enabled jobs, starts scheduler if not already running
- [x] `shutdown_scheduler()` — graceful shutdown

### 2.3 Config (`app/core/config.py`)

- [x] Pydantic `BaseSettings` reading env vars (TZ, etc.)

---

## 3. Backend — API Layer

### 3.1 Schemas

#### Jobs (`app/api/schemas/jobs.py`)

- [x] `JobCreate` with field validators: `name` (non-empty, max 128), `source_label` / `destination_label` (no `/` or `..`), `source_subpath` (no `/`, one level only)
- [x] `JobCreate` schedule validator: interval regex `^([1-9][0-9]*)(h|d|m)$`; interval minutes minimum 5; cron validated via `CronTrigger.from_crontab()` with minimum 1-hour gap between consecutive fires
- [x] `JobCreate` check-settings validator: `check_mode` required when `check_enabled=True`; `check_subset_percent` required when `check_mode=subset`
- [x] `JobUpdate(JobCreate)` with `restic_password: Optional[str] = None`
- [x] `RunSummarySchema` (from_attributes, all BackupRun fields except output blobs, plus `job_name: Optional[str]`)
- [x] `SnapshotResponse` (from_attributes, all Snapshot columns)
- [x] `JobResponse` (from_attributes, `restic_password` always `None`, computed: `has_successful_run`, `next_run_time`, `last_run`)

#### Runs (`app/api/schemas/runs.py`)

- [x] `RunDetailSchema(RunSummarySchema)` — adds `backup_output`, `error_output`, `prune_error_output`, `check_error_output`

#### Mounts (`app/api/schemas/mounts.py`)

- [x] `RenameDestinationRequest` with validator: `new_label` no `/` or `..`, not equal to `old_label`
- [x] `RenameDestinationResult` with `affected_jobs: List[Dict[str, Any]]`

#### Settings (`app/api/schemas/settings.py`)

- [x] `SettingsUpdate`: `ntfy_server_url` must start with `http://` or `https://` (max 512 chars), `ntfy_topic` regex `^[a-zA-Z0-9_-]{0,64}$`, `default_job_timeout_hours` ge=1 le=168
- [x] `SettingsResponse` — `ntfy_token` always `None`
- [x] `HealthResponse`, `NtfyTestResult`, `ResticUpdateCheck`

### 3.2 Routes

#### Jobs (`app/api/routes/jobs.py`)

- [x] `GET /jobs` — list all with computed fields
- [x] `POST /jobs` (201) — validate mounts exist, check duplicate (source+dest), create job, register in scheduler if enabled
- [x] `GET /jobs/{id}` — fetch or 404
- [x] `PUT /jobs/{id}` — enforce `destination_label` immutability (422), enforce password immutability after successful run (422), duplicate check (409) before mount check (422), reschedule if scheduler running
- [x] `DELETE /jobs/{id}` — block if run active (409), remove from scheduler, cascade delete
- [x] `POST /jobs/{id}/run` — if active: create skipped row and return; else create running row + fire background task
- [x] `POST /jobs/{id}/enable` — set enabled=True, register in scheduler
- [x] `POST /jobs/{id}/disable` — set enabled=False, remove from scheduler
- [x] `POST /jobs/{id}/unlock` — block if run active (409), call `restic_unlock`, return output
- [x] `GET /jobs/{id}/runs` — all runs ordered by `started_at` desc
- [x] `GET /jobs/{id}/snapshots` — all snapshots ordered by `snapshot_time` desc

#### Runs (`app/api/routes/runs.py`)

- [x] `GET /runs/recent` — query param `limit` (ge=1, le=100, default 10), JOIN with BackupJob for `job_name`, ordered by `started_at` desc — registered BEFORE `/{id}` to prevent routing conflict
- [x] `GET /runs/{id}` — full detail including all output fields

#### Mounts (`app/api/routes/mounts.py`)

- [x] `GET /mounts/sources` — scan `SOURCES_ROOT`, return directory names
- [x] `GET /mounts/sources/{label}/subdirs` — one level of subdirectories, 404 if label not found
- [x] `GET /mounts/destinations` — scan `DESTINATIONS_ROOT`, return directory names
- [x] `POST /mounts/destinations/rename` — validate **new** label directory is mounted (old label directory does NOT need to exist — rename is DB-only); 404 if no jobs use old label; 409 if any such job has an active run; bulk update `destination_label`

#### Settings (`app/api/routes/settings.py`)

- [x] `GET /settings` — fetch or create AppSettings(id=1), return with `ntfy_token=None`
- [x] `PUT /settings` — upsert, return with `ntfy_token=None`
- [x] `POST /settings/test-ntfy` — 422 if topic empty, POST to ntfy, return `{ok, error}`
- [x] `GET /settings/restic-update-check` — fetch GitHub releases API, compare versions, handle network errors gracefully
- [x] `GET /health` — always 200, reports `scheduler_running`, `db_ok`, `restic_version`

### 3.3 App wiring (`app/main.py`)

- [x] `lifespan` context manager calling `setup_logging()`, `start_scheduler()`, `shutdown_scheduler()`
- [x] Custom `RequestValidationError` handler — flattens Pydantic v2 list errors to a single string
- [x] CORS middleware
- [x] `RequestLoggingMiddleware`
- [x] All routers included under `/api` prefix
- [x] `StaticFiles` mount at `/static` (only if directory exists)
- [x] Catch-all SPA route registered last

---

## 4. Backend — Service Layer

### 4.1 Restic subprocess wrappers (`app/services/restic.py`)

Every function uses `asyncio.create_subprocess_exec` and passes these env vars: `RESTIC_PASSWORD=password`, `RESTIC_REPOSITORY=repo_path` (for all repo commands), `RESTIC_CACHE_DIR=/app/data/restic-cache`. Repo path is conveyed via env, not via `-r` flag. All use `proc.communicate()` (not separate stdout/stderr reads). On `asyncio.TimeoutError`, call `proc.kill()` and return a non-zero code with `"timed out"` in stderr — never re-raise. Never log the password.

- [x] `restic_version()` — run `restic version`; wrap communicate in `asyncio.wait_for`; parse `"restic X.Y.Z"` from stdout; return version string or `None` on any failure (non-zero rc, timeout, parse error); @log_call decorator applied
- [x] `restic_cat_config(repo_path, password)` — run `restic cat config`; return `(rc, stdout, stderr)`; rc=0 means repo exists and password is correct; pass `RESTIC_REPOSITORY` in env; @log_call decorator applied
- [x] `restic_init(repo_path, password)` — run `restic init`; return `(rc, stdout, stderr)`; @log_call decorator applied
- [x] `restic_backup(repo_path, password, source_path, timeout_seconds, **kwargs)` — build CLI flags from kwargs: one `--exclude <pattern>` per entry in `exclude_patterns`; `--exclude-caches` if true; one `--exclude-if-present <file>` per entry; one `--tag <tag>` per entry in `tags`; `--one-file-system` if true; `--no-scan` if true; `--compression <val>` if set; `--pack-size <n>` if set; `--read-concurrency <n>` if set; always append `--json --verbose`; wrap in `asyncio.wait_for(timeout=timeout_seconds)`; on timeout call `proc.kill()` and return `(-1, "", "backup timed out", None)`; on rc != 0 return `(rc, stdout, stderr, None)`; on success parse the last JSON line of stdout into summary dict; **strip the password string from stdout before returning** (replace all occurrences); return `(rc, cleaned_stdout, stderr, summary_dict)`; @log_call decorator applied
- [x] `restic_snapshots(repo_path, password)` — run `restic snapshots --json`; parse JSON array from stdout; return `(rc, list_of_dicts, stderr)`; return `(rc, [], stderr)` on parse error; @log_call decorator applied
- [x] `restic_forget_prune(repo_path, password, timeout_seconds, **retention_flags)` — build `--keep-last`, `--keep-hourly`, `--keep-daily`, `--keep-weekly`, `--keep-monthly`, `--keep-yearly`, `--keep-within`, `--keep-within-hourly`, `--keep-within-daily`, `--keep-within-weekly`, `--keep-within-monthly`, `--keep-within-yearly` from non-null kwargs; always append `--prune`; wrap in `asyncio.wait_for`; on timeout return `(-1, "", "forget/prune timed out")`; return `(rc, stdout, stderr)`; @log_call decorator applied
- [x] `restic_prune(repo_path, password, timeout_seconds)` — run `restic prune`; wrap in `asyncio.wait_for`; return `(rc, stdout, stderr)` — must NOT include any `--keep-*` flags; @log_call decorator applied
- [x] `restic_check(repo_path, password, mode, subset_percent, timeout_seconds)` — `structural` → no extra args; `subset` → `--read-data-subset={subset_percent}%`; `full` → `--read-data`; wrap in `asyncio.wait_for`; return `(rc, stdout, stderr)`; @log_call decorator applied
- [x] `restic_unlock(repo_path, password)` — run `restic unlock`; pass `RESTIC_REPOSITORY` and `RESTIC_PASSWORD` in env; return `(rc, stdout, stderr)`; @log_call decorator applied

### 4.2 Backup runner (`app/services/backup_runner.py`)

Implements the 12-step lifecycle. Uses `LoggerAdapter` with `job_id` + `run_id` on every log line. All restic calls go through `app.services.restic` module reference (not direct imports) so tests can patch them. DB sessions are opened via `async_sessionmaker(engine)` where `engine` is imported from `app.db.database` at module level.

**Type checking improvements:**

- [x] Added explicit type hints to all intermediate variables before SQLAlchemy ORM attribute assignment (e.g., `now_utc: datetime` before assigning to `failed_run.finished_at`)
- [x] Replaced variable redeclarations and shadowing with direct assignments (removed `prune_status`, `check_status`, `skip_status`, `final_status_success`, `final_check_skip`, `check_passed`, `check_failed` intermediate variables where unused)
- [x] Standardised SQLAlchemy ORM attribute assignments with `# type: ignore[assignment]` comments (industry standard due to SQLAlchemy descriptor limitations)
- [x] All type checking warnings in VS Code eliminated (Pylance/Pyright)

**Pre-step — Invocation and job lookup**

- [x] At module level, import `engine` from `app.db.database` so tests can patch it
- [x] `run_backup(job_id, run_id=None)`: fetch `BackupJob` by `job_id`; if not found, return silently (no-op — tests verify `job_id not in _active_jobs`)
- [x] Two invocation paths based on whether `run_id` is provided:
  - **Scheduler path** (`run_id=None`): must do the full concurrent run guard (steps below) and create the `BackupRun` row itself
  - **API path** (`run_id` provided): the route already created the row and checked `_active_jobs`; add `job_id` to `_active_jobs` and proceed directly to step 2

**Step 1 — Concurrent run guard** (scheduler path only, when `run_id=None`)

- [x] Initialise `_job_locks[job_id]` on first access via `_job_locks.setdefault(job_id, asyncio.Lock())`
- [x] Acquire the lock; within the lock, query for an existing `status=running` row for this job
- [x] If running row found: create `status=skipped`, `reason=overlapping_run`, `started_at=now`, `finished_at=now`, `prune_status=skipped`, `check_status=skipped`; release lock; return
- [x] If no running row: create a new `BackupRun(status=running, triggered_by=scheduler, started_at=now)` row; commit; add `job_id` to `_active_jobs`; release lock; proceed with this new run row

**Step 2 — Validate password**

- [x] If `restic_password` is null/empty: update run to `status=failed`, `error_output="No restic password configured for this job."`, `finished_at=now`, `prune_status=skipped`, `check_status=skipped`; skip to cleanup (finally block)

**Step 3 — Start notification**

- [x] Fetch `AppSettings(id=1)` to get ntfy config (reused for all notification steps)
- [x] If `settings.notify_on_start` and topic is set: fire-and-forget `send_notification` with job name, source, destination, triggered-by

**Step 4 — Init check**

- [x] Build `repo_path = f"/destinations/{job.destination_label}/{job.id}"`
- [x] Call `restic_cat_config(repo_path, password)`
- [x] If rc != 0 **and** `"wrong password"` appears in stderr: update run to `status=failed`, `error_output=stderr`, `prune_status=skipped`, `check_status=skipped`; skip to steps 10/11 — do NOT attempt init
- [x] If rc != 0 **and** wrong-password not in stderr (repo absent or other error): call `restic_init(repo_path, password)`
- [x] If init also fails (rc != 0): update run to `status=failed`, `error_output=stderr`, `prune_status=skipped`, `check_status=skipped`; skip to steps 10/11

**Step 5 — Backup**

- [x] Build `source_path = f"/sources/{job.source_label}"` (append `/{job.source_subpath}` if set)
- [x] Compute timeout: `(job.timeout_hours or settings.default_job_timeout_hours) * 3600`
- [x] Collect kwargs from all non-null backup-behaviour fields on the job
- [x] Call `restic_backup(repo_path, password, source_path, timeout_seconds, **kwargs)`
- [x] On timeout: `status=failed`, `error_output="Backup subprocess timed out after N hours"`, `prune_status=skipped`, `check_status=skipped`; go to step 10/11
- [x] On rc != 0: `status=failed`, `error_output=stderr`, `prune_status=skipped`, `check_status=skipped`; go to step 10/11

**Step 6 — Parse output**

- [x] Extract final JSON summary dict from stdout (last line matching `{`)
- [x] If no summary dict found: treat as failure

**Step 7 — Update run stats**

- [x] Populate `files_new`, `files_changed`, `files_unmodified` from summary
- [x] Populate `dirs_new`, `dirs_changed`, `dirs_unmodified` from summary
- [x] Populate `data_added_bytes` (`data_added`), `data_added_packed_bytes` (`data_added_packed`), `total_bytes_processed`
- [x] Populate `snapshot_id` from summary `snapshot_id` field
- [x] Store full stdout in `backup_output`

**Step 8 — Prune**

- [x] Collect non-null retention fields from job
- [x] If any retention field set: call `restic_forget_prune(repo_path, password, timeout, **retention)`; else call `restic_prune(repo_path, password, timeout)`
- [x] If rc == 0: `prune_status=passed`
- [x] If rc != 0: `prune_status=failed`, `prune_error_output=stderr`; **non-fatal** — continue to step 9

**Step 9 — Reconcile snapshots**

- [x] Call `restic_snapshots(repo_path, password)`
- [x] Delete `Snapshot` rows for this job whose `snapshot_id` is not in the returned list (pruned)
- [x] Upsert a `Snapshot` row for each entry returned: `snapshot_id`, `snapshot_time`, `hostname`, `paths`, `tags`, `size_bytes` from `summary.total_size`
- [x] Set `run_id` on the row matching the current run's `snapshot_id`
- [x] Set `captured_at = run.finished_at`

**Step 10 — Finalise run**

- [x] Set `status=success` (or `failed` if any earlier step failed)
- [x] Set `finished_at = now`, `duration_seconds = (finished_at - started_at).seconds`
- [x] If `check_status` is still `null` — regardless of whether backup succeeded or failed, and regardless of `check_enabled` — set `check_status=skipped`; this covers: failed runs, runs where `check_enabled=false`, and any other path that didn't reach step 12

**Step 11 — Completion notification**

- [x] If `status=success` and `settings.notify_on_success`: fire-and-forget `send_notification` with duration, files changed, data added
- [x] If `status=failed` and `settings.notify_on_failure`: fire-and-forget with excerpt from `error_output`

**Step 12 — Integrity check** (only if `check_enabled=True` and `status=success`)

- [x] If `settings.notify_on_verification`: send "verification started" notification
- [x] Compute check timeout: `(job.check_timeout_hours or settings.default_job_timeout_hours) * 3600`
- [x] Call `restic_check(repo_path, password, job.check_mode, job.check_subset_percent, timeout_seconds)`
- [x] On timeout: `check_status=failed`, `check_error_output="Verification subprocess timed out after N hours"`; run `status` stays `success`
- [x] On rc == 0: `check_status=passed`
- [x] On rc != 0: `check_status=failed`, `check_error_output=stderr`; **non-fatal**
- [x] If `settings.notify_on_verification`: send "verification complete" notification with passed/failed

**Cleanup**

- [x] Remove `job_id` from `_active_jobs` in a `finally` block (always runs, even on unhandled exception)

### 4.3 Notifications (`app/services/notifications.py`)

- [x] `send_notification(server_url, topic, title, message, token=None)` — no-op if topic empty; includes `Authorization: Bearer` header only when token is set

---

## 5. Backend — Tests

### 5.1 Already-passing test suites (157/157)

- [x] `test_jobs.py` — 73 tests (all API route cases)
- [x] `test_runs.py` — 15 tests
- [x] `test_mounts.py` — 18 tests
- [x] `test_settings.py` — 25 tests
- [x] `test_scheduler.py` — 26 tests

### 5.2 Tests that will pass once service layer is implemented

- [x] `test_restic.py` — 42 tests covering: version parse, cat config, init, backup flags/timeout/password masking, snapshots, forget-prune retention flags, prune, check modes, unlock
- [x] `test_backup_runner.py` — 31 tests covering: all 12 steps, overlapping run guard, password validation, active-job cleanup, notification flags, source/repo path construction, stats population, check timeout and modes

---

## 6. Frontend — Setup

### 6.1 shadcn/ui installation

- [x] Install shadcn/ui CLI and initialise (`npx shadcn-ui@latest init`)
- [x] Add components used across all pages: `Button`, `Card`, `CardHeader`, `CardContent`, `Input`, `Label`, `Select`, `Checkbox`, `Switch`, `Badge`, `Tabs`, `TabsList`, `TabsTrigger`, `TabsContent`, `Dialog`, `DialogTrigger`, `DialogContent`, `Collapsible`, `CollapsibleTrigger`, `CollapsibleContent`, `Separator`, `Tooltip`, `Toast`, `Toaster`
- [x] Add `Sonner` (or shadcn Toast) for toast notifications
- [x] Confirm build passes: `npm run build`

---

## 7. Frontend — Components

### 7.1 `RunStatusBadge` (`src/components/RunStatusBadge.tsx`)

- [x] Renders as an inline element (`<span>` or `<div>`) — tests query by tag or role
- [x] Accept `status: RunStatus | null` prop — `null` renders **lowercase** label `"pending"` with a gray/muted CSS class (tests use `getByText('pending')`)
- [x] `running` → **lowercase** label `"running"`, yellow/amber CSS class (className must match `/yellow|amber|running/`)
- [x] `success` → **lowercase** label `"success"`, green CSS class (className must match `/green|success/`)
- [x] `failed` → **lowercase** label `"failed"`, red CSS class (className must match `/red|danger|failed/`)
- [x] `skipped` → **lowercase** label `"skipped"`, gray/muted CSS class (className must match `/gray|muted|skipped/`)
- [x] Accept optional `checkStatus: CheckStatus | null` prop — when non-null render a second badge alongside the main one; `"passed"` is a valid value and renders **lowercase** `"passed"`
- [x] Accept optional `className` prop and apply it to the root element (for layout/spacing by parent)

### 7.2 `ScheduleInput` (`src/components/ScheduleInput.tsx`)

- [x] Root element must have `data-testid="schedule-input"` (required by JobForm test)
- [x] Props shape: `value: { type: 'cron' | 'interval', value: string }` and `onChange: (val: { type: 'cron' | 'interval', value: string }) => void` — NOT separate `value: string` + `scheduleType` props
- [x] Two mode buttons (`Interval` and `Cron`) each with `aria-pressed="true"/"false"` to indicate the active mode
- [x] Switching modes clears the inner value string (calls `onChange` with `{ type: newMode, value: '' }`)
- [x] Interval mode: text input accepting `Nh`, `Nd`, `Nm` format; inline validation; **empty value shows no error**; on non-empty invalid input show error message containing "use format" (e.g. "use format 6h"); on valid input show preview containing "every" and the interval (e.g. "every 6 hours")
- [x] Cron mode: text input for cron expression; **empty value shows no error**; on non-empty invalid cron show error containing "invalid cron"; on valid cron show preview prefixed with "next:" (e.g. "next: Mon 09:00")
- [x] Show validation error message when input is invalid (non-empty and malformed)

### 7.3 `SnapshotList` (`src/components/SnapshotList.tsx`)

- [x] Accept `snapshots: Snapshot[]` prop
- [x] When `snapshots` is empty: render an empty-state message (e.g. "No snapshots yet") and **no `<table>` element** — tests assert the table is absent
- [x] When snapshots present: render a table with columns: "Snapshot ID" (first 8 chars of snapshot_id), "Time", "Size", "Hostname", "Paths", "Tags" — column header text must match these names (case-insensitive)
- [x] Format `size_bytes` using binary units (1024-based): 1024 bytes → "1 KB", 524288000 bytes → "500 MB", 1073741824 bytes → "1 GB"; no decimal needed for round values; show `—` for null
- [x] Render snapshot tags when present (each tag visible in the row)

### 7.4 `JobForm` (`src/components/JobForm.tsx`)

- [x] Accept props: `job?: BackupJob` (undefined = create mode), `onSubmit: (data) => void`, `conflictingJob?: { id: string; name: string }`
- [x] **Basic section** (expanded by default, label "Basic"): Name field, Source Label input, Destination Label input, Password field
- [x] Password field: enabled and not disabled when `has_successful_run=false`; show text "no backups run yet" or "still change" near the field; disabled when `has_successful_run=true`; show lock icon or text matching `/🔒|permanent|cannot change/i`; show text matching `/restic key/i` (tooltip or note about key rotation)
- [x] Destination label: disabled/readonly in edit mode; show text matching `/cannot be changed after creation/i`; show link matching `/remounted.*new label|rename tool/i`
- [x] **Schedule section**: `ScheduleInput` component with `data-testid="schedule-input"` on its root element, Enabled checkbox (checked by default in create mode)
- [x] **Retention Policy section** (collapsible): all 12 retention fields (6 keep-N integers, 6 keep-within strings); section header text "Retention Policy"
- [x] **Backup Options section** (section header text "Backup Options")
- [x] **Verification section**: check_enabled toggle (`aria-label` matching `/enable.*check|check_enabled/i`), check_mode select (label matching `/check mode/i`), check_subset_percent number, check_timeout_hours number
- [x] Client-side validation on submit: if `check_enabled=true` and no `check_mode` selected → show error matching `/check_mode.*required|verification mode required/i`, do not call `onSubmit`; if `check_mode=subset` and no `check_subset_percent` → show error matching `/percent.*required|subset_percent/i`, do not call `onSubmit`
- [x] Source label change warning: when editing an existing job and source label is changed from its original value, show amber banner matching `/changing.*source|redirect.*future backups/i`; do not show banner initially
- [x] Conflict banner: when `conflictingJob` prop is provided, show text matching `/already.*job|conflict/i` and a link with text matching the `conflictingJob.name`
- [x] Submit button label matches `/save|create|submit/i`; calls `onSubmit` with form data on valid submit
- [x] Form root element has `role="form"` so tests can find it with `getByRole('form')`

---

## 8. Frontend — Pages

### 8.1 `Dashboard` (`src/pages/Dashboard.tsx`)

- [x] Fetch `GET /api/runs/recent?limit=10` with TanStack Query
- [x] Fetch `GET /api/jobs` with TanStack Query (needed for stat cards and next run times)
- [x] Fetch `GET /api/health` with TanStack Query; display `restic_version` from the health response on the dashboard (e.g. in a stat card or info line)
- [x] Display stat cards: **total job count** and **enabled job count** (separate cards)
- [x] Display recent runs table: job name (link to `/runs/{id}`), `RunStatusBadge` (with `checkStatus` prop so both status and check_status badges appear), duration, started at, triggered by
- [x] Per-job next run time section: for each job show next run time or `"—"` if `next_run_time` is null or job is disabled
- [x] Show static disk space warning callout — text must match `/disk space.*not monitored/i` ("disk space" before "not monitored" in same element)
- [x] Show red error banner when `scheduler_running=false` — text must match `/scheduler.*not running/i`; a separate or same element must contain "container logs" (matches `/container logs/i`)
- [x] No error banner when `scheduler_running=true`
- [x] Poll `getRecentRuns` while any run has `status=running` OR (`status` terminal and `check_status` null); stop when all runs are settled
- [x] Show error state when `listJobs` or `getRecentRuns` API call fails

### 8.2 `Jobs` (`src/pages/Jobs.tsx`)

- [x] Fetch `GET /api/jobs` with TanStack Query; refetch on window focus
- [x] Render jobs table: Name, Source → Destination, Schedule, Last Run (status badge + relative time), Next Run, Enabled toggle
- [x] Enabled column shows text "Enabled" or "Disabled" per row (in addition to / or as the toggle label)
- [x] Enabled toggle: rendered as `role="switch"` or `role="checkbox"` (either is acceptable); calls `POST /jobs/{id}/enable` or `/disable`
- [x] "Run Now" button: calls `POST /jobs/{id}/run`; on 409 shows toast/message matching `/already.*running|in progress|409/i`; on success navigates to RunDetail `/runs/{run_id}`
- [x] "New Job" button: opens `JobForm` in a dialog or navigates to a form page
- [x] Row click: navigate to `/jobs/{id}`
- [x] Delete button: opens a **confirmation dialog** before calling `DELETE /jobs/{id}`
  - [x] Dialog contains text matching `/are you sure|cannot be undone/i`
  - [x] Dialog displays the job's name
  - [x] Confirm button: label must match `/confirm|yes.*delete|delete.*job/i`; clicking it calls `deleteJob` and closes dialog
  - [x] Cancel button: clicking it does NOT call `deleteJob` and closes dialog
  - [x] On 409 response: close dialog, show toast mentioning "active run" or "in progress"
  - [x] On non-409 error (e.g. 500): close dialog, show generic error toast
  - [x] On success: refetch jobs list
- [x] Show empty state when no jobs
- [x] Show loading skeleton while fetching
- [x] Show error state when `listJobs` API call fails

### 8.3 `JobDetail` (`src/pages/JobDetail.tsx`)

- [x] Fetch `GET /api/jobs/{id}` with TanStack Query
- [x] Show `enabled`/`disabled` badge or indicator prominently on the page (not just in edit form)
- [x] Show job config summary (source, destination, schedule, password status, retention, advanced options)
- [x] "Edit" button: opens `JobForm` pre-filled
- [x] "Run Now" button: calls `POST /jobs/{id}/run`; on 409 show inline error (not just toast) mentioning run already active; on success navigate to RunDetail
- [x] "Unlock" button:
  - [x] Disabled when any run in the runs list has `status=running`
  - [x] Disabled when any run has `check_status=null` (verification in progress)
  - [x] On click: calls `POST /jobs/{id}/unlock`
  - [x] On 409: show error toast mentioning "active run"
  - [x] On success: display the `output` text — must show text matching `/removed.*lock|successfully|output/i`
- [x] Tabs: **Runs**, **Snapshots**, and **Settings** (or "Configuration") — three tabs total
- [x] **Runs tab**: fetch `GET /api/jobs/{id}/runs`, table with status badge, duration, triggered by, started at; row click navigates to `/runs/{id}`
- [x] **Snapshots tab**: render `SnapshotList` with data from `GET /api/jobs/{id}/snapshots`
- [x] **Settings/Configuration tab**: display at minimum `source_label` and `schedule_value` (the current job configuration values)
- [x] Restore snippet: for each snapshot show a collapsible `restic restore` command (with repo path, snapshot ID, password env var placeholder)
- [x] 404 handling: redirect to `/jobs` if job not found
- [x] 500/other error handling: show text matching `/error|failed|could not load/i`
- [x] "Run Now" 409: show text matching `/already.*running|in progress|409/i`

### 8.4 `RunDetail` (`src/pages/RunDetail.tsx`)

- [x] Fetch `GET /api/runs/{id}` with TanStack Query
- [x] Show run header: job name (link), status badge, triggered by text, started at, duration
- [x] Duration formatted as human-readable (e.g. "3 min", "45 sec") — not raw seconds
- [x] `triggered_by` displayed as readable text (e.g. "Manual", "Scheduler")
- [x] `snapshot_id` displayed (first 8 chars) or `"—"` / `"N/A"` when null
- [x] Show stats section (files new/changed/unmodified, data added) when available
- [x] `data_added_bytes` formatted as GB or MB (not raw bytes)
- [x] Show `backup_output` in a scrollable pre block when status=success
- [x] Show `error_output` in a scrollable pre block when status=failed
- [x] When `error_output` contains "locked": show callout with text matching `/locked|repository.*lock|unlock/i`
- [x] Show `prune_status` badge for **all** states — `passed`, `failed`, `skipped` (not only on failure); show `prune_error_output` when `prune_status=failed`
- [x] Show `check_status` badge for all states; show `check_error_output` when `check_status=failed`
- [x] Poll while `status=running` OR (`status` terminal **and** `check_status` is `null`); stop only when `check_status` is non-null (this applies even when `status=failed`)
- [x] When `reason=overlapping_run`: show info card with text matching `/overlapping|already.*running|skipped.*previous/i`
- [x] When `reason=container_restart`: show info card with text matching `/container.*restart|was skipped.*restart/i`
- [x] 404 handling: show text matching `/not found|does not exist|404/i`
- [x] 500/other error handling: show text matching `/error|failed|could not load/i`

### 8.5 `Settings` (`src/pages/Settings.tsx`)

- [x] Fetch `GET /api/settings` with TanStack Query; show load error state if fetch fails
- [x] Form inputs are **controlled** — populated with current values from the API response on load
- [x] **Notifications section**: ntfy server URL (text input), topic (text input), token (masked/password input), per-event checkboxes: `notify_on_start`, `notify_on_success`, `notify_on_failure`, `notify_on_verification` — all four must be rendered
- [x] "Test" button: calls `POST /api/settings/test-ntfy`, shows success toast on `ok=true`, failure toast with error text on `ok=false`
- [x] **Defaults section**: default job timeout hours (number input, 1–168)
- [x] Save button: calls `PUT /api/settings`, shows success toast; shows inline error toast/message if save fails
- [x] **Restic section**: show current installed `restic_version` from settings; show text "not detected" when `restic_version` is `null`; "Check for update" button calls `GET /api/settings/restic-update-check`; display the **latest version string** from the response; show "update available" banner when `update_available=true`; show "up to date" when `update_available=false`; show "unavailable" or similar when `update_available=null`
- [x] **Rename Destination** section:
  - [x] Fetch `GET /mounts/destinations` with TanStack Query to populate the old-label select
  - [x] Old label: a `<select>` element populated from the mounted destinations list
  - [x] New label: a plain text `<input>` (not a select) for the user to type the new label
  - [x] "Rename" button: `role="button"`, label matching `/rename/i`; calls `POST /mounts/destinations/rename`
  - [x] On 409: show message matching `/already exists|conflict|409/i`
  - [x] On 404: show message matching `/not found|no longer.*exist|404/i`
  - [x] On 422: show message matching `/invalid|validation|422/i`
  - [x] On success: show text matching `/2.*job|job.*updated|affected/i` (count or "affected" keyword)
- [x] Show inline validation errors from API (422)

---

## 9. Frontend — Tests

### 9.1 Tests that will pass once components/pages are implemented

- [x] `RunStatusBadge.test.tsx` — colour and label for all 4 statuses plus check status
- [x] `ScheduleInput.test.tsx` — cron vs interval toggle, validation, preview rendering
- [x] `SnapshotList.test.tsx` — table headers, size formatting (B/KB/MB/GB/null), empty state
- [x] `JobForm.test.tsx` — all sections render, password lock, destination immutability, source change warning, conflict banner, submit behaviour
- [x] `Dashboard.test.tsx` — stats cards, recent runs table, polling behaviour, error state
- [x] `Jobs.test.tsx` — table rendering, enable/disable toggle, Run Now, delete, create
- [x] `JobDetail.test.tsx` — tabs, unlock button, restore snippets, run list
- [x] `RunDetail.test.tsx` — output blocks by status, polling, reason explanations
- [x] `Settings.test.tsx` — ntfy form, test-ntfy, update check, rename destination

---

## 10. Logging Enhancements — Request Traceability

### 10.1 Problem Statement

Current logging implementation logs function names, inputs, outputs, and HTTP requests, but lacks **request ID correlation**. Without a unique request ID propagated through the entire call stack, it's impossible to trace all logs from a single user request as it flows through multiple async functions. This makes debugging production issues difficult.

Example problem: If a user triggers a backup, you can see "POST /api/jobs/123/run → 202", but then you cannot correlate the restic, prune, check, and notification logs that happen asynchronously 5 minutes later.

### 10.2 Solution Design

Use Python's `contextvars` module to generate and store a unique request ID (UUID4) for each HTTP request, then propagate it through the async context and include it in every log line.

### 10.3 Implementation Tasks

- [ ] **10.3.1 Add contextvars to logging.py**: Import `contextvars`, create a `ContextVar("request_id")` that stores the current request ID (default `None`); create a `get_request_id()` function that returns the current value

- [ ] **10.3.2 Modify RequestLoggingMiddleware**: Generate a unique request ID using `uuid.uuid4().hex[:12]` (12-char short form) on each HTTP request; set it in the context variable at the start of request processing; include it in the initial request log; ensure it persists through the async context

- [ ] **10.3.3 Update logging format**: Change `basicConfig` format string from `"%(asctime)s %(levelname)s %(name)s %(message)s"` to include request ID field: `"%(asctime)s [%(request_id)s] %(levelname)s %(name)s:%(funcName)s - %(message)s"` (or similar readable format)

- [ ] **10.3.4 Implement LogRecord filter**: Create a custom `logging.Filter` subclass that injects the current request ID into every LogRecord via `record.request_id = get_request_id() or "none"`; register this filter on the root logger in `setup_logging()`

- [ ] **10.3.5 Update @log_call decorator**: Ensure the decorator logs correctly with the new format (no changes needed if the filter is working); verify in tests that request ID appears in log output

- [ ] **10.3.6 Test request traceability**: Write a test that:
  - Simulates an HTTP request to a route that triggers `backup_runner.run_backup()`
  - Captures all log output
  - Verifies that every log line (from RequestLoggingMiddleware, backup_runner, restic, notifications) contains the **same request ID**
  - Example assertion: `assert all(request_id in line for line in log_lines)`

- [ ] **10.3.7 Document logging format**: Update CLAUDE.md section 2.1 to describe the new request ID traceability feature; include example log output showing a complete request flow with consistent request ID

- [ ] **10.3.8 Verify existing tests still pass**: Run full test suite (`pytest`) to ensure LogRecord filter doesn't break any existing tests or log assertions

### 10.4 Expected Logging Output

After implementation, a complete backup request flow should produce logs like:

```
2026-05-17 10:23:45.123 [abc1234567f8] INFO app.api.routes.jobs:post_run - POST /api/jobs/job-456/run → 202
2026-05-17 10:23:45.125 [abc1234567f8] INFO app.services.backup_runner:run_backup - step=acquire_lock job_id=job-456
2026-05-17 10:23:45.126 [abc1234567f8] INFO app.services.backup_runner:run_backup - step=create_run run_id=run-789
2026-05-17 10:23:46.234 [abc1234567f8] DEBUG app.services.restic:restic_cat_config - restic_cat_config called args=(...)
2026-05-17 10:23:46.245 [abc1234567f8] DEBUG app.services.restic:restic_cat_config - restic_cat_config returned (0, '...', '')
2026-05-17 10:23:47.456 [abc1234567f8] INFO app.services.backup_runner:run_backup - step=backup source=/sources/documents
2026-05-17 10:23:52.789 [abc1234567f8] INFO app.services.notifications:send_notification - sending notification to https://ntfy.sh/documents-backups
```

All logs carry `abc1234567f8`, making the entire transaction traceable with: `grep "abc1234567f8" app.log`

### 10.5 Non-functional Requirements

- Request ID must be **12 characters** (UUID4 hex substring) to keep logs compact
- Request ID must be **immutable** once set for a given request — never change or regenerate
- Context variable must be **cleaned up** or isolated between concurrent requests (asyncio + contextvars handles this automatically)
- Format string must be **backward compatible** — adding request ID doesn't break parsing or tooling
- Logging level and existing log messages must **not change** — only format changes

---

## 11. Infrastructure

### 11.1 Dockerfile

- [ ] Stage 1 `frontend-builder` (`node:22-alpine`): `npm ci`, `npm run build`, output `/frontend/dist/`
- [ ] Stage 2 `restic-fetcher` (`alpine:3.21`): download restic binary, verify SHA256, decompress; accepts `RESTIC_VERSION` and `RESTIC_ARCH` build args; fail on unsupported arch
- [ ] Stage 3 `python-builder` (`python:3.12-alpine`): create venv, `pip install -r requirements.txt`
- [ ] Stage 4 runtime (`python:3.12-alpine`): copy from stages 1–3 and backend source, set `RESTIC_CACHE_DIR=/app/data/restic-cache`, entrypoint runs `alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 12345`
- [ ] Confirm `docker build --build-arg RESTIC_ARCH=arm64 -t backup-server .` succeeds (or `amd64`)
- [ ] Confirm expected image size ~165–190 MB

### 11.2 docker-compose (production)

- [ ] Write `docker-compose.yml` at repo root (separate from `.devcontainer/docker-compose.yml`)
- [ ] Expose port 12345
- [ ] Mount `/sources/{label}:ro` for each source
- [ ] Mount `/destinations/{label}:rw` for each destination
- [ ] Mount `/app/data:rw` for SQLite + restic cache
- [ ] Set `LOG_LEVEL`, `RESTIC_CACHE_DIR` environment variables
- [ ] Document how to configure Traefik labels (basicAuth, TLS) in README

### 11.3 End-to-end smoke test

- [ ] Start the container locally with a real source directory and destination
- [ ] Create a job via the UI, trigger a manual run
- [ ] Confirm run completes with `status=success`
- [ ] Confirm snapshot appears in the snapshots tab
- [ ] Confirm ntfy notification is delivered (if configured)
- [ ] Confirm `restic check` passes after a run with `check_enabled=True`

---

## 12. Polish and Documentation

- [ ] Write root `README.md`: what this is, docker-compose quick-start, env vars, Traefik config example, how to update restic version
- [ ] Add `RESTIC_ARCH` to Dockerfile `ARG` with a sensible error if not set
- [ ] Verify `ruff format . && ruff check --fix .` is clean
- [ ] Verify `npm run build` (tsc + vite) has zero errors
- [ ] Verify all 157 backend route-layer tests still pass after service layer is implemented
- [ ] Verify all 74 backend service-layer tests pass after restic.py and backup_runner.py are implemented
- [ ] Verify all frontend tests pass after all pages/components are implemented

---

## Summary

| Area                              | Done        | Remaining    |
| --------------------------------- | ----------- | ------------ |
| DB models & engine                | 6/6         | 0            |
| Alembic migration                 | 3/3         | 0            |
| Core (logging, scheduler, config) | 10/10       | 0            |
| API schemas                       | 12/12       | 0            |
| API routes                        | 18/18       | 0            |
| Main.py wiring                    | 7/7         | 0            |
| `restic.py` functions             | 9/9         | 0            |
| `backup_runner.py` steps          | 21/21       | 0            |
| Backend route-layer tests         | 157/157     | 0            |
| Backend service-layer tests       | 73/73       | 0            |
| **Backend Total**                 | **230/230** | **0** ✅     |
| Frontend shadcn/ui setup          | 4/4         | 0            |
| Frontend components               | 30/30       | 0 ✅         |
| Frontend component tests          | 4/4 files   | 0 ✅ (73/73) |
| Frontend pages                    | 0/47        | 47           |
| Frontend page tests               | 0/5 files   | 5 test files |
| Dockerfile                        | 0/6         | 6            |
| docker-compose production         | 0/5         | 5            |
| E2E smoke test                    | 0/5         | 5            |
| Polish & docs                     | 0/7         | 7            |

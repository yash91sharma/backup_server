# Design Document: Backup Server

**Status**: In review  
**Last Updated**: May 6, 2026

---

## 1. Overview

A self-hosted backup orchestration service for the home lab. Wraps [restic](https://restic.net/) with a web UI for scheduling, monitoring, and notification. Runs as a single container deployed via docker-compose behind Traefik.

---

## 2. Engineering Design

### 2.1 Backend: Python 3.12 + FastAPI

Python was chosen for subprocess-heavy orchestration — `asyncio.subprocess` keeps restic calls non-blocking, and APScheduler integrates natively with FastAPI's async event loop. FastAPI gives native async, auto-generated OpenAPI docs at `/api/docs`, and Pydantic validation as the single source of truth for request/response shapes.

### 2.2 Scheduler: APScheduler 4.x

Uses a `MemoryJobStore` — not `SQLAlchemyJobStore`. `BackupJob` is the single durable source of truth for every scheduled job. APScheduler's persistent store would duplicate that state and create a second source that can drift after migrations or failed writes. With `MemoryJobStore`, the scheduler is rebuilt deterministically from `BackupJob` rows on every startup, and the same startup path is exercised every restart. The only trade-off: an `IntervalTrigger` fires relative to container-start time after a restart rather than exactly N hours after the last run — acceptable for a home-lab scheduler.

Supports `CronTrigger` (cron expressions) and `IntervalTrigger` (every N hours/days). Celery + Redis was ruled out as too heavy for this scope.

### 2.3 Database: SQLite + SQLAlchemy + Alembic

SQLite in WAL mode: zero infra, no sidecar, consistent with the home lab pattern (Vikunja uses SQLite). At expected scale (<50 jobs, <50,000 run records) it handles concurrent reads comfortably. Alembic handles schema migrations so the container can be updated without manual DB changes.

### 2.4 Frontend: React 18 + Vite + TypeScript + shadcn/ui

shadcn/ui gives a professional, minimal admin-panel look. TanStack Query handles server state, polling, and cache invalidation. Vite produces faster builds and smaller bundles than CRA. Built static files are copied into the Python container in the multi-stage build — no Node in production.

### 2.5 Single Container

Frontend static files are served by FastAPI (`StaticFiles`). One Dockerfile, one image, one port, one container. No nginx sidecar.

---

## 3. Architecture

```
┌────────────────────────────────────────────────────┐
│                   Docker Container                 │
│  ┌─────────────────────────────────────────────┐   │
│  │  FastAPI (port 12345)                       │   │
│  │  ├── /api/*         REST API                │   │
│  │  ├── /              → static/index.html     │   │
│  │  └── /static/*      built React app         │   │
│  │                                             │   │
│  │  APScheduler (in-process)                   │   │
│  │  └── job triggers → BackupRunner            │   │
│  │       ├── restic subprocess                 │   │
│  │       ├── BackupRun DB record               │   │
│  │       └── ntfy notification                 │   │
│  │                                             │   │
│  │  SQLite (WAL mode)  /app/data/backup.db     │   │
│  └─────────────────────────────────────────────┘   │
│                                                    │
│  Volumes:                                          │
│  /sources/{label}        ← host paths (read-only)  │
│  /destinations/{label}   ← backup drive (rw)       │
│  /app/data               ← SQLite DB + restic cache│
└────────────────────────────────────────────────────┘
```

---

## 4. Repository Structure

```
/Users/yash/Dev/backup_server/
├── backend/
│   ├── app/
│   │   ├── main.py                  # FastAPI app, StaticFiles mount, CORS
│   │   ├── api/
│   │   │   ├── deps.py              # DB session dependency injection
│   │   │   └── routes/
│   │   │       ├── jobs.py          # CRUD + trigger for BackupJob
│   │   │       ├── runs.py          # Paginated run history + full log
│   │   │       ├── snapshots.py     # restic snapshots --json per job
│   │   │       ├── mounts.py        # Scan /sources/* and /destinations/*
│   │   │       └── settings.py      # ntfy config (singleton AppSettings row)
│   │   ├── core/
│   │   │   ├── config.py            # Pydantic BaseSettings (env vars)
│   │   │   └── scheduler.py         # APScheduler lifecycle, job registration
│   │   ├── db/
│   │   │   ├── database.py          # SQLAlchemy engine, session factory, WAL config
│   │   │   └── models.py            # ORM: BackupJob, BackupRun, AppSettings
│   │   └── services/
│   │       ├── restic.py            # Subprocess wrapper: init, backup, snapshots, forget, unlock
│   │       ├── backup_runner.py     # Orchestrates a full run (calls restic.py, updates DB)
│   │       └── notifications.py     # ntfy HTTP POST
│   ├── tests/
│   │   ├── conftest.py
│   │   ├── test_jobs.py
│   │   ├── test_runs.py
│   │   ├── test_backup_runner.py
│   │   ├── test_scheduler.py
│   │   ├── test_restic.py
│   │   ├── test_mounts.py
│   │   └── test_settings.py
│   ├── alembic/
│   ├── alembic.ini
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx                  # React Router setup
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx
│   │   │   ├── Jobs.tsx
│   │   │   ├── JobDetail.tsx
│   │   │   ├── RunDetail.tsx
│   │   │   └── Settings.tsx
│   │   ├── components/
│   │   │   ├── JobForm.tsx
│   │   │   ├── ScheduleInput.tsx
│   │   │   ├── SnapshotList.tsx
│   │   │   └── RunStatusBadge.tsx
│   │   └── lib/
│   │       ├── api.ts               # Fetch client with base URL
│   │       └── types.ts
│   ├── package.json
│   ├── tsconfig.json
│   └── vite.config.ts               # base: '/' and proxy /api → backend in dev
├── Dockerfile
├── .dockerignore
└── plan.MD
```

---

## 5. Data Models

### `BackupJob`

| Field                        | Type                                              | Notes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| ---------------------------- | ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `id`                         | UUID PK                                           | **Immutable. Also the restic repo directory name.** Repo path: `/destinations/{destination_label}/{id}` — computed at runtime, never stored. UUID (not name) as the path means the display name is freely editable                                                                                                                                                                                                                                                                                                                   |
| `name`                       | String                                            | Display label. Freely editable                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `source_label`               | String                                            | Subdirectory of `/sources/`. Mutable — changing redirects future backups to `/sources/{new_label}`. UI shows a warning banner on change                                                                                                                                                                                                                                                                                                                                                                                              |
| `source_subpath`             | String (nullable)                                 | One direct subdirectory inside the source mount (e.g. `photos`). No slashes — one level only. Null = entire mount. Set via UI picker, never typed by hand                                                                                                                                                                                                                                                                                                                                                                            |
| `destination_label`          | String                                            | Subdirectory of `/destinations/`. **Immutable after creation** — part of the on-disk repo path                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `restic_password`            | String                                            | Encryption password for this job's restic repository. **Required. Immutable after the first successful run** — the repo is initialised with this password at `restic init`; changing the field afterwards does not change the repo password and will cause all future runs to fail. To rotate: use `restic key add/remove` then update this field. Stored plaintext in SQLite (same risk profile as the ntfy token — acceptable for a local home lab). Never logged or passed anywhere except as `RESTIC_PASSWORD` in subprocess env |
| `schedule_type`              | Enum: `cron` / `interval`                         |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `schedule_value`             | String                                            | Cron expression or `"6h"`, `"1d"`, `"30m"`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `enabled`                    | Boolean                                           | Controls APScheduler registration                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| **Retention**                |                                                   |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `retain_keep_last`           | Int (nullable)                                    | `--keep-last N`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `retain_keep_hourly`         | Int (nullable)                                    | `--keep-hourly N`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `retain_keep_daily`          | Int (nullable)                                    | `--keep-daily N`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `retain_keep_weekly`         | Int (nullable)                                    | `--keep-weekly N`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `retain_keep_monthly`        | Int (nullable)                                    | `--keep-monthly N`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `retain_keep_yearly`         | Int (nullable)                                    | `--keep-yearly N`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `retain_keep_within`         | String (nullable)                                 | `--keep-within <duration>`, e.g. `30d`, `6m`, `1y`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `retain_keep_within_hourly`  | String (nullable)                                 | `--keep-within-hourly <duration>`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `retain_keep_within_daily`   | String (nullable)                                 | `--keep-within-daily <duration>`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `retain_keep_within_weekly`  | String (nullable)                                 | `--keep-within-weekly <duration>`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `retain_keep_within_monthly` | String (nullable)                                 | `--keep-within-monthly <duration>`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `retain_keep_within_yearly`  | String (nullable)                                 | `--keep-within-yearly <duration>`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| **Backup behaviour**         |                                                   |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `exclude_patterns`           | JSON array of String (nullable)                   | One `--exclude <pattern>` per entry, e.g. `node_modules/`, `*.tmp`                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `exclude_caches`             | Boolean                                           | `--exclude-caches`. Default: false                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `exclude_if_present`         | JSON array of String (nullable)                   | One `--exclude-if-present <filename>` per entry, e.g. `.nobackup`                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `one_file_system`            | Boolean                                           | `--one-file-system`. Default: false                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `no_scan`                    | Boolean                                           | `--no-scan` — skip pre-scan. Default: false                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `tags`                       | JSON array of String (nullable)                   | One `--tag <tag>` per entry                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `compression`                | Enum: `auto` / `max` / `off` (nullable)           | `--compression`. Null = not passed (restic defaults to `auto`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `pack_size`                  | Int (nullable)                                    | `--pack-size <MiB>`. Null = not passed (restic default: 128 MiB)                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `read_concurrency`           | Int (nullable)                                    | `--read-concurrency N`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `timeout_hours`              | Int (nullable)                                    | Max hours for the `restic backup` subprocess before kill + `status=failed`. Null = `AppSettings.default_job_timeout_hours`. Independent of `check_timeout_hours`                                                                                                                                                                                                                                                                                                                                                                     |
| **Post-backup verification** |                                                   |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `check_enabled`              | Boolean                                           | Run `restic check` after every successful backup. Default: false                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `check_mode`                 | Enum: `structural` / `subset` / `full` (nullable) | `structural` = index-only (seconds, no data reads); `subset` = random % of pack files (~25–30 min for 3 TB at 5%); `full` = every pack file (~8–11 h for 3 TB)                                                                                                                                                                                                                                                                                                                                                                       |
| `check_subset_percent`       | Int (nullable)                                    | % of packs to read when `check_mode=subset`. Range: 1–100                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `check_timeout_hours`        | Int (nullable)                                    | Max hours for the `restic check` subprocess before kill. On timeout: `check_status=failed`, `check_error_output="timed out after N hours"`, run `status` stays `success` (check is non-fatal). Null = `AppSettings.default_job_timeout_hours`. Independent of `timeout_hours`                                                                                                                                                                                                                                                        |
| `created_at`                 | DateTime                                          |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `updated_at`                 | DateTime                                          |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |

> **Identity constraints:**
>
> - **`destination_label`**: immutable from creation — forms the on-disk repo path with the job UUID
> - **`restic_password`**: immutable after the first successful run — baked into the repo at `restic init`
> - All other fields (including `name`, `source_label`, `source_subpath`) are freely editable

---

### `BackupRun`

| Field                     | Type                                               | Notes                                                                                                                                                                                                                                                        |
| ------------------------- | -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `id`                      | UUID PK                                            |                                                                                                                                                                                                                                                              |
| `job_id`                  | UUID FK                                            | Cascade delete                                                                                                                                                                                                                                               |
| `status`                  | Enum: `running` / `success` / `failed` / `skipped` | `skipped` = trigger fired but run not started. `failed` includes normal failures and the startup-recovery case                                                                                                                                               |
| `reason`                  | String (nullable)                                  | `overlapping_run` (status=skipped) — another run was in progress when trigger fired; `container_restart` (status=failed) — run was active when container stopped, marked failed at next startup. Null for normal failures where `error_output` has the cause |
| `started_at`              | DateTime                                           |                                                                                                                                                                                                                                                              |
| `finished_at`             | DateTime (nullable)                                | Null while running. Equal to `started_at` for skipped runs. Set to cleanup time for `reason=container_restart`                                                                                                                                               |
| `duration_seconds`        | Int (nullable)                                     | Null for skipped and `reason=container_restart` runs (actual duration unknown)                                                                                                                                                                               |
| `snapshot_id`             | String (nullable)                                  | Null for failed/skipped runs                                                                                                                                                                                                                                 |
| `files_new`               | Int (nullable)                                     | From restic JSON summary. Null for skipped or pre-output-failed runs                                                                                                                                                                                         |
| `files_changed`           | Int (nullable)                                     |                                                                                                                                                                                                                                                              |
| `files_unmodified`        | Int (nullable)                                     |                                                                                                                                                                                                                                                              |
| `dirs_new`                | Int (nullable)                                     |                                                                                                                                                                                                                                                              |
| `dirs_changed`            | Int (nullable)                                     |                                                                                                                                                                                                                                                              |
| `dirs_unmodified`         | Int (nullable)                                     |                                                                                                                                                                                                                                                              |
| `data_added_bytes`        | BigInt (nullable)                                  | Post-compression bytes added. From restic `data_added`                                                                                                                                                                                                       |
| `data_added_packed_bytes` | BigInt (nullable)                                  | Wire-size bytes added to pack files. From restic `data_added_packed` (restic ≥ 0.16). Null on older versions                                                                                                                                                 |
| `total_bytes_processed`   | BigInt (nullable)                                  | Total source bytes scanned                                                                                                                                                                                                                                   |
| `backup_output`           | Text (nullable)                                    | Full stdout from `restic backup --verbose --json` on success — file-level change stream + final JSON summary. Null on failed/skipped                                                                                                                         |
| `error_output`            | Text (nullable)                                    | Captured stdout + stderr on failure. Null on success, skipped, and `reason=container_restart`                                                                                                                                                                |
| `prune_status`            | Enum: `passed` / `failed` / `skipped` (nullable)   | Null only while running                                                                                                                                                                                                                                      |
| `prune_error_output`      | Text (nullable)                                    | Populated when `prune_status=failed`                                                                                                                                                                                                                         |
| `check_status`            | Enum: `passed` / `failed` / `skipped` (nullable)   | `skipped` when `check_enabled=false` or backup was not successful. Null only while running — always set on completion                                                                                                                                        |
| `check_error_output`      | Text (nullable)                                    | Populated when `check_status=failed`                                                                                                                                                                                                                         |
| `triggered_by`            | Enum: `scheduler` / `manual`                       |                                                                                                                                                                                                                                                              |

---

### `Snapshot`

One row per restic snapshot, written after every successful backup. The Snapshots UI reads entirely from this table — no live `restic snapshots` calls on page load.

| Field           | Type                            | Notes                                                                     |
| --------------- | ------------------------------- | ------------------------------------------------------------------------- |
| `id`            | UUID PK                         |                                                                           |
| `job_id`        | UUID FK                         | Cascade delete                                                            |
| `run_id`        | UUID FK (nullable)              | The run that created this snapshot. Null for manually re-synced snapshots |
| `snapshot_id`   | String                          | Short restic snapshot ID (e.g. `a1b2c3d4`)                                |
| `snapshot_time` | DateTime                        | Timestamp from restic                                                     |
| `hostname`      | String                          |                                                                           |
| `paths`         | JSON array of String            | e.g. `["/sources/documents"]`                                             |
| `tags`          | JSON array of String (nullable) |                                                                           |
| `size_bytes`    | BigInt (nullable)               | `Summary.total_size` from restic                                          |
| `captured_at`   | DateTime                        | Set to the run's `finished_at`                                            |

Snapshot rows are never deleted directly by the app. After `restic forget --prune`, the next successful backup's snapshot-reconciliation step (`restic snapshots --json`) reconciles the table: deletes rows for pruned snapshot IDs, upserts rows for current ones. This is the only mechanism that writes to the `Snapshot` table.

---

### `AppSettings` (singleton, row id=1)

| Field                       | Type              | Notes                                                                                                                                                                                                                                           |
| --------------------------- | ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ntfy_server_url`           | String            | Default: `https://ntfy.sh`                                                                                                                                                                                                                      |
| `ntfy_topic`                | String            |                                                                                                                                                                                                                                                 |
| `ntfy_token`                | String (nullable) | For private topics                                                                                                                                                                                                                              |
| `notify_on_start`           | Boolean           | Default: true                                                                                                                                                                                                                                   |
| `notify_on_success`         | Boolean           | Default: true                                                                                                                                                                                                                                   |
| `notify_on_failure`         | Boolean           | Default: true                                                                                                                                                                                                                                   |
| `notify_on_verification`    | Boolean           | Default: true                                                                                                                                                                                                                                   |
| `restic_version`            | String            | Detected at startup via `restic version`; updated in DB if changed                                                                                                                                                                              |
| `default_job_timeout_hours` | Int               | Default: 24. Applied to both `restic backup` and `restic check` when a job's own timeout field is null. Rationale: a first-time full backup of 2–3 TB over 1 Gbps takes ~8–11 h; 24 h gives headroom for both backup and full-mode verification |

---

## 6. API Design

**Base path**: `/api`

**Duplicate job validation**: `POST /jobs` and `PUT /jobs/{id}` check for an existing job with the same `source_label` + `source_subpath` + `destination_label`. Conflict returns `409` with the conflicting job's name and ID. No client-side pre-check — the server is the sole enforcement point.

| Method | Path                              | Description                                                                                                                                   |
| ------ | --------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/jobs`                           | List all jobs with last run summary                                                                                                           |
| POST   | `/jobs`                           | Create job. Requires `restic_password`. Returns `409` on duplicate source+destination                                                         |
| GET    | `/jobs/{id}`                      | Job details                                                                                                                                   |
| PUT    | `/jobs/{id}`                      | Update job. Returns `422` if `destination_label` is changed (always immutable) or `restic_password` is changed after the first successful run |
| DELETE | `/jobs/{id}`                      | Delete job and remove from scheduler                                                                                                          |
| POST   | `/jobs/{id}/run`                  | Trigger immediate manual run                                                                                                                  |
| POST   | `/jobs/{id}/enable`               | Enable and register in scheduler                                                                                                              |
| POST   | `/jobs/{id}/disable`              | Disable and deregister                                                                                                                        |
| POST   | `/jobs/{id}/unlock`               | Run `restic unlock` using `BackupJob.restic_password` from DB. Returns subprocess output                                                      |
| GET    | `/jobs/{id}/runs`                 | Paginated run history                                                                                                                         |
| GET    | `/jobs/{id}/snapshots`            | Paginated `Snapshot` rows ordered by `snapshot_time` desc. DB-only, no restic call                                                            |
| GET    | `/runs/{id}`                      | Full run detail + log                                                                                                                         |
| GET    | `/mounts/sources`                 | Top-level mount labels under `/sources/`                                                                                                      |
| GET    | `/mounts/sources/{label}/subdirs` | Direct subdirectories of `/sources/{label}` — one level only                                                                                  |
| GET    | `/mounts/destinations`            | Top-level mount labels under `/destinations/`                                                                                                 |
| GET    | `/settings`                       | ntfy config + global defaults                                                                                                                 |
| PUT    | `/settings`                       | Update settings                                                                                                                               |
| POST   | `/settings/test-ntfy`             | Send test ntfy notification                                                                                                                   |
| GET    | `/health`                         | Scheduler state, restic version (from DB), DB status                                                                                          |
| GET    | `/settings/restic-update-check`   | Compare `AppSettings.restic_version` against GitHub Releases API. Returns `{ current, latest, update_available }`                             |

---

## 7. Scheduler Design

APScheduler runs as a singleton initialized on FastAPI startup via the `lifespan` context manager. Uses `MemoryJobStore` — `BackupJob` is the sole durable state; nothing can drift.

**Startup sequence (order matters):**

1. **Stale run cleanup** (before scheduler starts): find all `BackupRun` rows with `status=running` → set `status=failed`, `reason=container_restart`, `finished_at=now`, `duration_seconds=null`, `prune_status=skipped`, `check_status=skipped`. This prevents the concurrent run guard from permanently blocking a job after an unclean shutdown
2. `scheduler.start()` → load all `BackupJob` rows where `enabled=True` → register as in-memory APScheduler jobs

**Lifecycle:**

- `POST /jobs` → create DB row → register in APScheduler if `enabled`
- `PUT /jobs/{id}` → update DB row → remove + re-add APScheduler job
- `DELETE /jobs/{id}` → remove from APScheduler → `_job_locks.pop(job_id, None)` → delete DB rows (cascade to runs)
- `POST /jobs/{id}/enable` / `disable` → register / deregister

**Trigger types:**

- `cron`: `CronTrigger.from_crontab(schedule_value)`
- `interval`: `"6h"` → `IntervalTrigger(hours=6)`, `"1d"` → `IntervalTrigger(days=1)`, `"30m"` → `IntervalTrigger(minutes=30)`

**Concurrent run guard:** `backup_runner.py` maintains `_job_locks: dict[UUID, asyncio.Lock]` (module-level). Before starting a run, the runner acquires the per-job lock. Within the lock it checks for an existing `status=running` row — if none found, writes the `status=running` row, then releases the lock. This two-layer guard (in-memory lock + DB check + DB write, all inside the lock) prevents the TOCTOU race where two simultaneous APScheduler callbacks both read "no running row" and both start. The lock is released in a `finally` block immediately after the running row is committed — it is never held across a restic subprocess. If a running row is found: write `status=skipped`, `reason=overlapping_run`, `started_at=now`, `finished_at=now`, `prune_status=skipped`, `check_status=skipped` — release and return.

---

## 8. Restic Workflow per Backup Run

Each run in `backup_runner.py`:

**Step 1 — Concurrent run guard (acquire lock)**  
Acquire `_job_locks[job_id]` (created on first access). Within the lock:

- Check for existing `status=running` row for this job
- If found: write `status=skipped`, `reason=overlapping_run`, `started_at=now`, `finished_at=now`, `prune_status=skipped`, `check_status=skipped` — release and return
- If not found: write `status=running` row — then release the lock
- Lock released in `finally` block. Never held past this step.

**Step 2 — Validate password**  
If `restic_password` is null/empty: update the step-1 row to `status=failed`, `error_output="No restic password configured for this job."`, `finished_at=now`, `prune_status=skipped`, `check_status=skipped` — return. (Password validation is outside the lock.)

**Step 3 — Notify: backup started**  
Fire-and-forget ntfy with job name, source, destination, triggered-by.

**Step 4 — Init check**  
Run `restic cat config`. If it fails, run `restic init`. Both use `RESTIC_PASSWORD` from the job record.

**Step 5 — Backup**  
`restic backup <source_path> --json --verbose [flags]` — all non-null job fields produce CLI flags dynamically; null fields produce no flags. Wrapped in `asyncio.wait_for(timeout=(job.timeout_hours or settings.default_job_timeout_hours) * 3600)`. On timeout: SIGTERM → SIGKILL, `status=failed`, `error_output="Backup subprocess timed out after N hours"`, `prune_status=skipped`, `check_status=skipped`.

**Step 6 — Parse output**  
Extract final JSON summary line from restic stdout.

**Step 7 — Update run stats**  
Populate `files_new/changed/unmodified`, `dirs_new/changed/unmodified`, `data_added_bytes`, `data_added_packed_bytes`, `total_bytes_processed`, `snapshot_id`. Store full captured stdout in `backup_output` (success path only).

**Step 8 — Prune**  
`restic forget --prune [retention flags]`. If all retention fields are null, runs with no flags — prunes orphaned pack files but forgets no snapshots (safe, intentional). `prune_status=passed/failed`; failure is non-fatal (run status unchanged), output stored in `prune_error_output`.

**Step 9 — Reconcile snapshots**  
`restic snapshots --json` → delete `Snapshot` rows no longer returned (pruned in step 8), upsert current ones, set `run_id` on the new snapshot's row.

**Step 10 — Finalise run**  
Set `status=success/failed`, `finished_at`, `duration_seconds`. On failure: store `error_output`, set `prune_status=skipped` and `check_status=skipped` (steps 8–9 and 12 not reached), leave `backup_output` null.

**Step 11 — Notify: backup complete**  
Fire-and-forget ntfy with status, duration, files changed, data added. On failure: includes excerpt from `error_output`.

**Step 12 — Integrity check** (only if `check_enabled=true` and `status=success`)

- Notify: verification started
- Run according to `check_mode`:
  - `structural`: `restic check` — index integrity only, no data reads
  - `subset`: `restic check --read-data-subset=N%`
  - `full`: `restic check --read-data`
- Wrapped in `asyncio.wait_for(timeout=(job.check_timeout_hours or settings.default_job_timeout_hours) * 3600)`
- On timeout: `check_status=failed`, `check_error_output="Verification subprocess timed out after N hours"`. **Run `status` stays `success` — check is non-fatal**
- `check_status=passed/failed`; failure output in `check_error_output`
- Notify: verification complete (passed/failed)

`restic_password` is passed as `RESTIC_PASSWORD` env var to all subprocesses — never written to logs, DB output fields, or filesystem. `RESTIC_CACHE_DIR=/app/data/restic-cache` so the cache persists across container restarts.

---

## 9. Mount Strategy

Container mount conventions:

- **Sources**: `/sources/{label}` — always `:ro`
- **Destinations**: `/destinations/{label}` — `:rw`
- **App data**: `/app/data` — `:rw` (SQLite DB + restic cache)

`{label}` is whatever the user names the directory in `docker-compose.yaml`. The app discovers mounts at runtime via the `/mounts/*` endpoints.

> **Constraints:**
>
> - Every source must be under `/sources/<label>` (`:ro`); every destination under `/destinations/<label>` (`:rw`). Mounts outside these paths are invisible to the app
> - Labels become the display name in the UI — choose meaningful names (`nas`, `documents`, `usb_drive`)
> - **Destination labels are permanent** once a job references them — they are part of the restic repo path. Renaming a destination label in `docker-compose.yaml` without updating every referencing job orphans all existing snapshots
> - **Source labels can be changed** in both compose and the job config, but both must be updated together or runs will fail. UI shows a warning banner when `source_label` is edited on an existing job
> - **⚠ Disk space is not monitored.** Ensure destinations have enough free space before runs. A first-time full backup of 2–3 TB occupies that much space; running out mid-backup can corrupt the restic repository

`GET /mounts/sources/{label}/subdirs` returns immediate subdirectories only (one level — no recursive traversal) to populate the subpath picker. If a deeper path is needed (e.g. `photos/2024`), mount it as its own labelled source in `docker-compose.yaml`.

---

## 10. Security

| Concern              | Approach                                                                                                                                                          |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| UI access            | No app-level auth — delegated entirely to Traefik (`basicAuth`, IP allowlist, etc.)                                                                               |
| TLS                  | Traefik handles TLS using the existing Cloudflare wildcard certificate                                                                                            |
| Backup encryption    | AES-256 via restic. Per-job password stored plaintext in SQLite (same risk as ntfy token — acceptable locally). Passed as `RESTIC_PASSWORD` env var; never logged |
| ntfy token           | Stored plaintext in SQLite                                                                                                                                        |
| Container privileges | Runs as root — required to read mixed-owner source volumes (files owned by `postgres`, `www-data`, `root`, etc.)                                                  |
| Source mounts        | Always `:ro` — container cannot modify backup sources                                                                                                             |
| No Docker socket     | No privilege escalation path                                                                                                                                      |
| Input validation     | All inputs validated via Pydantic; cron expressions validated before acceptance                                                                                   |

---

## 11. Dockerfile (4-Stage)

Each stage produces one artifact; the final image copies only those artifacts — nothing from builder stages is inherited.

### Stage 1 — `frontend-builder` (`node:22-alpine`)

```dockerfile
FROM node:22-alpine AS frontend-builder
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci --prefer-offline
COPY frontend/ ./
RUN npm run build
# Output: /frontend/dist/
```

Node, npm, node_modules (~300 MB) are not in the final image.

### Stage 2 — `restic-fetcher` (`alpine:3.21`)

```dockerfile
FROM alpine:3.21 AS restic-fetcher
RUN apk add --no-cache curl bzip2
ARG RESTIC_VERSION=0.17.3
ARG RESTIC_ARCH
# Accepted values: arm64 (Mac Mini M-series, ARM64 Linux), amd64 (Intel/AMD x86-64)
RUN if [ "$RESTIC_ARCH" != "arm64" ] && [ "$RESTIC_ARCH" != "amd64" ]; then \
      echo "ERROR: RESTIC_ARCH must be 'arm64' or 'amd64' — got '${RESTIC_ARCH}'"; \
      exit 1; \
    fi
RUN curl -fsSL "https://github.com/restic/restic/releases/download/v${RESTIC_VERSION}/restic_${RESTIC_VERSION}_linux_${RESTIC_ARCH}.bz2" \
      -o restic.bz2 \
    && curl -fsSL "https://github.com/restic/restic/releases/download/v${RESTIC_VERSION}/SHA256SUMS" \
      -o SHA256SUMS \
    && grep "restic_${RESTIC_VERSION}_linux_${RESTIC_ARCH}.bz2" SHA256SUMS | sha256sum -c - \
    && bunzip2 restic.bz2 \
    && chmod 755 restic
# Output: /restic
```

### Stage 3 — `python-builder` (`python:3.12-alpine`)

```dockerfile
FROM python:3.12-alpine AS python-builder
RUN apk add --no-cache build-base
RUN python -m venv /venv
COPY backend/requirements.txt .
RUN /venv/bin/pip install --no-cache-dir -r requirements.txt
# Output: /venv/
```

### Stage 4 — Runtime (`python:3.12-alpine`)

```dockerfile
FROM python:3.12-alpine AS runtime
RUN apk add --no-cache ca-certificates

COPY --from=frontend-builder /frontend/dist    /app/static
COPY --from=restic-fetcher   /restic           /usr/local/bin/restic
COPY --from=python-builder   /venv             /venv
COPY backend/app             /app/app
COPY backend/alembic         /app/alembic
COPY backend/alembic.ini     /app/alembic.ini

ENV PATH="/venv/bin:$PATH"
ENV RESTIC_CACHE_DIR="/app/data/restic-cache"

WORKDIR /app
EXPOSE 12345
ENTRYPOINT ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 12345"]
```

### `.dockerignore`

```
.git/
node_modules/
frontend/.vite/
frontend/dist/
**/__pycache__/
**/*.pyc
**/.pytest_cache/
backend/tests/
*.env
```

### Expected Image Size

| Layer                                            | Approx.         |
| ------------------------------------------------ | --------------- |
| `python:3.12-alpine` base                        | ~55 MB          |
| `ca-certificates`                                | ~1 MB           |
| `/venv` (FastAPI, SQLAlchemy, APScheduler, etc.) | ~80–100 MB      |
| restic binary                                    | ~25 MB          |
| Frontend static files                            | ~3–5 MB         |
| App source                                       | ~1 MB           |
| **Total**                                        | **~165–190 MB** |

A naive single-stage image (Node + pip + build tools left in) would be ~700–900 MB.

---

## 12. Deployment docker-compose

Location: `/Users/yash/ys-apps/backup_server/docker-compose.yaml`

```yaml
services:
  backup-server:
    image: backup-server:latest
    container_name: backup-server
    environment:
      - TZ=America/Los_Angeles
    # ⚠ SECURITY: This port binding bypasses Traefik and all its middleware — anyone
    # on the LAN can access the UI unauthenticated. For Traefik-only access (recommended),
    # remove the ports block entirely. Keep only for initial setup or troubleshooting.
    ports:
      - '12345:12345'
    volumes:
      # Sources (read-only) — must be under /sources/<label>
      - /Users/yash/Documents:/sources/documents:ro
      - /Volumes/YashNAS:/sources/nas:ro
      # - /Users/yash/Photos:/sources/photos:ro

      # Destinations (read-write) — must be under /destinations/<label>
      - /Volumes/BackupDrive:/destinations/main:rw
      # - /Volumes/BackupDrive2:/destinations/offsite:rw

      # App data (SQLite + restic cache) — do not change this path
      - ./data:/app/data:rw
    restart: unless-stopped
    networks:
      - traefik_default

networks:
  traefik_default:
    external: true
```

`.env` at `/Users/yash/ys-apps/backup_server/.env`:

```
RESTIC_VERSION=0.17.3
RESTIC_ARCH=arm64
# arm64 — Mac Mini M-series or any ARM64 Linux host
# amd64 — Intel/AMD x86-64 host
# Build fails immediately with a clear error for any other value.
# Restic repo passwords are set per-job in the UI — not an env var.
```

`RESTIC_VERSION` and `RESTIC_ARCH` are passed as `--build-arg` at image build time. Update both when upgrading restic or moving hosts, then rebuild.

---

## 13. Traefik Integration

Add to `/Users/yash/ys-apps/traefik/dynamic_config/traefik_dynamic.yaml`:

```yaml
http:
  routers:
    backup-server:
      rule: 'Host(`backup-server.yashrma.xyz`)'
      entryPoints:
        - websecure
      service: backup-server
      tls:
        certResolver: cloudflare
      middlewares:
        - backup-server-auth

  services:
    backup-server:
      loadBalancer:
        servers:
          - url: 'http://192.168.30.11:12345'

  middlewares:
    backup-server-auth:
      basicAuth:
        users:
          - 'yash:$2y$05$...' # Generate with: htpasswd -nB yash
```

Swap the middleware for any preferred access control (IP allowlist, forward auth, etc.).

---

## 14. Frontend UI Pages

### Dashboard

Stats: total jobs, enabled count, last 10 runs with status badges, next scheduled run time per job, restic version (from DB). All data from DB — no restic calls. Persistent callout: _"Disk space is not monitored automatically — ensure destinations have sufficient free space before scheduled runs."_

### Jobs

Sortable table: name, source → destination, schedule, last run status + time, next run time, enable/disable toggle, Run Now, edit/delete. Edit button tooltip: _"You can edit all fields except the destination and (after the first backup) the restic password, which are permanent once set."_

### Job Detail

Config summary at top; three tabs:

- **Run History**: paginated (started, duration, files new/changed, data added, status)
- **Snapshots**: paginated `Snapshot` rows from `GET /jobs/{id}/snapshots` — snapshot time, ID, tags, paths, size. No live restic call
- **Restore Steps**: copy-pasteable `restic restore` commands with `RESTIC_PASSWORD` env var, `--target`, `--include` options, and a concrete example using the job's destination label

In the config summary, `destination_label` shows a 🔒 icon (_"Permanent — cannot be changed after creation"_). `restic_password` shows a 🔒 icon after the first successful run (_"Permanent after the first backup — to rotate, use restic key directly"_).

**Unlock Repository button** — always visible in the Job Detail header, styled secondary/outline. Tooltip: _"Use this if recent runs are failing with 'repository is already locked' — usually caused by the container stopping during an active backup. The repository password stored in this job's configuration will be used to authenticate the unlock operation."_ On click: confirmation dialog → `POST /jobs/{id}/unlock` → result as toast.

### Run Detail

Layout adapts to outcome:

- **`success`**: metadata card (duration, files new/changed/unmodified, dirs new/changed/unmodified, data added packed vs raw, snapshot ID). Collapsible _Backup log_ panel shows `backup_output` in monospace. If `prune_status=failed`: collapsible amber panel with `prune_error_output`
- **`failed`**: metadata card + scrollable `error_output`. If `prune_status=skipped`: muted note. If `error_output` contains `"repository is already locked"`: inline callout pointing to the Unlock button. If `reason=container_restart`: info card (_"This run was in progress when the container stopped and was automatically marked failed on next startup"_) — no error output panel
- **`skipped`**: reason badge (_"Skipped — another run was already in progress"_). No stats or log
- **`running`**: metadata card with live elapsed time; stats as dashes

### Settings

ntfy: server URL, topic, token (masked), four notification toggles, Send Test Notification button. Global timeout: `default_job_timeout_hours` numeric input (_"Default backup / verification timeout (hours)"_), tooltip: _"Maximum time any backup or verification subprocess can run before killed and marked failed. Applied to jobs without their own timeout. Default: 24 h."_ Restic version (from DB) + Check for Update button → `GET /settings/restic-update-check`.

### JobForm

Two-step source picker in _Basic_:

1. **Source mount** — dropdown from `GET /mounts/sources`; selecting triggers `GET /mounts/sources/{label}/subdirs`
2. **Subdirectory (optional)** — second dropdown with _"Entire mount"_ + returned subdirs. Hidden if mount has no subdirectories

**Restic password field** (required, masked):

- **Create mode**: editable
- **Edit mode, before first run**: editable, note _"No backups have run yet — you may still change this"_
- **Edit mode, after first run**: read-only (masked placeholder), 🔒 icon, tooltip _"Permanent — the repo was initialised with this password. To rotate, use `restic key add/remove` on the command line."_

**Source label change**: amber banner when `source_label` is changed on an existing job: _"Changing the source mount will redirect future backups to a different directory. Make sure the new source is correctly mounted in docker-compose.yaml."_

**409 Conflict**: blocking error banner at top of _Basic_ section with link to conflicting job. Form not resubmittable without changing an identity field.

**Edit mode**: `destination_label` read-only always, with inline notice: _"The destination cannot be changed after creation — it forms part of the restic repository path."_ Helper link: _"Need to change this? Delete this job and create a new one."_

**Form sections** (collapsible; _Basic_ always expanded):

- _Basic_ — name, source, destination, password, schedule
- _Retention Policy_ — all `--keep-*` and `--keep-within-*` fields
- _Backup Options_ — excludes, tags, compression, timeout, other flags
- _Verification_ — check settings

Every optional field has an **(optional)** label and a tooltip (ⓘ):

| Field                  | Tooltip                                                                                                                                                                                                             |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `retain_keep_last`     | _"Keep the N most recent snapshots regardless of age. Example: `5`"_                                                                                                                                                |
| `retain_keep_within`   | _"Keep all snapshots within this window. Example: `30d`. Accepts: `Nd`, `Nm`, `Ny`, `Nh`"_                                                                                                                          |
| `exclude_patterns`     | _"Glob patterns to skip, one per line. Example: `node_modules/`, `_.tmp`, `.DS_Store`"\*                                                                                                                            |
| `exclude_if_present`   | _"Skip a directory if it contains a file with this name. Example: `.nobackup`"_                                                                                                                                     |
| `exclude_caches`       | _"Skip directories containing a `CACHEDIR.TAG` file — standard marker used by browsers, package managers, and build tools"_                                                                                         |
| `one_file_system`      | _"Don't cross filesystem mount boundaries — useful when source contains network drives or sub-volumes to exclude"_                                                                                                  |
| `tags`                 | _"Labels attached to each snapshot. Example: `documents`, `weekly`"_                                                                                                                                                |
| `compression`          | _"`auto` compresses compressible data (default). `max` tries harder but is slower. `off` disables"_                                                                                                                 |
| `pack_size`            | _"Internal pack file size in MiB. Leave blank for restic default (128 MiB). Increase (e.g. `512`) for large repos to reduce destination file count"_                                                                |
| `read_concurrency`     | _"Source files read in parallel. Leave blank for restic's automatic default"_                                                                                                                                       |
| `no_scan`              | _"Skip pre-scan for total size estimate. Backup starts immediately but no progress percentage is shown"_                                                                                                            |
| `timeout_hours`        | _"Max time the backup can run before killed and marked failed. Leave blank for global default. Does not apply to verification — use Verification timeout for that"_                                                 |
| `check_enabled`        | _"Run integrity check after every successful backup. `structural` is fast (seconds). `subset` reads a data percentage (~25–30 min for large repos). `full` reads everything and is impractical after every backup"_ |
| `check_mode`           | _"`structural` — checks all referenced data exists, no disk reads. `subset` — reads and verifies a random % of packs (recommended). `full` — reads and verifies every pack file"_                                   |
| `check_subset_percent` | _"% of packs to read in subset mode. Example: `5` covers the repo statistically over 20 runs. Range: 1–100"_                                                                                                        |
| `check_timeout_hours`  | _"Max time for integrity check before killed and marked failed. Check failure is non-fatal — backup run stays success. Leave blank for global default. Note: subset at 5% on 3 TB ≈ 25–30 min; full ≈ 8–11 h"_      |

---

## 15. Testing Strategy

**TDD throughout**: tests are written against this spec before implementation. The suite must catch regressions, serve as living documentation, and give confidence to modify any module.

**Backend stack**: pytest + pytest-asyncio + httpx `AsyncClient`

**`conftest.py` fixtures**: fresh in-memory SQLite per test; `AsyncClient` with test DB injected via dependency override; all `restic.py` subprocess calls patched globally; ntfy calls patched; APScheduler in test mode with memory-only store.

---

### `test_jobs.py`

- `POST /jobs` creates job, registers in APScheduler when `enabled=True`; no registration when `enabled=False`
- `POST /jobs` returns `409` on duplicate source+subpath+destination; `422` when `restic_password` missing
- `PUT /jobs/{id}` updates all editable fields and reschedules APScheduler job
- `PUT /jobs/{id}` with changed `source_label` — succeeds and returns warning flag
- `PUT /jobs/{id}` with changed `destination_label` — returns `422`
- `PUT /jobs/{id}` with changed `restic_password` before first successful run — succeeds; after — `422`
- `PUT /jobs/{id}` with `restic_password` absent or empty — leaves stored password unchanged
- `PUT /jobs/{id}` duplicate check excludes the job being updated
- `DELETE /jobs/{id}` removes from APScheduler, pops `_job_locks`, cascades to `BackupRun` rows
- `GET /jobs` returns last run summary; `GET /jobs/{id}` returns full detail
- Enable/disable endpoints register/deregister from APScheduler
- `POST /jobs/{id}/run` triggers immediate run
- Null retention fields excluded from CLI; set fields included correctly
- Invalid cron → `422`; `check_subset_percent` outside 1–100 → `422`

### `test_runs.py`

- `GET /jobs/{id}/runs` — paginated, newest first, includes skipped runs with `reason`
- `GET /jobs/{id}/snapshots` — paginated, ordered by `snapshot_time` desc; no subprocess; empty list for jobs with no successful runs
- `GET /runs/{id}` for success — `error_output=null`, `backup_output` non-null, `prune_status` set, `check_status` set, all stat fields present
- `GET /runs/{id}` for failed — `error_output` populated, `backup_output=null`, `prune_status=skipped`, `check_status=skipped`
- `GET /runs/{id}` for skipped — `status=skipped`, `reason=overlapping_run`, null stats, both sub-statuses skipped
- `GET /runs/{id}` for container-restart — `status=failed`, `reason=container_restart`, `error_output=null`, `duration_seconds=null`
- `GET /runs/{id}` — includes `check_status` and `check_error_output` when check ran

### `test_backup_runner.py`

- Full happy path: `running → success`, all stat fields populated, `error_output=null`, `backup_output` non-null
- Step ordering: `status=running` row written inside lock before release; password validation updates (not creates) the running row to `failed`
- Missing `restic_password`: running row updated to `failed`; no subprocess called
- Restic `backup` failure: `status=failed`, `error_output` captured, `backup_output=null`, steps 8–9 and 12 skipped
- Restic `forget` failure: run stays `success` (non-fatal)
- Snapshot reconciliation: rows created/updated; pruned snapshots deleted
- Repo absent: `restic init` called; repo present: init not called
- Concurrent guard — lock path: two simultaneous callbacks → one `running`, one `skipped`
- Concurrent guard — DB path: existing `running` row → new `skipped` row
- Backup timeout: `status=failed`, timeout message in `error_output`, `prune_status=skipped`, `check_status=skipped`
- Job `timeout_hours` overrides `default_job_timeout_hours` for backup
- `check_enabled=False`: `check_status=skipped` (never null on a completed run)
- Prune success → `prune_status=passed`; failure → `prune_status=failed`, `prune_error_output` set, run stays `success`
- `check_enabled=True`, `structural`: `restic check` with no extra flags
- `check_enabled=True`, `subset` at 10%: `restic check --read-data-subset=10%`
- `check_enabled=True`, `full`: `restic check --read-data`
- Check timeout: `check_status=failed`, timeout message, run `status` stays `success`
- Job `check_timeout_hours` overrides `default_job_timeout_hours` for check; independent of `timeout_hours`
- Check failure (non-timeout): `check_status=failed`, run `status` unchanged
- All four ntfy notifications fire in correct order on happy path; each toggle independently disables its notification
- `restic_password` passed via env; never in any DB output field

### `test_scheduler.py`

- Stale `status=running` rows set to `failed` + `reason=container_restart` before scheduler starts
- All `enabled=True` jobs registered on startup; `enabled=False` not registered
- CronTrigger and IntervalTrigger fire correctly; invalid interval rejected at job creation
- `DELETE /jobs/{id}` removes entry from `_job_locks`
- Job survives simulated container restart (re-loaded from DB)

### `test_restic.py`

- CLI built correctly from a fully-populated job config; null fields excluded
- `exclude_patterns` → one `--exclude` per pattern; `tags` → one `--tag` per tag
- Retention flags passed/omitted correctly
- JSON summary parsed correctly from realistic restic stdout
- Non-zero exit code raises expected exception
- `POST /jobs/{id}/unlock` calls `restic unlock` with correct repo path and `restic_password` from DB; returns subprocess output

### `test_mounts.py`

- `/mounts/sources` and `/mounts/destinations` return direct subdirectories only — files excluded
- `/mounts/sources/{label}/subdirs` — direct subdirs only (not recursive); empty list when none; `404` when label doesn't exist
- Empty `/sources/` returns empty list

### `test_settings.py`

- `GET/PUT /settings` round-trips all fields including `default_job_timeout_hours`
- `POST /settings/test-ntfy` sends correct payload
- `GET /settings/restic-update-check` returns correct `update_available` flag (GitHub API mocked for both cases)
- `AppSettings.restic_version` populated on startup; updated if changed
- `GET /health` returns scheduler state, restic version, DB status

---

**Frontend stack**: Vitest + React Testing Library + MSW (all API calls intercepted)

### `JobForm`

- Required fields show validation errors on empty submit; optional fields accept empty values
- Every optional field renders **(optional)** label and non-empty tooltip
- `409` → blocking error banner with conflicting job link; not resubmittable without identity field change
- Correct API payload on submit (null optionals omitted)
- Edit mode pre-populates all fields; `destination_label` read-only; `restic_password` read-only after first run, editable before
- `source_label` change shows amber warning banner
- Empty `restic_password` on submit does not change stored password
- Password field masked in create and edit modes

### `ScheduleInput`

- Switching type clears the opposing value; invalid cron shows error; valid cron shows next-run preview
- `6h`, `1d`, `30m` accepted; arbitrary strings rejected

### `RunStatusBadge`

- Correct colour and label for all four run statuses and all three check statuses

### `Dashboard`

- Total/enabled counts, last 10 runs, restic version, next-run times, disk space callout

### `Settings`

- All ntfy toggles render and toggle; test notification calls correct endpoint
- Update check shows correct message; token field masked; `default_job_timeout_hours` saves correctly

### `RunDetail`

- Success: metadata card, backup log panel shown, no error panel; prune failure shows amber panel
- Failed: error panel; lock error → unlock callout; `reason=container_restart` → info card, no error panel
- Skipped: reason badge, no stats or log
- Check passed/failed badges render correctly; failed check shows `check_error_output` panel

### `SnapshotList`

- Renders rows from DB response; shows empty state; does not call restic endpoints

### `JobDetail` — Unlock button

- Always visible; click → confirmation dialog → `POST /jobs/{id}/unlock` → toast with result

---

## 16. Logging Strategy

Every function and API handler must be fully traceable from logs alone — function name, inputs, and outputs at every step, with timestamps already included by the formatter.

### Central logging module: `app/core/logging.py`

All logging infrastructure lives here. No other module calls `logging.basicConfig`, sets handlers, or defines redaction rules. This is the single place to change format, level, handlers, or switch to structured/JSON logging.

```
app/core/logging.py
├── setup_logging()          # called once at startup from main.py lifespan
├── get_logger(name)         # used by every module instead of logging.getLogger directly
├── log_call (decorator)     # auto-logs function name, sanitized inputs, return value, exceptions
├── RequestLoggingMiddleware # FastAPI middleware: logs every request + response
└── SENSITIVE_FIELDS         # {"restic_password", "ntfy_token"} — redaction lives here
```

**`setup_logging()`** — called once in the `lifespan` handler in `main.py`:

- Reads `LOG_LEVEL` from env (default: `INFO`)
- Sets format: `%(asctime)s %(levelname)s [%(name)s] %(message)s`
- Configures handlers (console; optionally rotating file via `LOG_FILE` env var)

**`get_logger(name)`** — thin wrapper around `logging.getLogger(name)`. Every module uses this:

```python
from app.core.logging import get_logger
logger = get_logger(__name__)
```

Routing through a factory means the underlying implementation (plain, JSON, structured) can be swapped without touching any caller.

**`@log_call` decorator** — wraps async and sync functions. Logs entry with sanitized args, logs exit with return value, logs exceptions with full stack trace:

```python
from app.core.logging import log_call

@log_call
async def restic_backup(job_id, source_path, env):
    ...
```

Produces:

```
2026-05-10 14:03:01 INFO [app.services.restic] restic_backup | input: job_id=abc source_path=/sources/documents
2026-05-10 14:11:45 INFO [app.services.restic] restic_backup | output: exit_code=0 snapshot_id=a1b2c3d4
```

The decorator inspects the function signature and redacts any argument whose name appears in `SENSITIVE_FIELDS` before logging. Return values are logged as their string representation (truncated at 500 chars to avoid flooding logs with large stdout blobs).

**`RequestLoggingMiddleware`** — Starlette middleware added in `main.py`. Logs every API request and response without touching individual route handlers:

```
2026-05-10 14:03:00 INFO [app.core.logging] → POST /api/jobs body={"name":"docs","source_label":"documents",...}
2026-05-10 14:03:00 INFO [app.core.logging] ← POST /api/jobs status=201 duration_ms=12
```

Request bodies are sanitized through `SENSITIVE_FIELDS` before logging.

### Usage in every module

```python
from app.core.logging import get_logger, log_call

logger = get_logger(__name__)

@log_call
async def run_backup(job_id, triggered_by):
    ...
```

For backup run steps that need structured context (`job_id`, `run_id`) on every line, use a `LoggerAdapter`:

```python
log = logging.LoggerAdapter(logger, {"job_id": job_id, "run_id": run_id})
log.info("step=init_check")
log.info("step=backup source=%s", source_path)
log.info("step=done status=%s duration_seconds=%d", status, duration)
```

This produces a grep-able stream for any single run: `grep "run_id=xyz" app.log`.

### Security — fields that must never appear in logs

Redaction is enforced centrally in `SENSITIVE_FIELDS` inside `app/core/logging.py`. Callers never need to remember:

| Field                     | Logged as                                                        |
| ------------------------- | ---------------------------------------------------------------- |
| `restic_password`         | `[REDACTED]`                                                     |
| `ntfy_token`              | `[REDACTED]`                                                     |
| `RESTIC_PASSWORD` env var | Subprocess env is never logged; only the command args are logged |

When logging Pydantic model dumps anywhere outside a `@log_call`-decorated function, pass through the central `sanitize(data)` helper exported from `app/core/logging.py`.

---

## 17. Open Items / Future Considerations

1. **DB backup**: SQLite at `/app/data/backup.db` contains all config and run history. Consider a job that backs this file up to the destination drive so configuration is recoverable after hardware failure.

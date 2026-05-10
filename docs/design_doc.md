# Design Document: Backup Server

**Author**: yash91sharma@gmail.com

---

## 1. Overview

A self-hosted backup orchestration service for the home lab. Wraps [restic](https://restic.net/) with a web UI for scheduling, monitoring, and notification. Both FE and BE run as a single container deployed via docker-compose behind a reverse proxy.

---

## 2. Architecture

```
┌────────────────────────────────────────────────────┐
│                   Docker Container                 │
│  ┌─────────────────────────────────────────────┐   │
│  │  FastAPI (port 12345)                       │   │
│  │  ├── /api/*    REST API                     │   │
│  │  │    ├── manages → APScheduler             │   │
│  │  │    └── triggers → BackupRunner (manual)  │   │
│  │  ├── /static/*      built React app         │   │
│  │  └── /              → static/index.html     │   │
│  │                                             │   │
│  │  APScheduler (in-process)                   │   │
│  │  └── scheduled triggers → BackupRunner      │   │
│  │                                             │   │
│  │  BackupRunner                               │   │
│  │  ├── restic subprocess                      │   │
│  │  ├── BackupRun DB record                    │   │
│  │  ├── Snapshot DB records                    │   │
│  │  └── ntfy notification                      │   │
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

## 3. Engineering Design

### 3.1 Backend: Python 3.12 + FastAPI

- Python: chosen for subprocess-heavy orchestration — `asyncio.subprocess` keeps restic calls non-blocking, and APScheduler integrates natively with FastAPI's async event loop.
- FastAPI gives native async, auto-generated OpenAPI docs at `/api/docs`, and Pydantic validation as the single source of truth for request/response shapes.
- The `FastAPI` constructor is invoked with `docs_url='/api/docs'` and `openapi_url='/api/openapi.json'` so docs sit under the `/api/*` prefix and never collide with the SPA catch-all (§3.5).
- CORS is configured via `CORSMiddleware` with `allow_origins=['http://localhost:5173']` (the Vite dev server), `allow_methods=['GET', 'POST', 'PUT', 'DELETE']`, and `allow_headers=['Content-Type']` — production traffic is same-origin via the served bundle and needs no CORS entries.

### 3.2 Scheduler: APScheduler 3.x

Uses `AsyncIOScheduler` with a `MemoryJobStore` — not `SQLAlchemyJobStore`.

- `AsyncIOScheduler` is mandatory: `backup_runner.py` uses `asyncio.Lock` and `asyncio.subprocess`, both of which must execute inside the asyncio event loop.
- `BackgroundScheduler` (thread-based) must not be used — scheduler callbacks would be invoked from a thread, causing cryptic errors when they attempt to acquire an `asyncio.Lock`.
- `BackupJob` is the single durable source of truth for every scheduled job.
- APScheduler's persistent store would duplicate that state and create a second source that can drift after migrations or failed writes. With `MemoryJobStore`, the scheduler is rebuilt deterministically from `BackupJob` rows on every startup, and the same startup path is exercised every restart. The only trade-off: an `IntervalTrigger` fires relative to container-start time after a restart rather than exactly N hours after the last run — acceptable for a home-lab scheduler.

Supports `CronTrigger` (cron expressions) and `IntervalTrigger` (every N hours/days). The scheduler is constructed with `job_defaults={'misfire_grace_time': 3600, 'coalesce': True}` and `timezone=os.environ.get('TZ', 'UTC')`. Setting these as constructor-level defaults rather than per-`add_job` kwargs ensures they apply to every job uniformly — without them, APScheduler's defaults silently drop misfires after 1 second and queue every missed firing separately, both wrong for this workload. `timezone` makes cron expressions evaluate in the container's configured timezone (set via `TZ` in compose); falling back to UTC is explicit rather than implicit. Celery + Redis was ruled out as too heavy for this scope.

### 3.3 Database: SQLite + SQLAlchemy + Alembic

SQLite in WAL mode: zero infra, no sidecar, consistent with the home lab pattern. At expected scale (<50 jobs, <50,000 run records) it handles concurrent reads comfortably. Alembic handles schema migrations so the container can be updated without manual DB changes.

The SQLAlchemy engine is created with `create_async_engine('sqlite+aiosqlite:///...')` and `async_sessionmaker`. All DB operations in route handlers and `backup_runner.py` use `async with AsyncSession(engine) as session` — no `run_in_executor` wrapping needed. `aiosqlite` is the only async driver required; it is listed in `requirements.txt`. Alembic migrations use a sync `create_engine` (the standard Alembic pattern) only at migration time inside the ENTRYPOINT — not during normal app operation.

### 3.4 Frontend: React 18 + Vite + TypeScript + shadcn/ui

shadcn/ui gives a professional, minimal admin-panel look. TanStack Query handles server state, polling, and cache invalidation. Vite produces faster builds and smaller bundles than CRA. Built static files are copied into the Python container in the multi-stage build — no Node in production. **Vite is configured with `base: '/static/'`** so emitted asset URLs (e.g. `/static/assets/index-*.js`) match the FastAPI `StaticFiles` mount path (§3.5). A mismatch — Vite emitting `/assets/...` while FastAPI mounts at `/static` — would cause every asset request to be intercepted by the SPA catch-all and the page would never load.

### 3.5 Single Container

Frontend static files are served by FastAPI (`StaticFiles`). One Dockerfile, one image, one port, one container. No nginx sidecar.

A catch-all route in `main.py` handles React Router deep links: `GET /{full_path:path}` returns `FileResponse("static/index.html")` for any path not matched by `/api/*` or `/static/*`. Without this, direct navigation to any React Router URL (browser refresh, shared link, restored session) returns a 404 from FastAPI before the frontend loads. **The catch-all must be the last route registered in `main.py`** — after `app.include_router(api_router)` and after the `StaticFiles` mount. FastAPI uses first-match routing; registering it first would intercept all `/api/*` requests before they reach the API handlers.

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
│   │   │       ├── runs.py          # Run history + full log
│   │   │       ├── snapshots.py     # Snapshot rows per job (DB-only, no pagination)
│   │   │       ├── mounts.py        # Scan /sources/* and /destinations/*; rename destination label across jobs
│   │   │       └── settings.py      # ntfy config (singleton AppSettings row)
│   │   ├── core/
│   │   │   ├── config.py            # Pydantic BaseSettings (env vars)
│   │   │   └── scheduler.py         # APScheduler lifecycle, job registration
│   │   ├── db/
│   │   │   ├── database.py          # SQLAlchemy engine, session factory, WAL config
│   │   │   └── models.py            # ORM: BackupJob, BackupRun, Snapshot, AppSettings
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
│   │   │   ├── Dashboard.test.tsx
│   │   │   ├── Jobs.tsx
│   │   │   ├── Jobs.test.tsx
│   │   │   ├── JobDetail.tsx
│   │   │   ├── JobDetail.test.tsx
│   │   │   ├── RunDetail.tsx
│   │   │   ├── RunDetail.test.tsx
│   │   │   ├── Settings.tsx
│   │   │   └── Settings.test.tsx
│   │   ├── components/
│   │   │   ├── JobForm.tsx
│   │   │   ├── JobForm.test.tsx
│   │   │   ├── ScheduleInput.tsx
│   │   │   ├── ScheduleInput.test.tsx
│   │   │   ├── SnapshotList.tsx
│   │   │   ├── SnapshotList.test.tsx
│   │   │   ├── RunStatusBadge.tsx
│   │   │   └── RunStatusBadge.test.tsx
│   │   └── lib/
│   │       ├── api.ts               # Fetch client with base URL
│   │       └── types.ts
│   ├── package.json
│   ├── tsconfig.json
│   └── vite.config.ts               # base: '/static/' (matches StaticFiles mount); proxy /api → backend in dev
├── docs/
│   └── design_doc.md
├── Dockerfile
└── .dockerignore
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
| `enabled`                    | Boolean                                           | Controls APScheduler registration. Default: `true` — a newly created job is immediately scheduled                                                                                                                                                                                                                                                                                                                                                                                                                                    |
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

> **Validation constraints (enforced at the API layer; violations return `422` with a single human-readable `detail`):**
>
> - `name`: 1–128 chars after trim
> - `source_label`, `destination_label`: must match `^[A-Za-z0-9._-]{1,64}$`. Cannot be `.` or `..` and cannot contain `..` as a substring. Path-traversal characters (`/`, `\`) and any other character outside the regex are rejected — these labels are concatenated into filesystem paths (`/sources/{label}`, `/destinations/{label}/{id}`), so unconstrained values are a directory-traversal vector
> - `source_subpath`: when non-null, must match `^[A-Za-z0-9._-]{1,255}$`; cannot be `.` or `..`; no slashes (one level only). The UI picker enforces this, but the API also enforces it independently — never trust the client
> - `restic_password`: 1+ chars (no trim — leading/trailing whitespace is preserved verbatim)
> - `schedule_value` for `interval`: matches `^([1-9][0-9]*)(h|d|m)$`. Per-unit range: `h` — prefix in `[1, 8760]` (1 hour to 1 year); `d` — prefix in `[1, 8760]` (1 day to ~24 years); `m` — prefix in `[5, 1440]` (5 minutes to 1 day). The 5-minute floor for `m` is consistent with the cron minimum-gap check and guards against accidental short-interval run and notification storms
> - `schedule_value` for `cron`: a 5-field expression accepted by `CronTrigger.from_crontab`. Additionally rejected if its minimum gap between consecutive fire times is below 5 minutes (`detail: "cron expression fires too often (minimum gap is 5 minutes)"`) — guards against accidental `* * * * *` notification storms
> - `check_subset_percent`: 1–100
> - All `retain_keep_*` integers: 1–9999 when non-null
> - All `retain_keep_within_*` strings: match `^[1-9][0-9]*[hdmwy]$`
> - `pack_size`: 1–1024 when non-null (MiB; restic default is 128)
> - `read_concurrency`: 1–128 when non-null
> - `timeout_hours` (per-job): integer in `[1, 168]` when non-null — same range as `default_job_timeout_hours`
> - `check_timeout_hours` (per-job): integer in `[1, 168]` when non-null — same range as `default_job_timeout_hours`

---

### `BackupRun`

| Field                     | Type                                                     | Notes                                                                                                                                                                                                                                                                                                                                                                    |
| ------------------------- | -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `id`                      | UUID PK                                                  |                                                                                                                                                                                                                                                                                                                                                                          |
| `job_id`                  | UUID FK                                                  | Cascade delete                                                                                                                                                                                                                                                                                                                                                           |
| `status`                  | Enum: `running` / `success` / `failed` / `skipped`       | `skipped` = trigger fired but run not started. `failed` includes normal failures and the startup-recovery case                                                                                                                                                                                                                                                           |
| `reason`                  | Enum: `overlapping_run` / `container_restart` (nullable) | `overlapping_run` — trigger fired while another run was already active; `container_restart` — run was active when container stopped, marked failed by startup stale-run cleanup. Null for all other runs (normal success, normal failure, or timeout — `error_output` carries the cause)                                                                                 |
| `started_at`              | DateTime                                                 |                                                                                                                                                                                                                                                                                                                                                                          |
| `finished_at`             | DateTime (nullable)                                      | Null while running. Equal to `started_at` for skipped runs. Set to cleanup time for `reason=container_restart`                                                                                                                                                                                                                                                           |
| `duration_seconds`        | Int (nullable)                                           | Null for skipped and `reason=container_restart` runs (actual duration unknown)                                                                                                                                                                                                                                                                                           |
| `snapshot_id`             | String (nullable)                                        | Full 64-char restic snapshot ID (same value as `Snapshot.snapshot_id`). Null for failed/skipped runs. Never the 8-char short form                                                                                                                                                                                                                                        |
| `files_new`               | Int (nullable)                                           | From restic JSON summary. Null for skipped or pre-output-failed runs                                                                                                                                                                                                                                                                                                     |
| `files_changed`           | Int (nullable)                                           |                                                                                                                                                                                                                                                                                                                                                                          |
| `files_unmodified`        | Int (nullable)                                           |                                                                                                                                                                                                                                                                                                                                                                          |
| `dirs_new`                | Int (nullable)                                           |                                                                                                                                                                                                                                                                                                                                                                          |
| `dirs_changed`            | Int (nullable)                                           |                                                                                                                                                                                                                                                                                                                                                                          |
| `dirs_unmodified`         | Int (nullable)                                           |                                                                                                                                                                                                                                                                                                                                                                          |
| `data_added_bytes`        | BigInt (nullable)                                        | Post-compression bytes added. From restic `data_added`                                                                                                                                                                                                                                                                                                                   |
| `data_added_packed_bytes` | BigInt (nullable)                                        | Wire-size bytes added to pack files. From restic `data_added_packed` (restic ≥ 0.16). Null on older versions                                                                                                                                                                                                                                                             |
| `total_bytes_processed`   | BigInt (nullable)                                        | Total source bytes scanned                                                                                                                                                                                                                                                                                                                                               |
| `backup_output`           | Text (nullable)                                          | Full stdout from `restic backup --verbose --json` on success — file-level change stream + final JSON summary. **Capped at 1 MB via a ring buffer that drops the oldest lines; if lines were dropped, a leading note is prepended: `[output truncated at 1 MB — earliest lines dropped]`.** Null on failed/skipped                                                        |
| `error_output`            | Text (nullable)                                          | Captured stdout + stderr on failure. Assembled by appending stderr to stdout (stderr holds the actionable error line; placing it last ensures the 1 MB tail preserves it). **Capped at 1 MB; the combined string is truncated to preserve the tail, and `[output truncated at 1 MB]` is appended if exceeded.** Null on success, skipped, and `reason=container_restart` |
| `prune_status`            | Enum: `passed` / `failed` / `skipped` (nullable)         | Null only while running. `skipped` when: the run was skipped (overlapping), backup failed before reaching Step 8 (Steps 2–5 early exit or timeout), or `reason=container_restart` (startup stale-run cleanup)                                                                                                                                                            |
| `prune_error_output`      | Text (nullable)                                          | Populated when `prune_status=failed`. **Capped at 1 MB with `[output truncated at 1 MB]` suffix if exceeded**                                                                                                                                                                                                                                                            |
| `check_status`            | Enum: `passed` / `failed` / `skipped` (nullable)         | `skipped` when: `check_enabled=false` (Step 10), backup did not succeed (`status=failed` — Steps 8–12 not reached), run was skipped (overlapping), or `reason=container_restart` (startup stale-run cleanup, both passes). Null only while the run is still active — always set on completion                                                                            |
| `check_error_output`      | Text (nullable)                                          | Populated when `check_status=failed`. **Capped at 1 MB with `[output truncated at 1 MB]` suffix if exceeded**                                                                                                                                                                                                                                                            |
| `triggered_by`            | Enum: `scheduler` / `manual`                             |                                                                                                                                                                                                                                                                                                                                                                          |

---

### `Snapshot`

One row per restic snapshot, written after every successful backup. The Snapshots UI reads entirely from this table — no live `restic snapshots` calls on page load.

| Field           | Type                            | Notes                                                                                                                                                                                                                                                                                                                        |
| --------------- | ------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `id`            | UUID PK                         |                                                                                                                                                                                                                                                                                                                              |
| `job_id`        | UUID FK                         | Cascade delete                                                                                                                                                                                                                                                                                                               |
| `run_id`        | UUID FK (nullable)              | The run that created this snapshot. Null for snapshots that predate this app or were created outside of it                                                                                                                                                                                                                   |
| `snapshot_id`   | String                          | Full 64-char restic snapshot ID from `restic snapshots --json` (`.id` field). The 8-char short ID shown in restic output is not stored here                                                                                                                                                                                  |
| `snapshot_time` | DateTime                        | Timestamp from restic                                                                                                                                                                                                                                                                                                        |
| `hostname`      | String                          |                                                                                                                                                                                                                                                                                                                              |
| `paths`         | JSON array of String            | e.g. `["/sources/documents"]`                                                                                                                                                                                                                                                                                                |
| `tags`          | JSON array of String (nullable) |                                                                                                                                                                                                                                                                                                                              |
| `size_bytes`    | BigInt (nullable)               | Set to `total_bytes_processed` (total source bytes scanned) from the Step 7 backup summary — represents the snapshot's full content size. Set on the new snapshot row during Step 9 reconciliation. Null for snapshots that predate the app or were created outside it. `restic snapshots --json` does not include size data |
| `captured_at`   | DateTime                        | Set to the `finished_at` timestamp computed at the start of Step 9 (the same value written to `BackupRun.finished_at` in Step 10). For pre-existing snapshots (`run_id=null`, imported during reconciliation), set to `snapshot_time`                                                                                        |

Snapshot rows are never deleted directly by the app. After `restic forget --prune`, the next successful backup's snapshot-reconciliation step (`restic snapshots --json`) reconciles the table: deletes rows for pruned snapshot IDs, upserts rows for current ones. Upsert conflict key is `(job_id, snapshot_id)` — the `Snapshot` table carries a `UNIQUE(job_id, snapshot_id)` constraint. This is the only mechanism that writes to the `Snapshot` table.

---

### `AppSettings` (singleton, row id=1)

| Field                       | Type              | Notes                                                                                                                                                                                                                                           |
| --------------------------- | ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `id`                        | Int PK            | Always `1` — singleton row                                                                                                                                                                                                                      |
| `ntfy_server_url`           | String            | Default: `https://ntfy.sh`                                                                                                                                                                                                                      |
| `ntfy_topic`                | String            | Default: empty string. All ntfy notifications are skipped silently when `ntfy_topic` is empty                                                                                                                                                   |
| `ntfy_token`                | String (nullable) | For private topics                                                                                                                                                                                                                              |
| `notify_on_start`           | Boolean           | Default: true. Controls Step 3 ("backup started") notification                                                                                                                                                                                  |
| `notify_on_success`         | Boolean           | Default: true. Controls Step 11 notification when `status=success`                                                                                                                                                                              |
| `notify_on_failure`         | Boolean           | Default: true. Controls Step 11 notification when `status=failed`                                                                                                                                                                               |
| `notify_on_verification`    | Boolean           | Default: true. Controls both Step 12 notifications ("verification started" and "verification complete")                                                                                                                                         |
| `restic_version`            | String (nullable) | Detected at startup via `restic version`; updated in DB if changed. Null on first boot until startup detection succeeds, or if `restic` is not found in `PATH`                                                                                  |
| `default_job_timeout_hours` | Int               | Default: 24. Applied to both `restic backup` and `restic check` when a job's own timeout field is null. Rationale: a first-time full backup of 2–3 TB over 1 Gbps takes ~8–11 h; 24 h gives headroom for both backup and full-mode verification |

> **Validation constraints (enforced at the API layer; violations return `422` with a single human-readable `detail`):**
>
> - `ntfy_server_url`: max 512 chars; must start with `http://` or `https://`. Other schemes (`file://`, `javascript:`, etc.) are rejected — the URL is fed to an HTTP client at notification time
> - `ntfy_topic`: empty string OR 1–64 chars matching `^[A-Za-z0-9_-]{1,64}$` (the alphabet ntfy itself accepts)
> - `ntfy_token`: when non-null, 1–512 chars
> - `default_job_timeout_hours`: integer in `[1, 168]` (1 hour to 1 week)

---

## 6. API Design

**Base path**: `/api`

**Duplicate job validation**: `POST /jobs` and `PUT /jobs/{id}` check for an existing job with the same `source_label` + `source_subpath` + `destination_label`. Conflict returns `409` with the conflicting job's name and ID. No client-side pre-check — the server is the sole enforcement point.

**404 behavior**: All `/{id}` endpoints return `404 { "detail": "Not found" }` when the referenced job or run does not exist.

**Error response format**: All `4xx` responses return `{ "detail": "<human-readable string>" }`. Pydantic validation errors are caught and rewritten to a single readable sentence before returning — the raw Pydantic error list is never sent to the client. The frontend reads `response.detail` and displays it directly without further parsing. Key validation messages:

- `"check_mode is required when check_enabled is true"`
- `"check_subset_percent is required when check_mode is 'subset'"`
- `"destination_label cannot be changed after creation"`
- `"restic_password cannot be changed after the first successful run"`
- `"label may only contain letters, digits, dots, underscores, and dashes"` (source/destination labels)
- `"source subpath must be a single directory name with no slashes"`
- `"interval must be in the form Nh, Nd, or Nm — for h: 1–8760; for d: 1–8760; for m: 5–1440"`
- `"cron expression fires too often (minimum gap is 5 minutes)"`
- `"source mount /sources/<label> is not present in the container — check docker-compose.yaml"` (on `POST /jobs` always; on `PUT /jobs/{id}` only when `source_label` is being changed)
- `"destination mount /destinations/<label> is not present in the container — check docker-compose.yaml"` (on `POST /jobs` only)
- `"A run is currently in progress for this job"` (used by `DELETE /jobs/{id}` and `POST /jobs/{id}/unlock`)
- `"A run is currently in progress for a job using this destination. Wait for it to finish before renaming."` (used by `POST /mounts/destinations/rename`)

| Method | Path                              | Description                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ------ | --------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/jobs`                           | List all jobs. Each row includes all `BackupJob` fields — **except `restic_password`, which is excluded from all API responses and returned as `null`** — plus `next_run_time` (ISO timestamp from APScheduler; `null` when disabled), `has_successful_run: bool` (`true` if any `BackupRun` row for this job has `status=success` — used by the frontend to determine whether `restic_password` is editable), and `last_run: { id, status, check_status, started_at, finished_at, duration_seconds, triggered_by } \| null` (most recent `BackupRun` row regardless of status; `null` if no runs yet). `check_status` is included so the Jobs list and Dashboard can apply the two-condition polling stop (§14). `backup_output` and `error_output` are excluded — those are returned only by `GET /runs/{id}`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| POST   | `/jobs`                           | Create job. Requires `restic_password`. Returns `201` with the created `BackupJob` object (`restic_password` excluded — returned as `null`). Returns `409` on duplicate source+destination. Returns `422` if `check_enabled=true` and `check_mode=null`, `check_mode=subset` and `check_subset_percent` is null, or the supplied `source_label` / `destination_label` is not currently present under `/sources/` / `/destinations/` respectively. Field-level validation rules (label/subpath regex, interval format, cron min-gap, retention ranges) are listed in §5 — violations return `422` with the corresponding message above                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| GET    | `/jobs/{id}`                      | Returns full `BackupJob` object (all fields, with `restic_password` excluded — returned as `null`) plus `next_run_time` and `last_run` — same shape as each row in `GET /jobs`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| PUT    | `/jobs/{id}`                      | Update job. Returns `200` with the updated `BackupJob` object (`restic_password` excluded — returned as `null`). Returns `422` if `destination_label` is changed (always immutable), `restic_password` is changed after the first successful run, `check_enabled=true` and `check_mode=null`, `check_mode=subset` and `check_subset_percent` is null, or the supplied `source_label` is different from the stored value and is not currently present under `/sources/` (mount check runs only when `source_label` is changing — prevents redirecting a job to an unmounted source; unrelated edits such as schedule or retention changes are never blocked by a temporarily offline drive). Field-level validation rules are listed in §5. **Password contract: if `restic_password` is absent or empty string in the request body, the stored password is left unchanged. Only a present, non-empty, differing value triggers the immutability check.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| DELETE | `/jobs/{id}`                      | Delete job and remove from scheduler. Returns `204 No Content` on success. Returns `409 { "detail": "Cannot delete a job while a run is in progress. Wait for the active run to finish, then retry." }` if `job.id ∈ _active_jobs` (§7) — without this guard, cascade-delete would remove the `BackupRun` row that the runner is actively writing to, producing silent FK errors and an orphaned restic subprocess. **Does not delete the restic repository from disk** — the data at `/destinations/{label}/{id}` is left intact and must be removed manually to reclaim disk space                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| POST   | `/jobs/{id}/run`                  | Trigger immediate manual run. **The API synchronously creates the `BackupRun` row before returning** — under the per-job lock it consults `_active_jobs` (§7); if free it adds the job UUID, writes a `status=running` row (`triggered_by=manual`, `started_at=now`), releases the lock, and only then schedules the runner task to enter at Step 2. Scheduling the runner task is wrapped in a `try/except`: on any exception, `_active_jobs.discard(job.id)` is called before re-raising — the `status=running` row is left intact for the next startup's stale-run cleanup (§7 Step 3). If the job is already in `_active_jobs` it instead writes a `status=skipped`, `reason=overlapping_run` row (with `finished_at=now`, `prune_status=skipped`, `check_status=skipped`) and does not schedule any task. Either way, returns `{ "run_id": "<UUID>" }` pointing at a row that already exists in the DB — the frontend can navigate to Run Detail and start polling without a 404 race. Succeeds regardless of whether the job is enabled — the `enabled` flag controls scheduler registration only, not manual triggers                                                                                                                                                                                                                                                                                                     |
| POST   | `/jobs/{id}/enable`               | Enable and register in scheduler. Returns `200 { "id": "<UUID>", "enabled": true }`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| POST   | `/jobs/{id}/disable`              | Disable and deregister. Returns `200 { "id": "<UUID>", "enabled": false }`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| POST   | `/jobs/{id}/unlock`               | Run `restic unlock` using `BackupJob.restic_password` from DB. Returns `200 { "output": "<full stdout+stderr from restic unlock>" }` on success. Returns `409 { "detail": "A run is currently in progress for this job; the repository will be unlocked automatically when it finishes." }` if `job.id ∈ _active_jobs` (§7) — without this guard, `restic unlock` during the integrity-check phase (§8 Step 12) would kill the app's own running restic process                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| GET    | `/jobs/{id}/runs`                 | All run history for this job ordered by `started_at` desc — all rows returned, no pagination. `backup_output`, `error_output`, `prune_error_output`, and `check_error_output` are excluded — those are returned only by `GET /runs/{id}`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| GET    | `/jobs/{id}/snapshots`            | All `Snapshot` rows for this job ordered by `snapshot_time` desc — all rows returned, no pagination. DB-only, no restic call                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| GET    | `/runs/recent`                    | Most recent N `BackupRun` rows across all jobs, ordered by `started_at` desc. Query param: `limit` (default 10, min 1, max 100); values below 1 or above 100 return `422`. Each row includes all `BackupRun` fields (including `check_status`) plus `job_id` and `job_name`. `backup_output`, `error_output`, `prune_error_output`, and `check_error_output` are excluded — those are returned only by `GET /runs/{id}`. Used by the Dashboard                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| GET    | `/runs/{id}`                      | Full run detail. Returns all `BackupRun` fields including `backup_output`, `error_output`, `prune_error_output`, and `check_error_output` (the four output fields excluded from all list endpoints). Returns `404 { "detail": "Not found" }` when the run does not exist                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| GET    | `/mounts/sources`                 | Top-level mount labels under `/sources/`. **Directories only** — regular files, sockets, FIFOs, and broken symlinks are filtered out; symlinks are followed and included only when they resolve to a directory inside the mount                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| GET    | `/mounts/sources/{label}/subdirs` | Direct subdirectories of `/sources/{label}` — one level only. Same directory filter as `/mounts/sources`. Returns `404` if `/sources/{label}` does not exist                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| GET    | `/mounts/destinations`            | Top-level mount labels under `/destinations/`. Same directory filter as `/mounts/sources`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| POST   | `/mounts/destinations/rename`     | Rename a destination label across all referencing jobs atomically. Body: `{ old_label, new_label }`. Both `old_label` and `new_label` must satisfy the label regex `^[A-Za-z0-9._-]{1,64}$` (same rules as §5). Validates `/destinations/new_label` is currently mounted before writing. **Does not require `old_label` to be currently mounted** — the entire purpose of this endpoint is to update job rows after a drive has been remounted under a new label, at which point the old mount is gone. Before writing, checks `_active_jobs` (§7): if any `BackupJob` with `destination_label == old_label` has an active run, returns `409 { "detail": "A run is currently in progress for a job using this destination. Wait for it to finish before renaming." }` — without this guard, the DB update would take effect immediately while the runner still has `RESTIC_REPOSITORY` set to the old path; on the next run the new path would be absent, triggering `restic init` and wiping all snapshot history. Updates `destination_label` on all `BackupJob` rows with `old_label` in one transaction. Returns `{ affected_jobs: [{ id, name }] }`. Returns `404` only if no `BackupJob` rows reference `old_label`. Returns `409` if any such job has an active run (see above). Returns `422` if `old_label` or `new_label` fails the label regex, `old_label == new_label`, or `/destinations/new_label` is not mounted |
| GET    | `/settings`                       | ntfy config + global defaults                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| PUT    | `/settings`                       | Update settings. Returns `200` with the full updated `AppSettings` object                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| POST   | `/settings/test-ntfy`             | Send test ntfy notification. Returns `200 { "ok": true }` on success; `200 { "ok": false, "error": "<reason>" }` if ntfy returns a non-2xx response. Returns `422` if `ntfy_topic` is empty                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| GET    | `/health`                         | Returns `200 { "scheduler_running": bool, "restic_version": string \| null, "db_ok": bool }`. `db_ok` is the result of `SELECT 1` against the configured engine wrapped in a 1-second timeout — `false` on timeout or any exception. The endpoint always returns `200` even when the scheduler did not start (degraded mode, §7) so an operator can read it via Traefik or `docker exec` to diagnose startup failures without a crash loop                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| GET    | `/settings/restic-update-check`   | Compare `AppSettings.restic_version` against GitHub Releases API. The GitHub API call uses a 10-second timeout. Returns `{ current, latest, update_available }`. If `restic_version` is null (restic not found at startup), returns `{ current: null, latest: "<latest from GitHub>", update_available: null }`. If the GitHub API call times out or fails, returns `{ current: "<current>", latest: null, update_available: null }` with the error logged                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |

---

## 7. Scheduler Design

APScheduler runs as a singleton `AsyncIOScheduler` initialized on FastAPI startup via the `lifespan` context manager. Uses `MemoryJobStore` — `BackupJob` is the sole durable state; nothing can drift. `AsyncIOScheduler` is mandatory (not `BackgroundScheduler`) because job callbacks are coroutines that use `asyncio.Lock` and `asyncio.subprocess`.

**Startup sequence (order matters):**

**Each step creates its own `async with AsyncSession(engine) as session` context and is wrapped in its own `try/except` that catches and logs any exception with full traceback; a failure in any step is logged but does not abort startup.** Using per-step sessions means a DB exception in one step never corrupts the session state for the next — sharing a single session would require an explicit `await session.rollback()` in each `except` block to avoid `InvalidRequestError` on the next operation. This is deliberate: with `restart: unless-stopped` (§12) a hard-aborting startup produces an opaque crash loop where the operator cannot reach the UI to diagnose the problem. Degraded mode keeps the API and `/health` reachable so the failure is visible.

1. **Seed AppSettings**: `INSERT OR IGNORE INTO app_settings (id, ntfy_server_url, ntfy_topic, ntfy_token, notify_on_start, notify_on_success, notify_on_failure, notify_on_verification, restic_version, default_job_timeout_hours) VALUES (1, 'https://ntfy.sh', '', NULL, TRUE, TRUE, TRUE, TRUE, NULL, 24)` — ensures the singleton row exists on first boot. No-op on subsequent starts. On failure, log and continue — first read of `AppSettings` will surface a clearer error to the operator than a crash loop.
2. **Detect restic version**: run `restic version` wrapped in `asyncio.wait_for(timeout=5)`, parse the output, update `AppSettings.restic_version` if changed. If the call times out, fails, or the binary is not in `PATH`, `restic_version` stays null and `/health.restic_version` reflects this.
3. **Stale run cleanup** (before scheduler starts): two passes, both in the same transaction:
   - Find all `BackupRun` rows with `status=running` → set `status=failed`, `reason=container_restart`, `finished_at=now`, `duration_seconds=null`, `prune_status=skipped`, `check_status=skipped`. Handles containers killed before Step 10.
   - Find all `BackupRun` rows with `status IN ('success', 'failed')` **and** `check_status IS NULL` → set `check_status=skipped`. Handles containers killed between Step 10 (writes `status=success`) and Step 12 (writes `check_status`) — without this pass, those rows would have a terminal `status` but a null `check_status` forever, causing the two-condition polling stop (§14) to never fire.

   Combined with `_active_jobs` being purely in-memory (and therefore empty on every restart), this guarantees no job is permanently blocked from running and no run row is stuck in an unresolvable polling state after a restart.

4. `scheduler.start()` → load all `BackupJob` rows where `enabled=True` → register as in-memory APScheduler jobs (triggers constructed per §7 "Trigger types"; `misfire_grace_time` and `coalesce` apply from the constructor-level `job_defaults` per §3.2). Failure here is logged and the API still serves; `/health.scheduler_running` is `false` and the Dashboard surfaces this to the user.

**Lifecycle:**
APScheduler job ID is `str(job.id)` in all operations — APScheduler 3.x requires string IDs; passing a UUID object produces a different key and causes silent mismatches on remove/reschedule.

- `POST /jobs` → create DB row → `scheduler.add_job(..., id=str(job.id))` if `enabled`
- `PUT /jobs/{id}` → update DB row → remove old scheduler entry with `try: scheduler.remove_job(str(job.id)) except JobLookupError: pass` (disabled jobs are never registered, so the remove must be guarded) → `scheduler.add_job(..., id=str(job.id))` only if the updated job has `enabled=True`
- `DELETE /jobs/{id}` → reject with `409` if `job.id ∈ _active_jobs` → otherwise `try: scheduler.remove_job(str(job.id)) except JobLookupError: pass` → `_job_locks.pop(job_id, None)` → delete DB rows (cascade to runs)
- `POST /jobs/{id}/enable` → `try: scheduler.remove_job(str(job.id)) except JobLookupError: pass` then `scheduler.add_job(..., id=str(job.id))` (remove-then-add makes enable idempotent without a `ConflictingIdError` on double-enable)
- `POST /jobs/{id}/disable` → `try: scheduler.remove_job(str(job.id)) except JobLookupError: pass` (guard prevents `JobLookupError` on double-disable)

**Trigger types:**

- `cron`: `CronTrigger.from_crontab(schedule_value)`
- `interval`: `"6h"` → `IntervalTrigger(hours=6)`, `"1d"` → `IntervalTrigger(days=1)`, `"30m"` → `IntervalTrigger(minutes=30)`

**Concurrent run guard:** `backup_runner.py` holds two module-level structures:

- `_job_locks: dict[UUID, asyncio.Lock]` — per-job lock used for the brief critical section that decides whether a new run starts or is skipped. Accessed via `_job_locks.setdefault(job_id, asyncio.Lock())` — the lock is created on first access and reused on all subsequent accesses for that job.
- `_active_jobs: set[UUID]` — every job UUID currently inside _any_ phase of a run (Steps 2–12 in §8, including the integrity check). Membership outlives `BackupRun.status` transitions: it is added when the running row is written and removed only after Step 12 completes (or any error path escapes the inner steps).

Before starting a run the runner acquires the per-job lock. Within the lock:

- If `job.id ∈ _active_jobs`: write `status=skipped`, `reason=overlapping_run`, `started_at=now`, `finished_at=now`, `prune_status=skipped`, `check_status=skipped` — release and return.
- Otherwise: `_active_jobs.add(job.id)`, write the `status=running` `BackupRun` row, release the lock.

Steps 2–12 run **outside** the lock; the runner's outer `finally` calls `_active_jobs.discard(job.id)` once Step 12 (or the relevant error path) completes. Holding `_active_jobs` membership across the entire run — including the integrity-check phase that follows Step 10's `status=success` write — is what keeps the next scheduler firing from launching a backup that would immediately collide with `restic check`'s exclusive repository lock. The DB `status=running` check is no longer used for live concurrency: stale-run cleanup at startup step 3 ensures no orphaned `running` rows survive a restart, and `_active_jobs` (in-memory) is reset to empty on every process start. `_active_jobs` is the single source of truth for live concurrency.

Manual runs share this guard: `POST /jobs/{id}/run` performs the per-job-lock + `_active_jobs` check + `BackupRun` row insert _synchronously inside the request handler_ (writing either a `running` row or, if the job is already active, a `skipped` row), then releases the lock. For the `running` case only, it then schedules the runner task (via `asyncio.create_task`) before returning `{ "run_id": "<UUID>" }`. Scheduling is wrapped in `try/except`: on any exception, `_active_jobs.discard(job.id)` is called before re-raising, and the orphaned `status=running` row is left for startup stale-run cleanup (Step 3 above) to handle on next boot. The runner enters at Step 2 for manual runs (Step 1 was done by the API) and at Step 1 for scheduler-fired runs. `_active_jobs.discard` always happens in the runner's outer `finally`, regardless of entry path.

`POST /jobs/{id}/unlock` and `DELETE /jobs/{id}` consult `_active_jobs` and return `409` when the job is active (see §6) — preventing self-inflicted lock-kills and FK-violation cascades respectively.

---

## 8. Restic Workflow per Backup Run

Each run in `backup_runner.py`:

**Step 1 — Concurrent run guard (acquire lock)**  
_Manual runs skip Step 1 entirely_ — `POST /jobs/{id}/run` performs the lock + `_active_jobs` check + row insert synchronously inside the request handler (§6, §7), and the runner is invoked with a pre-created run ID. The runner enters at Step 2 for manual runs.

For scheduler-fired runs, acquire `_job_locks.setdefault(job_id, asyncio.Lock())` (created on first access if not already present). Within the lock:

- If `job_id ∈ _active_jobs`: write `status=skipped`, `reason=overlapping_run`, `started_at=now`, `finished_at=now`, `prune_status=skipped`, `check_status=skipped` — release and return
- Otherwise: `_active_jobs.add(job_id)` then write the `status=running` row — then release the lock
- Lock released in `finally` block. Never held past this step.

`_active_jobs.discard(job_id)` is registered in an outer `finally` that wraps Steps 2–12, so the entry persists across the integrity-check phase even after Step 10 sets `status=success`. This is the difference between the two structures: `_job_locks` covers only the brief admit/reject critical section; `_active_jobs` covers the entire run.

**Step 2 — Validate password**  
If `restic_password` is null/empty: update the step-1 row to `status=failed`, `error_output="No restic password configured for this job."`, `finished_at=now`, `prune_status=skipped`, `check_status=skipped` — return. (Password validation is outside the lock.)

**Step 3 — Notify: backup started**  
Fire-and-forget ntfy with job name, source, destination, triggered-by. Skipped if `ntfy_topic` is empty or `notify_on_start=false`. The `ntfy_topic` empty check applies to all ntfy steps (3, 11, 12) — it is the blanket condition; the per-step `notify_on_*` flags are additional conditions checked only when `ntfy_topic` is non-empty.

**Step 4 — Init check**  
Run `restic cat config`. Parse the result:

- **Exit 0**: repo exists and password is correct — proceed.
- **Non-zero + stderr indicates repo absent** (contains `"no such file"`, `"does not exist"`, or `"is not a restic repository"`): run `restic init`. If init fails: set `status=failed`, `finished_at=now`, `error_output` from init stdout+stderr, `prune_status=skipped`, `check_status=skipped` — return.
- **Non-zero for any other reason** (wrong password, permission denied, locked, corrupted): set `status=failed`, `finished_at=now`, `error_output` from `cat config` stdout+stderr — do **not** attempt init, `prune_status=skipped`, `check_status=skipped` — return.

All commands use `RESTIC_PASSWORD` from the job record.

**Step 5 — Backup**  
`restic backup <source_path> --json --verbose [flags]` where `source_path` is `/sources/{source_label}` when `source_subpath` is null, or `/sources/{source_label}/{source_subpath}` when set — all non-null job fields produce CLI flags dynamically; null fields produce no flags. Wrapped in `asyncio.wait_for(timeout=(job.timeout_hours or settings.default_job_timeout_hours) * 3600)`. **stdout is streamed line-by-line into a bounded ring buffer (1 MB capacity, drops oldest lines when full); stderr is streamed into its own 1 MB ring buffer; the final JSON summary line is also held separately as it streams in, so Step 6 always sees it even if later verbose output pushed earlier lines (or the summary itself, on a noisy run) out of the main buffer.** Without bounded streaming, a `--verbose` run on a 3 TB source can buffer hundreds of MB of file-line output in memory before write-time truncation. On timeout: SIGTERM → wait 10 s → SIGKILL, `status=failed`, `finished_at=now`, `error_output="Backup subprocess timed out after N hours"`, `prune_status=skipped`, `check_status=skipped` — return. On non-zero exit (non-timeout): assemble `error_output` by appending the stderr ring buffer to the stdout ring buffer (stderr last, so the 1 MB tail cap preserves the actionable error line) — proceed to Step 10 failure finalization, skipping Steps 6–9. **Note on restic exit code 3 (incomplete):** restic exits `3` when some source files could not be read (permission errors, locked files, etc.) but a valid snapshot was still created. This design treats exit code 3 identically to exit code 1 (fatal): the run is marked `status=failed` and the snapshot is orphaned until the next successful run's Step 9 reconciliation picks it up. This is conservative by design — for home-lab sources (`~/Documents`, `~/Photos`) exit code 3 is rarely triggered; if a source directory reliably produces it (mixed-ownership files), use `--exclude` patterns to skip the unreadable paths.

**Step 6 — Parse output**  
Parse the final JSON summary line captured separately in Step 5. If the line is missing or fails to parse despite `restic backup` exiting `0` (very rare; observed in early restic versions or when the process is killed mid-write), the run still completes as `status=success` (the data is on disk — exit code 0), all stat fields stay null, and `\n[summary parse failed: <exception class>]` is appended to the buffered stdout for Step 7 to persist. Step 9 (snapshot reconciliation) will still pick up the new snapshot. Do **not** promote a parse failure to `status=failed`.

**Step 7 — Update run stats**  
Populate `files_new/changed/unmodified`, `dirs_new/changed/unmodified`, `data_added_bytes`, `data_added_packed_bytes`, `total_bytes_processed`, `snapshot_id` from the Step 6 summary (each field stays null on parse failure). Persist the Step 5 stdout ring-buffer contents into `backup_output` (success path only); the stderr ring buffer is discarded on the success path. If the stdout ring buffer dropped lines, prepend `[output truncated at 1 MB — earliest lines dropped]\n` so the user knows the buffer rolled. At the end of Step 7, record `backup_finished_at = datetime.now(timezone.utc)` — this timestamp is used to compute `duration_seconds` in Step 10 and covers the backup subprocess only (Steps 2–7), not prune. The separate `finished_at` timestamp (for `BackupRun.finished_at`) is recorded at the start of Step 9.

**Step 8 — Prune**

- If **any** retention field is non-null: run `restic forget --prune [retention flags]` — forgets snapshots per policy then removes orphaned pack files.
- If **all** retention fields are null: run `restic prune` — removes orphaned pack files only, forgets no snapshots. `restic forget --prune` without `--keep-*` flags exits with `Fatal: no retention policy was specified` and must not be used in this case. Note: without a prior `restic forget` there are typically no orphaned packs to remove; `restic prune` is still run as a safety measure to clean up any pack files left incomplete by a previously interrupted backup. On large repositories, `restic prune` must scan all pack file headers and may take several minutes even when nothing is pruned — this cost is paid after every successful backup when no retention policy is configured.

Both prune variants are wrapped in `asyncio.wait_for(timeout=(job.timeout_hours or settings.default_job_timeout_hours) * 3600)`. On timeout: SIGTERM → wait 10 s → SIGKILL, `prune_status=failed`, `prune_error_output="Prune timed out after N hours"`. On non-zero exit (non-timeout): `prune_status=failed`, `prune_error_output` assembled from stdout+stderr of the prune subprocess. Run `status` is unchanged in all failure cases — prune failure is non-fatal.

`prune_status=passed/failed`; failure is non-fatal (run status unchanged), output stored in `prune_error_output`. **Step 9 always executes regardless of Step 8 outcome.**

**Step 9 — Reconcile snapshots**  
Runs regardless of Step 8 outcome. If `restic snapshots --json` exits non-zero or throws an exception, log the error, skip all table mutations for this step, and proceed to Step 10. The snapshot created in this run remains absent from the `Snapshot` table until the next successful backup's Step 9 reconciles it. Otherwise: `restic snapshots --json` → delete `Snapshot` rows no longer returned (pruned in Step 8), upsert current ones using conflict key `(job_id, snapshot_id)`. The upsert is implemented as `INSERT … ON CONFLICT(job_id, snapshot_id) DO UPDATE SET hostname=excluded.hostname, paths=excluded.paths, tags=excluded.tags` — **`run_id`, `size_bytes`, and `captured_at` are explicitly excluded from the `DO UPDATE SET` clause** so the size and provenance recorded on a snapshot's first insert are never overwritten by later reconciliations (which lack size data — `restic snapshots --json` does not return it). The runner records `finished_at = datetime.now(timezone.utc)` once at the start of Step 9; this same timestamp is written to `BackupRun.finished_at` in Step 10. On the _insert_ path: for the newly-created snapshot from this run, set `run_id` to the current run, `size_bytes` to `total_bytes_processed` from the Step 7 summary, and `captured_at` to this `finished_at` timestamp; for any other newly-discovered rows (e.g. pre-existing snapshots imported on first reconciliation), leave `run_id` null, `size_bytes` null, and set `captured_at` to `snapshot_time`.

**Step 10 — Finalise run**  
Set `status=success/failed`, `finished_at`, `duration_seconds = int((backup_finished_at - started_at).total_seconds())` (where `backup_finished_at` was recorded at the end of Step 7 — covers Steps 2–7 only, not prune). On failure: `backup_finished_at` is set to `datetime.now(timezone.utc)` at failure time rather than at the end of Step 7; `duration_seconds` therefore covers the time from run start to the point of failure (not just Steps 2–7); store `error_output`, set `prune_status=skipped` and `check_status=skipped` (steps 8–9 and 12 not reached), leave `backup_output` null. On success with `check_enabled=false`: set `check_status=skipped` here (Step 12 will not run); on success with `check_enabled=true`: leave `check_status=null` so the Run Detail UI (§14) can distinguish "check pending" from "check skipped" while polling. On success, `duration_seconds` covers the backup subprocess only (Steps 2–7) — prune and the integrity check are separate phases. The Run Detail UI labels the field _"Backup duration"_ on success runs and _"Time to failure"_ on failed runs to reflect this difference.

**Step 11 — Notify: backup complete**  
Fire-and-forget ntfy with status, duration, files changed, data added. Skipped when status=success and `notify_on_success=false`, or when status=failed and `notify_on_failure=false`. On failure: includes the **last 500 chars of `error_output`** as the excerpt (the failing message is almost always at the end of the stream). The ntfy POST sends `Authorization: Bearer <ntfy_token>` when `ntfy_token` is set (ntfy 2.x convention) and no auth header otherwise. All ntfy POSTs use a 10-second connect+read timeout; failures are logged and discarded — they never affect the run's outcome.

**Step 12 — Integrity check** (only if `check_enabled=true` and `status=success`)

- Notify: verification started — skipped if `notify_on_verification=false`
- Run according to `check_mode`:
  - `structural`: `restic check` — index integrity only, no data reads
  - `subset`: `restic check --read-data-subset=N%`
  - `full`: `restic check --read-data`
- Wrapped in `asyncio.wait_for(timeout=(job.check_timeout_hours or settings.default_job_timeout_hours) * 3600)`
- On timeout: `check_status=failed`, `check_error_output="Verification subprocess timed out after N hours"`. **Run `status` stays `success` — check is non-fatal**
- On non-zero exit (non-timeout): `check_status=failed`, `check_error_output` assembled from stdout+stderr of the check subprocess. Run `status` stays `success` — check is non-fatal
- `check_status=passed` on exit 0
- Notify: verification complete (passed/failed) — skipped if `notify_on_verification=false`

`RESTIC_REPOSITORY=/destinations/{job.destination_label}/{job.id}` and `RESTIC_PASSWORD` (from `job.restic_password`) are passed as env vars to every restic subprocess — never written to logs, DB output fields, or filesystem. `RESTIC_CACHE_DIR` is inherited from the Docker `ENV` set in Stage 4 of the Dockerfile (§11) and does not need to be passed explicitly. Captured stdout/stderr is scanned for the literal `RESTIC_PASSWORD` value before being persisted to `backup_output`, `error_output`, `prune_error_output`, or `check_error_output`, and any match is replaced with `[REDACTED]` — restic does not normally echo the password, but this is cheap defense-in-depth against a future restic version that does.

**Outer `finally` (run lifecycle):** Steps 2–12 are wrapped in an outer `try`/`finally`. The `finally` block executes `_active_jobs.discard(job.id)` exactly once per run, regardless of outcome (success, failure, timeout, exception, cancellation). This is the single point at which the job becomes eligible for the next run, and it is what allows §6's `409`-on-active guards (`DELETE`, `unlock`, `run`) to be correct: as long as `_active_jobs` membership accurately tracks "any restic subprocess is still running for this job", those endpoints are safe to invoke at any other time.

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
> - **Destination labels are permanent at the job level** — they are part of the restic repo path and cannot be changed via `PUT /jobs/{id}`. If a drive is remounted under a new label in `docker-compose.yaml`, use `POST /mounts/destinations/rename` to update all referencing jobs atomically. The new label must already be mounted before calling this endpoint. Renaming the label in compose without calling this endpoint orphans all existing snapshots
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
RUN apk add --no-cache ca-certificates && mkdir -p /sources /destinations /app/data

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
ENTRYPOINT ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 12345"]
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
.env
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
    # No `ports:` block by default — all ingress goes through Traefik (§13), which
    # provides TLS and basicAuth. To expose the UI directly on the LAN for initial
    # setup or troubleshooting only, add:
    #
    #   ports:
    #     - "12345:12345"
    #
    # ⚠ SECURITY: that binding bypasses Traefik entirely — anyone on the LAN can
    # access the UI unauthenticated, including `GET /jobs/{id}` which exposes
    # plaintext restic passwords. Remove again before normal operation.
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

**Build command** (run from the repo root `/Users/yash/Dev/backup_server/`):

```bash
docker build \
  --build-arg RESTIC_VERSION=0.17.3 \
  --build-arg RESTIC_ARCH=arm64 \
  -t backup-server:latest .
```

Omitting `--build-arg RESTIC_ARCH` causes an immediate build failure with a clear error. `RESTIC_VERSION` defaults to `0.17.3` if omitted, but passing it explicitly is recommended to avoid accidentally building with a stale default.

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

Stats: total jobs, enabled count, last 10 runs with status badges (each row also shows its `check_status` badge so verification failures are visible at a glance — §8 keeps `status=success` even when the integrity check fails), next scheduled run time per job (`null` for disabled jobs — rendered as _"—"_), restic version (from DB). All data from DB — no restic calls. Persistent callout: _"Disk space is not monitored automatically — ensure destinations have sufficient free space before scheduled runs."_ When `GET /health` returns `scheduler_running=false` (degraded mode, §7), a red banner displays at the top: _"Scheduler is not running — backups will not fire automatically. Check container logs."_ While any job has an in-progress run, the Dashboard polls `GET /runs/recent` and `GET /jobs` every 5 seconds; polling stops when no job has an in-progress run (same two-condition definition as Run Detail below).

### Jobs

Sortable table: name, source → destination, schedule, last run status + time (rendered as _"No runs yet"_ when `last_run` is null), next run time (rendered as _"—"_ when `next_run_time` is null — disabled jobs), enable/disable toggle, Run Now, edit/delete. Sorting is client-side — the browser sorts the full list returned by `GET /jobs`; no sort query params are sent to the API. Edit button tooltip: _"You can edit all fields except the destination and (after the first backup) the restic password, which are permanent once set."_ Delete shows a confirmation dialog: _"Delete [job name]? This removes the job configuration and all run history. The backup data at /destinations/{label}/{id} is NOT deleted — remove it manually to reclaim disk space."_ If `DELETE /jobs/{id}` returns `409` (run in progress): dismiss the dialog and show an error toast with the API's `detail` message. Run Now → `POST /jobs/{id}/run` → navigate immediately to the Run Detail page for the returned `run_id`, regardless of whether the run was started (`status=running`) or skipped (`status=skipped`) — both are valid rows already in the DB.

### Job Detail

Config summary at top showing: **name**, **enabled** status badge (_Scheduled_ / _Disabled_), **source** (label + subpath rendered as `label/subpath`, or just `label` when no subpath), **destination label** (with 🔒 icon), **schedule** (human-readable — e.g. _"Every 6 hours"_ or the raw cron expression), **retention policy** (compact summary of all non-null keep fields — e.g. _"Keep last 7, daily 30d"_ — or _"No policy (prune only)"_ when all null), **verification** (mode + subset % or _"Disabled"_ when `check_enabled=false`). Three tabs:

- **Run History**: full list ordered newest first (started, duration, files new/changed, data added, **backup status**, **check status**, triggered-by badge — `scheduler` or `manual`). The check-status column renders `passed` / `failed` / `skipped` / _pending_ via `RunStatusBadge` and is the only place a verification failure surfaces in this table — Step 12 (§8) keeps the run's `status=success` even when the integrity check fails, so the backup-status column alone would show a wall of green for runs whose verification was failing. All rows returned, no pagination.
- **Snapshots**: full list of `Snapshot` rows from `GET /jobs/{id}/snapshots` — snapshot time, ID, tags, paths, size. All rows returned, no pagination. No live restic call.
- **Restore Steps**: copy-pasteable `restic restore` commands. The password is rendered as the literal shell-variable reference `$RESTIC_PASSWORD` (never the real value). Two callouts appear above the snippet:

1. _"Set RESTIC_PASSWORD in your shell before running these commands. The app will never render your stored password here."_
2. _"These commands use the container-internal path `/destinations/{label}/{id}`. Run them from inside the container: `docker exec -it backup-server sh`. Alternatively, replace the path prefix with the host path for your destination drive (e.g. `/Volumes/BackupDrive/{id}`)."_

A unit test asserts the rendered HTML never contains the actual password value — guards against a future templating change accidentally substituting it. Commands are pre-filled with `RESTIC_REPOSITORY` (computed repo path `/destinations/{label}/{id}`) and use `latest` as the snapshot selector with `--target /tmp/restore` as a placeholder; `--include` is omitted (restores the entire snapshot by default). A note below the snippet reads: _"Replace `latest` with a specific snapshot ID from the Snapshots tab, and change `--target` to your desired restore path."_

**Unlock Repository button** — always visible in the Job Detail header, styled secondary/outline. Tooltip: _"Use this if recent runs are failing with 'repository is already locked' — usually caused by the container stopping during an active backup. The repository password stored in this job's configuration will be used to authenticate the unlock operation."_ On click: confirmation dialog → `POST /jobs/{id}/unlock` → result as toast. The button is **disabled** (with the tooltip _"A run is in progress for this job — unlock will be available when it finishes"_) whenever the Job Detail page sees an in-progress run for this job in its polled state, mirroring the `409` guard in §6 so the user gets immediate feedback rather than discovering the refusal after submitting the dialog. **An in-progress run for UI purposes is: `last_run.status === 'running'`, OR (`last_run.status` is terminal AND `last_run.check_status === null`).** When `last_run` is `null` (job has never run), neither condition is true — the button is enabled. The second condition covers the Step 10→12 window where `status=success` has been written but the integrity check is still executing and `_active_jobs` still holds the job UUID — the server's `409` is correct in that window, so the button must stay disabled until `check_status` becomes non-null.

### Run Detail

Layout adapts to outcome:

- **`success`**: metadata card (_"Backup duration"_ for `duration_seconds`, files new/changed/unmodified, dirs new/changed/unmodified, data added packed vs raw, snapshot ID). Collapsible _Backup log_ panel shows `backup_output` in monospace. If `prune_status=failed`: collapsible amber panel with `prune_error_output`. **Integrity check result** (always shown on success runs): `check_status=null` → _"Integrity check: running…"_ pending indicator (polling still active); `check_status=passed` → green badge _"Integrity check: passed"_; `check_status=failed` → red badge _"Integrity check: failed"_ + collapsible amber panel with `check_error_output`; `check_status=skipped` → muted note _"Integrity check: skipped"_
- **`failed`**: metadata card labeled _"Time to failure"_ for `duration_seconds` + scrollable `error_output`. If `error_output` contains `"repository is already locked"`: inline callout pointing to the Unlock button. If `reason=container_restart`: info card (_"This run was in progress when the container stopped and was automatically marked failed on next startup"_) — no error output panel. Prune status is not shown on failed runs — prune is always skipped when backup fails, so there is nothing actionable to display
- **`skipped`**: reason badge (_"Skipped — another run was already in progress"_). No stats or log
- **`running`**: metadata card with live elapsed time; stats as dashes. TanStack Query polls `GET /runs/{id}` every 5 seconds. Polling stops when **both** conditions are true: `status` is a terminal value (`success` / `failed` / `skipped`) **and** `check_status` is not null. This two-condition stop is required because Step 10 sets `status=success` before the integrity check executes in Step 12 — stopping on `status` alone would freeze `check_status` as null in the UI. When `check_enabled=false` the condition collapses to a single check, because Step 10 sets `check_status=skipped` in that case (§8 Step 10) so it is never null on a completed run. The Jobs list and Dashboard poll every 5 seconds while any job in the current view has an in-progress run; they stop polling when every visible `last_run` entry has a terminal `status` and a non-null `check_status`.

### Settings

ntfy: server URL, topic, token (masked), four notification toggles, Send Test Notification button. Global timeout: `default_job_timeout_hours` numeric input (_"Default backup / verification timeout (hours)"_), tooltip: _"Maximum time any backup or verification subprocess can run before killed and marked failed. Applied to jobs without their own timeout. Default: 24 h."_ Restic version (from DB) + Check for Update button → `GET /settings/restic-update-check`. Response rendering: `update_available=true` → badge _"Update available: v{latest}"_; `update_available=false` → _"Up to date"_; `update_available=null` and `current=null` (restic not detected) → _"restic not detected — cannot check for updates"_; `latest=null` (GitHub unreachable) → _"Could not reach GitHub to check for updates"_. All states are displayed inline next to the button. `422` responses from `PUT /settings` surface the `detail` string as an inline error below the relevant field (same pattern as JobForm).

**Destinations** section — rename form for when a backup drive is remounted under a new label:

- _Current label_ — dropdown populated from the unique `destination_label` values in `GET /jobs` (job-referenced labels, not the live mount scan — the old label is typically no longer mounted when this form is used)
- _New label_ — dropdown populated from `GET /mounts/destinations` (currently mounted destinations; excludes the selected current label)
- Submit → `POST /mounts/destinations/rename`. On success: toast listing affected job names. On `409`: inline error _"A backup is currently running for a job using this destination — wait for it to finish, then retry."_ On `422`: inline error _"New destination is not mounted — update docker-compose.yaml and restart the container first."_ On `404`: inline error _"No jobs reference that destination label."_

### JobForm

Two-step source picker in _Basic_:

1. **Source mount** — dropdown from `GET /mounts/sources`; selecting triggers `GET /mounts/sources/{label}/subdirs`
2. **Subdirectory (optional)** — second dropdown with _"Entire mount"_ + returned subdirs. Hidden if mount has no subdirectories

**Restic password field** (required, masked):

- **Create mode**: editable
- **Edit mode, `has_successful_run=false`**: editable, note _"No backups have run yet — you may still change this"_
- **Edit mode, `has_successful_run=true`**: read-only (masked placeholder), 🔒 icon, tooltip _"Permanent — the repo was initialised with this password. To rotate, use `restic key add/remove` on the command line."_

**Source label change**: amber banner when `source_label` is changed on an existing job: _"Changing the source mount will redirect future backups to a different directory. Make sure the new source is correctly mounted in docker-compose.yaml."_

**409 Conflict**: blocking error banner at top of _Basic_ section with link to conflicting job. The banner clears automatically once the user modifies source label, subpath, or destination label.

**Edit mode**: `destination_label` read-only always, with inline notice: _"The destination cannot be changed after creation — it forms part of the restic repository path."_ Helper link: _"Drive remounted under a new label? Use the Destinations rename tool in Settings."_

**Form sections** (collapsible; _Basic_ always expanded):

- _Basic_ — name, source, destination, password, schedule, enabled (checkbox, default checked — unchecking creates the job without scheduling it)
- _Retention Policy_ — all `--keep-*` and `--keep-within-*` fields
- _Backup Options_ — excludes, tags, compression, timeout, other flags
- _Verification_ — check settings. When `check_enabled` is toggled on, `check_mode` becomes a required field — the dropdown is highlighted and the form cannot be submitted without it. When `check_mode=subset` is selected, `check_subset_percent` additionally becomes required — the field is highlighted and the form cannot be submitted without it. All `422` responses from the backend surface the `detail` string as an inline field error adjacent to the offending field.

### ScheduleInput

A two-mode sub-component used within the _Basic_ section of `JobForm`.

- **Mode toggle**: segmented control — _Cron_ | _Interval_. Switching mode clears the value from the other mode.
- **Cron mode**: free-text field for a standard 5-field cron expression (e.g. `0 2 * * *`). Shows a human-readable next-run preview beneath the field (e.g. _"Next: tomorrow at 2:00 AM"_) when the expression is valid. Invalid expressions show an inline error: _"Invalid cron expression."_
- **Interval mode**: text field accepting `<N>h`, `<N>d`, or `<N>m` (e.g. `6h`, `1d`, `30m`). Only these three unit suffixes are valid; any other format shows an inline error: _"Use format: 6h, 1d, or 30m."_
- The component emits `{ type: 'cron' | 'interval', value: string }` to the parent form and writes to `schedule_type` / `schedule_value`.

Every optional field has an **(optional)** label and a tooltip (ⓘ):

| Field                  | Tooltip                                                                                                                                                                                                                           |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `retain_keep_last`     | _"Keep the N most recent snapshots regardless of age. Example: `5`"_                                                                                                                                                              |
| `retain_keep_within`   | _"Keep all snapshots within this window. Example: `30d`, `2w`. Accepts: `Nd`, `Nw`, `Nm`, `Ny`, `Nh` (days, weeks, months, years, hours)"_                                                                                        |
| `exclude_patterns`     | _"Glob patterns to skip, one per line. Example: `node_modules/`, `_.tmp`, `.DS_Store`"\*                                                                                                                                          |
| `exclude_if_present`   | _"Skip a directory if it contains a file with this name. Example: `.nobackup`"_                                                                                                                                                   |
| `exclude_caches`       | _"Skip directories containing a `CACHEDIR.TAG` file — standard marker used by browsers, package managers, and build tools"_                                                                                                       |
| `one_file_system`      | _"Don't cross filesystem mount boundaries — useful when source contains network drives or sub-volumes to exclude"_                                                                                                                |
| `tags`                 | _"Labels attached to each snapshot. Example: `documents`, `weekly`"_                                                                                                                                                              |
| `compression`          | _"`auto` compresses compressible data (default). `max` tries harder but is slower. `off` disables"_                                                                                                                               |
| `pack_size`            | _"Internal pack file size in MiB. Leave blank for restic default (128 MiB). Increase (e.g. `512`) for large repos to reduce destination file count"_                                                                              |
| `read_concurrency`     | _"Source files read in parallel. Leave blank for restic's automatic default"_                                                                                                                                                     |
| `no_scan`              | _"Skip pre-scan for total size estimate. Backup starts immediately but no progress percentage is shown"_                                                                                                                          |
| `timeout_hours`        | _"Max time the backup can run before killed and marked failed. Leave blank for global default. Does not apply to verification — use Verification timeout for that"_                                                               |
| `check_enabled`        | _"Run integrity check after every successful backup. `structural` is fast (seconds). `subset` reads a data percentage (~25–30 min for large repos). `full` reads everything and is impractical after every backup"_               |
| `check_mode`           | _"`structural` — verifies index integrity and that all referenced blob IDs exist in the index; no pack file reads. `subset` — reads and verifies a random % of packs (recommended). `full` — reads and verifies every pack file"_ |
| `check_subset_percent` | _"% of packs to read in subset mode. Example: `5` covers the repo statistically over 20 runs. Range: 1–100"_                                                                                                                      |
| `check_timeout_hours`  | _"Max time for integrity check before killed and marked failed. Check failure is non-fatal — backup run stays success. Leave blank for global default. Note: subset at 5% on 3 TB ≈ 25–30 min; full ≈ 8–11 h"_                    |

---

## 15. Testing Strategy

**TDD throughout**: tests are written against this spec before implementation. The suite must catch regressions, serve as living documentation, and give confidence to modify any module.

**Backend stack**: pytest + pytest-asyncio + httpx `AsyncClient`

**`conftest.py` fixtures**: fresh in-memory SQLite per test; `AsyncClient` with test DB injected via dependency override; all `restic.py` subprocess calls patched globally; ntfy calls patched; `AsyncIOScheduler` patched to use `MemoryJobStore` and configured to run synchronously within the test event loop (no background threads). **An autouse fixture clears `backup_runner._job_locks` and `backup_runner._active_jobs` before each test** — both are module-level and would otherwise leak state across tests (a prior test's UUID could remain "active" and cause the next test to write a `skipped` row instead of `running`).

---

### `test_jobs.py`

- `POST /jobs` creates job, registers in APScheduler when `enabled=True`; no registration when `enabled=False`
- `POST /jobs` returns `409` on duplicate source+subpath+destination; `422` when `restic_password` missing
- `PUT /jobs/{id}` updates all editable fields and reschedules APScheduler job
- `PUT /jobs/{id}` with changed `source_label` — succeeds; warning is client-side only, no flag in response
- `PUT /jobs/{id}` with changed `destination_label` — returns `422`
- `PUT /jobs/{id}` with changed `restic_password` before first successful run — succeeds; after — `422`
- `PUT /jobs/{id}` with `restic_password` absent or empty — leaves stored password unchanged
- `PUT /jobs/{id}` duplicate check excludes the job being updated
- `PUT /jobs/{id}` on a disabled job — succeeds without raising `JobLookupError` (disabled jobs are not registered in APScheduler)
- `DELETE /jobs/{id}` removes from APScheduler, pops `_job_locks`, cascades to `BackupRun` rows
- `DELETE /jobs/{id}` on a disabled job — succeeds without raising `JobLookupError`
- `DELETE /jobs/{id}` returns `409` with the `"A run is currently in progress for this job"` message when `job.id ∈ _active_jobs`; the job and its rows remain intact
- `GET /jobs` returns last run summary and `has_successful_run` flag; `GET /jobs/{id}` returns full detail with `has_successful_run`; `has_successful_run` is `false` with no runs, `false` with only failed/skipped runs, and `true` after any successful run; each `last_run` object includes `check_status`
- `GET /jobs` and `GET /jobs/{id}` responses do not contain the `restic_password` value — it is returned as `null` in both responses regardless of whether the job has a password set
- `GET /jobs/{id}` returns `404` when the job does not exist; `GET /runs/{id}` returns `404` when the run does not exist
- Enable/disable endpoints register/deregister from APScheduler
- `POST /jobs/{id}/run` synchronously creates the `BackupRun` row before returning — the `run_id` returned is queryable via `GET /runs/{id}` immediately (no race window where the row does not yet exist)
- `POST /jobs/{id}/run` when the job is already in `_active_jobs`: returns the ID of a `status=skipped`, `reason=overlapping_run` row; no runner task is scheduled
- `POST /jobs/{id}/run` runner task scheduling failure after `_active_jobs.add()`: `_active_jobs` is empty afterward (discard called before re-raise); the `status=running` row remains for stale-run cleanup on next startup
- `POST /jobs/{id}/unlock` returns `409` when `job.id ∈ _active_jobs`; no `restic unlock` subprocess is invoked
- Null retention fields excluded from CLI; set fields included correctly
- Invalid cron → `422`; cron with min-gap below 5 minutes (e.g. `* * * * *`) → `422` with the `"cron expression fires too often"` message; `check_subset_percent` outside 1–100 → `422`; `check_enabled=true` with `check_mode=null` → `422` on both `POST` and `PUT`; `check_mode=subset` with `check_subset_percent` null → `422` on both `POST` and `PUT`
- Interval `m` values below 5 (e.g. `1m`, `4m`) → `422` with the interval format message; `5m` → accepted; interval `h` and `d` values of `1` → accepted (minimum applies to `m` only)
- Path-traversal validation: `source_label` / `destination_label` that are `.` or `..`, contain `..` as a substring, contain `/`, `\`, or any character outside `[A-Za-z0-9._-]` → `422` with the label regex message — covers `POST /jobs` and `PUT /jobs/{id}`
- `source_subpath` containing `/` or `..` → `422` with the subpath message — covers `POST` and `PUT`
- `interval` schedule_value outside the `^([1-9][0-9]*)(h|d|m)$` shape, or with N outside `[1, 8760]`, → `422`
- `POST /jobs` with a `source_label` that is not present under `/sources/` → `422` with the source-mount message; `PUT /jobs/{id}` with a `source_label` that is unchanged → mount check is skipped (no `422`); `PUT /jobs/{id}` with a changed `source_label` that is not mounted → `422`
- `POST /jobs` with a `destination_label` that is not present under `/destinations/` → `422` with the destination-mount message
- `POST /jobs` with no explicit `check_enabled` field → stored value is `false`; `exclude_caches` and `one_file_system` omitted → stored as `false`; `enabled` omitted → stored as `true` and job is registered in APScheduler
- `GET /jobs` includes `next_run_time` (non-null ISO timestamp for enabled jobs, `null` for disabled jobs); `GET /jobs/{id}` includes the same field
- `PUT /jobs/{id}` with `enabled` changed from `true` to `false` → job is deregistered from APScheduler; from `false` to `true` → job is registered
- `POST /jobs/{id}/enable` called on an already-enabled job succeeds without raising `ConflictingIdError` (remove-then-add is idempotent); `POST /jobs/{id}/disable` called on an already-disabled job succeeds without raising `JobLookupError`
- `POST /jobs/{id}/run` on a disabled job returns a valid `run_id` — the `enabled` flag controls scheduler registration only, not manual triggers
- `PUT /jobs/{id}` on a non-existent job → `404`; `DELETE /jobs/{id}` on a non-existent job → `404`; `POST /jobs/{id}/run` on a non-existent job → `404`; `POST /jobs/{id}/unlock` on a non-existent job → `404`
- Field validation: `name` empty string → `422`; `name` exceeding 128 chars → `422`; `retain_keep_last` of `0` or negative → `422`; `retain_keep_within` with invalid duration string (e.g. `30x`, `0d`) → `422`; `pack_size` of `0` → `422`; `pack_size` of `1025` → `422`; `read_concurrency` of `0` → `422`; `read_concurrency` of `129` → `422`; `timeout_hours` of `0` → `422`; `timeout_hours` of `169` → `422`; `check_timeout_hours` of `0` or `169` → `422` — all on both `POST` and `PUT`

### `test_runs.py`

- `GET /runs/recent` — returns rows across all jobs ordered by `started_at` desc; `limit` param respected (default 10, min 1, max 100); `limit` above 100 returns `422`; `limit` of 0 or negative returns `422`; each row includes all `BackupRun` fields (including `check_status`) plus `job_id` and `job_name`; `backup_output`, `error_output`, `prune_error_output`, and `check_error_output` are absent from every row; empty list when no runs exist
- `GET /jobs/{id}/runs` — all rows, newest first, includes skipped runs with `reason`; `backup_output`, `error_output`, `prune_error_output`, and `check_error_output` are absent from every row; no pagination params accepted
- `GET /jobs/{id}/snapshots` — all rows, ordered by `snapshot_time` desc; no subprocess; empty list for jobs with no successful runs; no pagination params accepted
- `GET /runs/{id}` for success — `error_output=null`, `backup_output` non-null, `prune_status` set, `check_status` set, all stat fields present (confirms output fields are returned by this endpoint but not by list endpoints)
- `GET /runs/{id}` for failed — `error_output` populated, `backup_output=null`, `prune_status=skipped`, `check_status=skipped`
- `GET /runs/{id}` for skipped — `status=skipped`, `reason=overlapping_run`, null stats, both sub-statuses skipped
- `GET /runs/{id}` for container-restart — `status=failed`, `reason=container_restart`, `error_output=null`, `duration_seconds=null`
- `GET /runs/{id}` — includes `check_status` and `check_error_output` when check ran
- `GET /runs/{id}` for a `status=running` row — `finished_at=null`, `duration_seconds=null`, all stat fields null, `backup_output=null`, `error_output=null`, `prune_status=null`, `check_status=null`; confirms the row is queryable immediately after `POST /jobs/{id}/run` returns

### `test_backup_runner.py`

- Full happy path: `running → success`, all stat fields populated, `error_output=null`, `backup_output` non-null; `duration_seconds` reflects backup-only time (Steps 2–7) — inject a delay in the prune mock and assert `duration_seconds` does not include it
- Step ordering: `status=running` row written inside lock before release; password validation updates (not creates) the running row to `failed`
- Missing `restic_password`: running row updated to `failed`; no subprocess called
- Restic `backup` failure: `status=failed`, `error_output` captured, `backup_output=null`, steps 8–9 and 12 skipped
- Restic `forget` failure: run stays `success` (non-fatal); Step 9 still executes
- `restic prune` used (not `forget --prune`) when all retention fields null; `restic forget --prune` used when any retention field is set
- Snapshot reconciliation: rows created/updated; pruned snapshots deleted; `size_bytes` set from backup summary on the new snapshot row only; pre-existing rows unaffected
- Snapshot reconciliation upsert preserves prior values: a row inserted by an earlier run (with `run_id` and `size_bytes` populated) keeps both fields after a later reconciliation overwrites `hostname`, `paths`, `tags` from the latest `restic snapshots --json` payload — covered by an explicit assertion that `run_id` and `size_bytes` are unchanged
- Repo absent (`cat config` stderr matches "not a restic repository"): `restic init` called; repo present: init not called
- `cat config` fails with wrong-password or other non-absence error: `status=failed`, init not attempted, error output captured
- Concurrent guard — lock path: two simultaneous scheduler callbacks → one `running`, one `skipped` (`reason=overlapping_run`)
- Concurrent guard — `_active_jobs` membership persists across the integrity-check phase: with `check_enabled=true`, a second scheduler firing while Step 12 is still running produces a `skipped` row even though the prior run's `BackupRun.status` is already `success`
- `_active_jobs.discard(job.id)` runs in the outer `finally` regardless of the exit path — covered for: success, backup failure, prune failure, check failure, backup timeout, check timeout, and an injected exception during Step 9
- Manual run entry: a pre-created `running` row provided by the API takes the runner straight to Step 2 (Step 1 lock/insert is not exercised); `_active_jobs` is still cleared in the outer `finally`
- Backup timeout: `status=failed`, timeout message in `error_output`, `prune_status=skipped`, `check_status=skipped`
- Job `timeout_hours` overrides `default_job_timeout_hours` for backup
- `check_enabled=False`: `check_status=skipped` (never null on a completed run)
- Prune success → `prune_status=passed`; failure → `prune_status=failed`, `prune_error_output` set, run stays `success`
- Prune timeout: `prune_status=failed`, `prune_error_output="Prune timed out after N hours"`, run `status` stays `success`; Step 9 still executes
- `check_enabled=True`, `structural`: `restic check` with no extra flags
- `check_enabled=True`, `subset` at 10%: `restic check --read-data-subset=10%`
- `check_enabled=True`, `full`: `restic check --read-data`
- Check timeout: `check_status=failed`, timeout message, run `status` stays `success`
- Job `check_timeout_hours` overrides `default_job_timeout_hours` for check; independent of `timeout_hours`
- Check failure (non-timeout): `check_status=failed`, run `status` unchanged
- All four ntfy notifications fire in correct order on happy path; each toggle independently disables its notification
- ntfy POST sends `Authorization: Bearer <token>` header when `ntfy_token` is set, and no auth header when it is null/empty
- ntfy excerpt on failure contains the **last 500 chars** of `error_output` (not the first); ntfy timeout (10 s) failures are logged and do not affect the run's outcome
- Step 5 streams stdout into a 1 MB ring buffer that drops earliest lines when capacity is exceeded; when buffer dropped lines, persisted `backup_output` is prefixed with `[output truncated at 1 MB — earliest lines dropped]`
- Step 5 captures the JSON summary line separately so Step 6 succeeds even when the ring buffer dropped the rest of the stream
- Step 6 parse failure with exit=0: run completes as `status=success`, all stat fields null, `backup_output` ends with the `[summary parse failed: ...]` marker; Step 9 still runs
- `restic_password` passed via env; never in any DB output field; if a contrived restic stdout containing the password literal is fed in, the persisted output has every occurrence replaced with `[REDACTED]`; same redaction verified independently in `prune_error_output` (inject password into mocked prune stderr) and `check_error_output` (inject password into mocked check stderr)
- Early exits set `finished_at`: Step 2 missing-password row has non-null `finished_at`; Step 4 init-failure row has non-null `finished_at`; Step 5 timeout row has non-null `finished_at` — all three paths explicitly asserted (we verified `finished_at` is set in §8, these tests guard against regressions)
- `duration_seconds` is `null` (not `0`) for skipped runs; `null` for `reason=container_restart` runs
- `error_output` assembly order on backup non-timeout failure: inject distinct stdout and stderr content into the mock; assert persisted `error_output` has stdout first then stderr (stderr last — so the 1 MB tail cap preserves the actionable error line)
- Step 9 failure: mock `restic snapshots --json` to exit non-zero; assert run still proceeds to Step 10 and `BackupRun.status=success`; assert Snapshot table is unchanged (no inserts, no deletes); assert the failure is logged
- Individual `notify_on_*` flag suppression: `notify_on_start=false` → Step 3 ntfy POST not called; `notify_on_success=false` on a success run → Step 11 ntfy POST not called; `notify_on_failure=false` on a failed run → Step 11 ntfy POST not called; `notify_on_verification=false` → neither the "verification started" nor "verification complete" Step 12 ntfy POST is called — each flag tested independently with all other flags `true`
- Prune non-timeout non-zero exit: mock `restic forget --prune` to exit non-zero with distinct stdout and stderr; assert `prune_status=failed`; assert `prune_error_output` contains both stdout and stderr (stderr appended last); assert run `status` stays `success`; assert Step 9 still executes

### `test_scheduler.py`

- `AppSettings` singleton row (id=1) inserted with defaults on first startup; no-op on subsequent startups
- `AppSettings.restic_version` updated at startup if changed; unchanged if version matches
- Stale `status=running` rows set to `failed` + `reason=container_restart` before scheduler starts
- `BackupRun` rows with `status=success` or `status=failed` and `check_status=null` (container killed between Step 10 and Step 12) are corrected to `check_status=skipped` during startup stale-run cleanup; their `status` is unchanged
- All `enabled=True` jobs registered on startup; `enabled=False` not registered
- Scheduler is constructed with `job_defaults={'misfire_grace_time': 3600, 'coalesce': True}`; these are constructor-level defaults, not per-`add_job` kwargs — verified by inspecting the scheduler's `_job_defaults` attribute, not the individual `add_job` call signatures
- Scheduler is constructed with `timezone` taken from the `TZ` env var (and falls back to UTC when unset)
- CronTrigger and IntervalTrigger fire correctly; invalid interval rejected at job creation
- `DELETE /jobs/{id}` removes entry from `_job_locks`
- Job survives simulated container restart (re-loaded from DB)
- Startup degraded mode: when seed-AppSettings raises, startup continues; `/health` is reachable; the failure is logged
- Startup degraded mode: when `restic version` hangs, the 5 s timeout fires and `restic_version` stays null; startup continues
- Startup degraded mode: when stale-run cleanup raises, startup continues; orphaned `status=running` rows remain in the DB, but `_active_jobs` is empty — the next scheduler firing for the affected job proceeds normally (writes a new `running` row and starts a backup), leaving the orphaned row as a visible data inconsistency that the operator can identify by querying run history
- Startup degraded mode: when `scheduler.start()` raises, the API still serves; `/health.scheduler_running` is `false`
- `_job_locks.setdefault` semantics: first call for a new `job_id` creates a new `asyncio.Lock` and stores it; a second call with the same `job_id` returns the identical lock object (Python `id()` equality) — guards against the `_job_locks[job_id]` `KeyError` regression
- Scheduler is constructed with `timezone=UTC` when `TZ` env var is unset; with `timezone` matching the value of `TZ` when it is set — verified by inspecting the scheduler's `timezone` attribute before `scheduler.start()`
- Stale-run cleanup pass 2 boundary: a row with `status=running` and `check_status=null` is modified by pass 1 (set to `failed`, `check_status=skipped`); it is NOT double-modified by pass 2 (pass 2 only processes `status IN ('success', 'failed')` rows, which this row becomes after pass 1 sets it to `failed` — but with `check_status` now `skipped`, not null, so pass 2 skips it); assert the row's final state reflects only pass 1

### `test_restic.py`

- CLI built correctly from a fully-populated job config; null fields excluded
- `source_subpath` path construction: when `source_subpath=null` the backup source path is `/sources/{label}`; when `source_subpath="photos"` the path is `/sources/{label}/photos` — tested as two separate cases
- `exclude_patterns` → one `--exclude` per pattern; `exclude_if_present` → one `--exclude-if-present` per entry; `tags` → one `--tag` per tag
- Retention flags passed/omitted correctly
- JSON summary parsed correctly from realistic restic stdout
- Non-zero exit code raises expected exception
- `POST /jobs/{id}/unlock` calls `restic unlock` with correct repo path and `restic_password` from DB; returns subprocess output
- All subprocess calls receive both `RESTIC_REPOSITORY=/destinations/{job.destination_label}/{job.id}` and `RESTIC_PASSWORD` as env vars; neither appears in any DB field or log

### `test_mounts.py`

- `/mounts/sources` and `/mounts/destinations` return direct subdirectories only — regular files, sockets, FIFOs, and broken symlinks excluded; symlinks resolving to a directory are included
- `/mounts/sources/{label}/subdirs` — direct subdirs only (not recursive); empty list when none; `404` when label doesn't exist; same directory filter
- Empty `/sources/` returns empty list
- `POST /mounts/destinations/rename` — all jobs referencing `old_label` updated atomically; returns `{ affected_jobs }` list with correct IDs and names
- `POST /mounts/destinations/rename` — `404` only if no `BackupJob` rows reference `old_label`; **succeeds when `old_label` is no longer mounted** (the primary use case — drive remounted under a new label)
- `POST /mounts/destinations/rename` — `422` if `/destinations/new_label` is not mounted
- `POST /mounts/destinations/rename` — `422` if `old_label == new_label`
- `POST /mounts/destinations/rename` — `422` if `old_label` fails the label regex `^[A-Za-z0-9._-]{1,64}$`
- `POST /mounts/destinations/rename` — `422` if `new_label` fails the label regex `^[A-Za-z0-9._-]{1,64}$`
- `POST /mounts/destinations/rename` — jobs with other destination labels are unaffected
- `POST /mounts/destinations/rename` — `409` if any `BackupJob` with `destination_label == old_label` has its UUID in `_active_jobs`; no DB writes are made; other jobs are unaffected
- `POST /mounts/destinations/rename` — DB write failure rolls back all changes; no partial update

### `test_settings.py`

- `GET/PUT /settings` round-trips all fields including `default_job_timeout_hours`
- `PUT /settings` validation: `ntfy_server_url` not starting with `http://` / `https://` → `422`; `default_job_timeout_hours` outside `[1, 168]` → `422`; `ntfy_topic` outside `^[A-Za-z0-9_-]{1,64}$` (and not the empty string) → `422`
- `POST /settings/test-ntfy` sends correct payload, including `Authorization: Bearer <token>` header when `ntfy_token` is set
- `GET /settings/restic-update-check` returns correct `update_available` flag (GitHub API mocked for both cases); when `restic_version` is null, returns `{ current: null, latest: "<mocked version>", update_available: null }`; GitHub API timeout or error → returns `{ current: "<current>", latest: null, update_available: null }` and logs the error; GitHub call uses a 10-second timeout
- `AppSettings.restic_version` populated on startup; updated if changed
- `GET /health` returns scheduler state, restic version, DB status; `db_ok` is `true` for a working engine and `false` when the engine is patched to raise on `SELECT 1`; the endpoint returns `200` even when `scheduler_running=false`
- `PUT /settings` with `ntfy_server_url` exceeding 512 chars → `422`
- `PUT /settings` with `ntfy_token` set to a string exceeding 512 chars → `422`
- `POST /settings/test-ntfy` when the ntfy server returns a non-2xx status → `200 { "ok": false, "error": "<reason>" }` (our API does not forward the ntfy error as a 4xx/5xx — the non-2xx is a soft failure); `POST /settings/test-ntfy` when `ntfy_topic` is empty → `422`

---

**Frontend stack**: Vitest + React Testing Library + MSW (all API calls intercepted)

### `JobForm`

- Required fields show validation errors on empty submit; optional fields accept empty values
- Every optional field renders **(optional)** label and non-empty tooltip
- `409` → blocking error banner with conflicting job link; not resubmittable without identity field change
- Correct API payload on submit (null optionals omitted)
- Edit mode pre-populates all fields; `destination_label` read-only; `restic_password` read-only when `has_successful_run=true`, editable when `has_successful_run=false`
- `source_label` change shows amber warning banner
- Empty `restic_password` on submit does not change stored password
- Password field masked in create and edit modes
- `check_enabled` toggled on: `check_mode` dropdown becomes required — submit button stays disabled and dropdown is highlighted until a mode is selected; toggling `check_enabled` off again clears the requirement
- `check_mode=subset` selected: `check_subset_percent` field becomes required — submit stays disabled until a value is entered; switching `check_mode` away from `subset` removes the requirement
- `422` response from `POST /jobs` or `PUT /jobs/{id}` with a `detail` string: rendered as an inline error directly below the relevant field (not a top-level banner, not a toast)
- Source subdirectory picker is hidden when `GET /mounts/sources/{label}/subdirs` returns an empty array — no "Subdirectory (optional)" dropdown rendered
- Successful `POST /jobs` → navigates to `/jobs/{new_id}` (the newly created job's detail page)
- `409` error banner disappears automatically once the user changes source label, subpath, or destination label — prior to that change, the form cannot be resubmitted

### `ScheduleInput`

- Switching type clears the opposing value; invalid cron shows error; valid cron shows next-run preview
- `6h`, `1d`, `30m` accepted; arbitrary strings rejected
- Interval minimum enforcement: `1m` and `4m` show the format error inline; `5m` is accepted without error (the 5-minute minimum applies only to the `m` unit; `1h` and `1d` are accepted)
- Malformed interval formats: no-number (`m`), wrong suffix (`5x`), uppercase suffix (`6H`), decimal (`1.5h`) → format error inline; `0m` → format error (minimum is 5)

### `RunStatusBadge`

- Correct colour and label for all four run statuses and all three check statuses

### `Jobs`

- Table renders all job rows with name, source → destination, schedule, last run status + time, next run time
- Enable/disable toggle calls `POST /jobs/{id}/enable` or `disable` and reflects new state
- Run Now button calls `POST /jobs/{id}/run` and shows confirmation feedback; on a `200` response with a row that already exists in the DB, the user is navigated to Run Detail without a 404
- Delete shows confirmation dialog before calling `DELETE /jobs/{id}`; row removed on success
- Delete on a job with an active run: server returns `409`; UI surfaces the `detail` string in a toast and does not remove the row
- Polling stop condition: stops when both `last_run.status` is terminal and `last_run.check_status` is not null; for jobs with `check_enabled=false`, `last_run.check_status=skipped` so polling stops immediately on terminal status

### `Dashboard`

- Total/enabled counts, last 10 runs (from `GET /runs/recent`), restic version, next-run times, disk space callout
- Each "last 10 runs" row renders both `status` and `check_status` badges so verification failures are visible
- Red banner displays at the top when `GET /health` returns `scheduler_running=false`
- Empty state: renders correctly when `GET /jobs` returns `[]` and `GET /runs/recent` returns `[]` — no jobs count, no run rows, disk space callout still present
- Polling: while any `last_run` entry has an in-progress run (status `running` OR terminal status with null `check_status`), component re-polls every 5 seconds; stops re-polling once all `last_run` entries have a terminal status and non-null `check_status`

### `Settings`

- All ntfy toggles render and toggle; test notification calls correct endpoint
- Update check shows correct message; token field masked; `default_job_timeout_hours` saves correctly
- Destinations rename: current-label dropdown populated from unique `destination_label` values in `GET /jobs`; new-label dropdown populated from `GET /mounts/destinations` with current label excluded; success shows toast with affected job names; `409` shows active-run error; `422` shows mounted error or same-label error; `404` shows no-jobs error
- `PUT /settings` with an invalid `ntfy_server_url` (non-http/https scheme) → `422` → inline error shown below the URL field (not a generic toast)
- All four `GET /settings/restic-update-check` response states render the correct message: `update_available=true` → "Update available: v{latest}"; `update_available=false` → "Up to date"; `update_available=null` with `current=null` → "restic not detected — cannot check for updates"; `latest=null` → "Could not reach GitHub to check for updates"

### `RunDetail`

- Success: metadata card, backup log panel shown, no error panel; prune failure shows amber panel
- Failed: error panel; lock error → unlock callout; `reason=container_restart` → info card, no error panel
- Skipped: reason badge, no stats or log
- Check passed/failed badges render correctly; failed check shows `check_error_output` panel
- `triggered_by` badge renders correctly for both `scheduler` and `manual` values
- `duration_seconds` is labeled _"Backup duration"_ on success runs and _"Time to failure"_ on failed runs — per §8 Step 10 (success covers Steps 2–7 only; failure covers time from run start to the point of failure)
- Polling continues after `status=success` while `check_status` is null (check still running); stops only when both `status` is terminal and `check_status` is not null
- Polling stops immediately at terminal `status` when `check_enabled=false` (`check_status=skipped` is set in Step 10, never null on a completed run)
- `status=success`, `check_status=null`: renders "Integrity check: running…" pending indicator; polling is still active in this state — assert the component has not stopped re-fetching
- `status=success`, `check_status=skipped`: renders muted note "Integrity check: skipped"; no amber error panel present
- `running` state: live elapsed time is displayed (computed client-side from `started_at` and current time); assert the rendered value increments with time (e.g. mock `Date.now` to return a value 90 seconds after `started_at` and assert "1 min 30 s" or equivalent)
- `status=failed`, `prune_status=skipped`: no prune note is rendered (prune is always skipped on failed runs — §14 does not display it); `error_output` panel is shown for normal failures; no error output panel when `reason=container_restart`

### `SnapshotList`

- Renders snapshot rows with all defined fields: snapshot time, snapshot ID, tags (comma-separated or empty), paths, and size (human-readable bytes); empty-state message rendered when the list is empty; does not call any restic subprocess endpoints — data is DB-only

### `JobDetail`

- Unlock button always visible; click → confirmation dialog → `POST /jobs/{id}/unlock` → toast with result
- Unlock button is **disabled** with the active-run tooltip whenever the polled state shows an in-progress run for the job; the button becomes enabled again as soon as the run terminates
- Unlock button is **disabled** when `last_run.status` is terminal but `last_run.check_status` is null (Step 10→12 window — integrity check still running); becomes enabled only when `check_status` is non-null
- Unlock button is **enabled** when `last_run` is null (job has never run — no active run possible)
- Tab navigation switches between Run History, Snapshots, and Restore Steps
- Run History tab: rows show started time, duration, files changed, data added, **backup-status badge, check-status badge**, triggered-by badge; all rows rendered with no pagination controls
- Run History tab: a row whose backup `status=success` but `check_status=failed` renders with a green backup badge and a red check badge (regression guard for the §8 invariant that check failure does not change run status)
- Snapshots tab: delegates to `SnapshotList`; renders within tab context correctly
- Restore Steps tab: rendered commands include the correct destination label and use the literal `$RESTIC_PASSWORD` shell-variable reference; the rendered HTML never contains the actual `restic_password` value (asserted via direct string-not-in-output check); both callouts are present — the password callout and the container-path callout (with `docker exec -it backup-server sh` instruction)
- Config summary renders all specified fields: job name, enabled badge (_Scheduled_ / _Disabled_), source label with subpath (rendered as `label/subpath` when subpath is set, just `label` otherwise), destination label with 🔒 icon, schedule in human-readable form, retention policy summary (_"No policy (prune only)"_ when all null), verification summary (_"Disabled"_ when `check_enabled=false`, mode+percent otherwise)
- Run History tab: a row with `check_status=skipped` renders the check badge as "skipped" (not null or blank); verifies all four check badge states (`passed`, `failed`, `skipped`, pending) are handled by `RunStatusBadge` within the table context

---

## 16. Open Items / Future Considerations

1. **DB backup**: SQLite at `/app/data/backup.db` contains all config and run history. Consider a job that backs this file up to the destination drive so configuration is recoverable after hardware failure.
2. **Live progress in Run Detail**: §14 Run Detail in the `running` state shows live elapsed time but no percent or ETA. `restic backup --json --verbose` emits intermediate `status` messages with `percent_done`, `total_files`, and `bytes_done`; a future iteration could surface them via a `/runs/{id}/progress` endpoint that tails the buffered stdout. Out of scope for v1 to keep the per-run state model minimal.
3. **Cancel a running backup**: there is no API to cancel an in-progress run; a run must finish or be terminated by container restart (which the stale-run cleanup at §7 startup converts to `failed` / `reason=container_restart`). `DELETE /jobs/{id}` and `POST /jobs/{id}/unlock` return `409` while a run is active. A cancel endpoint would require defining a graceful subprocess-shutdown protocol and is out of scope for v1.
4. **Migration safety on container downgrade**: `alembic upgrade head` runs at every container start (§11 ENTRYPOINT). If a user mounts a `backup.db` written by a newer schema and starts an older image, the older code may fail to read the DB. Document this constraint in the upgrade guide rather than handle it at runtime.
5. **Disk space monitoring**: §9 already calls out that disk space is not monitored. A future iteration could surface `statvfs` on each mounted destination on the Dashboard so the user can spot a drive about to fill.
6. **Container healthcheck**: the `docker-compose.yaml` has no `healthcheck:` directive. Adding one against `GET /health` would let Docker detect a process-alive-but-unresponsive scenario and restart the container automatically, complementing the `restart: unless-stopped` policy.

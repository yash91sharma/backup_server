import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.db.database import engine
from app.db.models import (
    AppSettings,
    BackupJob,
    BackupRun,
    CheckStatus,
    PruneStatus,
    RunStatus,
    Snapshot,
    TriggeredBy,
)
from app.services import restic
from app.services.notifications import send_notification

logger = get_logger(__name__)

_active_jobs: Set[uuid.UUID] = set()
_job_locks: Dict[uuid.UUID, asyncio.Lock] = {}


async def run_backup(job_id: uuid.UUID, run_id: Optional[uuid.UUID] = None) -> None:
    """12-step backup lifecycle orchestration."""
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )

    # Pre-step: Job lookup
    async with factory() as s:
        job = await s.get(BackupJob, str(job_id))

    if not job:
        logger.warning(f"job_id={job_id} not found in database")
        return

    logger.info(f"job_id={job_id} run_id={run_id} backup_started")

    # Two invocation paths: scheduler (no run_id) or API (run_id provided)
    try:
        if run_id is None:
            # Scheduler path: do concurrent run guard and create run row
            logger.debug(f"job_id={job_id} step=concurrent_guard acquiring lock")
            lock = _job_locks.setdefault(job_id, asyncio.Lock())
            async with lock:
                async with factory() as s:
                    result = await s.execute(
                        select(BackupRun).where(
                            BackupRun.job_id == str(job_id),
                            BackupRun.status == RunStatus.running,
                        )
                    )
                    running_run = result.scalars().first()

                if running_run:
                    logger.info(
                        f"job_id={job_id} step=concurrent_guard "
                        f"reason=overlapping_run skipping"
                    )
                    async with factory() as s:
                        run = BackupRun(
                            id=str(uuid.uuid4()),
                            job_id=str(job_id),
                            status=RunStatus.skipped,
                            reason="overlapping_run",
                            started_at=datetime.now(timezone.utc),
                            finished_at=datetime.now(timezone.utc),
                            triggered_by=TriggeredBy.scheduler,
                            prune_status=PruneStatus.skipped,
                            check_status=CheckStatus.skipped,
                        )
                        s.add(run)
                        await s.commit()
                    return

                # Create running row
                now = datetime.now(timezone.utc)
                async with factory() as s:
                    run = BackupRun(
                        id=str(uuid.uuid4()),
                        job_id=str(job_id),
                        status=RunStatus.running,
                        started_at=now,
                        triggered_by=TriggeredBy.scheduler,
                    )
                    s.add(run)
                    await s.commit()
                    run_id = uuid.UUID(run.id)
                logger.debug(
                    f"job_id={job_id} run_id={run_id} step=concurrent_guard "
                    f"run_row_created"
                )

        _active_jobs.add(job_id)
        logger.debug(f"job_id={job_id} run_id={run_id} added to active_jobs")

        # Step 2: Validate password
        logger.debug(f"job_id={job_id} run_id={run_id} step=validate_password")
        if not job.restic_password:
            logger.error(
                f"job_id={job_id} run_id={run_id} step=validate_password "
                f"error=no_password_configured"
            )
            async with factory() as s:
                run = await s.get(BackupRun, str(run_id))
                run.status = RunStatus.failed
                run.error_output = "No restic password configured for this job."
                run.finished_at = datetime.now(timezone.utc)
                run.duration_seconds = 0
                run.prune_status = PruneStatus.skipped
                run.check_status = CheckStatus.skipped
                await s.commit()
            return

        # Load settings once for use throughout
        settings_dict: Dict[str, Any] = {}
        async with factory() as s:
            settings_obj = await s.get(AppSettings, 1)
            if settings_obj:
                settings_dict = {
                    "ntfy_server_url": settings_obj.ntfy_server_url,
                    "ntfy_topic": settings_obj.ntfy_topic,
                    "ntfy_token": settings_obj.ntfy_token,
                    "notify_on_start": settings_obj.notify_on_start,
                    "notify_on_success": settings_obj.notify_on_success,
                    "notify_on_failure": settings_obj.notify_on_failure,
                    "notify_on_verification": settings_obj.notify_on_verification,
                    "default_job_timeout_hours": settings_obj.default_job_timeout_hours,
                }

        # Step 3: Start notification
        if settings_dict.get("notify_on_start") and settings_dict.get("ntfy_topic"):
            logger.info(f"step=start_notification job_id={job_id}")
            await send_notification(
                settings_dict.get("ntfy_server_url"),
                settings_dict.get("ntfy_topic"),
                f"Starting backup: {job.name}",
                f"Source: {job.source_label}, Destination: {job.destination_label}",
                token=settings_dict.get("ntfy_token"),
            )

        # Build repo and source paths
        repo_path = f"/destinations/{job.destination_label}/{str(job_id)}"
        source_path = f"/sources/{job.source_label}"
        if job.source_subpath:
            source_path = f"{source_path}/{job.source_subpath}"

        # Step 4: Init check
        logger.debug(
            f"job_id={job_id} run_id={run_id} step=init_check repo_path={repo_path}"
        )
        rc, _, stderr = await restic.restic_cat_config(repo_path, job.restic_password)
        if rc != 0:
            if "wrong password" in stderr.lower():
                logger.error(
                    f"job_id={job_id} run_id={run_id} step=init_check "
                    f"error=wrong_password"
                )
                async with factory() as s:
                    run = await s.get(BackupRun, str(run_id))
                    run.status = RunStatus.failed
                    run.error_output = stderr
                    run.finished_at = datetime.now(timezone.utc)
                    run.duration_seconds = 0
                    run.prune_status = PruneStatus.skipped
                    run.check_status = CheckStatus.skipped
                    await s.commit()
                return

            # Try to init
            logger.info(
                f"job_id={job_id} run_id={run_id} step=init_check "
                f"repo_not_found initializing"
            )
            rc, _, init_stderr = await restic.restic_init(
                repo_path, job.restic_password
            )
            if rc != 0:
                logger.error(
                    f"job_id={job_id} run_id={run_id} step=init_check error=init_failed"
                )
                async with factory() as s:
                    run = await s.get(BackupRun, str(run_id))
                    run.status = RunStatus.failed
                    run.error_output = init_stderr
                    run.finished_at = datetime.now(timezone.utc)
                    run.duration_seconds = 0
                    run.prune_status = PruneStatus.skipped
                    run.check_status = CheckStatus.skipped
                    await s.commit()
                return
            logger.info(
                f"job_id={job_id} run_id={run_id} step=init_check repo_initialized"
            )

        # Step 5: Backup
        timeout_seconds = (
            job.timeout_hours or settings_dict.get("default_job_timeout_hours", 24)
        ) * 3600
        logger.info(
            f"job_id={job_id} run_id={run_id} step=backup_execution "
            f"source_path={source_path} timeout_seconds={timeout_seconds}"
        )
        backup_kwargs: Dict[str, Any] = {
            k: getattr(job, k)
            for k in [
                "exclude_patterns",
                "exclude_caches",
                "exclude_if_present",
                "one_file_system",
                "no_scan",
                "tags",
                "compression",
                "pack_size",
                "read_concurrency",
            ]
            if getattr(job, k) is not None
        }

        backup_success: bool = False
        summary: Optional[Dict[str, Any]] = None
        stdout: str = ""

        try:
            rc, stdout, stderr, summary = await restic.restic_backup(
                repo_path,
                job.restic_password,
                source_path,
                timeout_seconds,
                **backup_kwargs,
            )
            if rc == 0:
                backup_success = True
                logger.info(
                    f"job_id={job_id} run_id={run_id} step=backup_execution "
                    f"status=success"
                )
            else:
                logger.error(
                    f"job_id={job_id} run_id={run_id} step=backup_execution "
                    f"status=failed rc={rc}"
                )
                async with factory() as s:
                    run = await s.get(BackupRun, str(run_id))
                    run.status = RunStatus.failed
                    run.error_output = stderr
                    await s.commit()
        except asyncio.TimeoutError:
            hours = job.timeout_hours or settings_dict.get(
                "default_job_timeout_hours", 24
            )
            logger.error(
                f"job_id={job_id} run_id={run_id} step=backup_execution "
                f"error=timeout timeout_hours={hours}"
            )
            async with factory() as s:
                run = await s.get(BackupRun, str(run_id))
                run.status = RunStatus.failed
                run.error_output = f"Backup timed out after {hours} hours"
                await s.commit()

        # Step 6 & 7: Parse output and update stats (only if backup succeeded)
        if backup_success:
            async with factory() as s:
                run = await s.get(BackupRun, str(run_id))
                if summary:
                    run.files_new = summary.get("files_new")
                    run.files_changed = summary.get("files_changed")
                    run.files_unmodified = summary.get("files_unmodified")
                    run.dirs_new = summary.get("dirs_new")
                    run.dirs_changed = summary.get("dirs_changed")
                    run.dirs_unmodified = summary.get("dirs_unmodified")
                    run.data_added_bytes = summary.get("data_added")
                    run.data_added_packed_bytes = summary.get("data_added_packed")
                    run.total_bytes_processed = summary.get("total_bytes_processed")
                    run.snapshot_id = summary.get("snapshot_id")
                run.backup_output = stdout
                await s.commit()

            # Step 8: Prune (only if backup succeeded)
            logger.debug(f"job_id={job_id} run_id={run_id} step=prune")
            retention_kwargs: Dict[str, Any] = {
                k: getattr(job, k)
                for k in [
                    "retain_keep_last",
                    "retain_keep_hourly",
                    "retain_keep_daily",
                    "retain_keep_weekly",
                    "retain_keep_monthly",
                    "retain_keep_yearly",
                    "retain_keep_within",
                    "retain_keep_within_hourly",
                    "retain_keep_within_daily",
                    "retain_keep_within_weekly",
                    "retain_keep_within_monthly",
                    "retain_keep_within_yearly",
                ]
                if getattr(job, k) is not None
            }

            if retention_kwargs:
                logger.info(
                    f"job_id={job_id} run_id={run_id} step=prune mode=forget_prune"
                )
                rc, _, prune_err = await restic.restic_forget_prune(
                    repo_path,
                    job.restic_password,
                    timeout_seconds,
                    **retention_kwargs,
                )
            else:
                logger.info(
                    f"job_id={job_id} run_id={run_id} step=prune mode=standard_prune"
                )
                rc, _, prune_err = await restic.restic_prune(
                    repo_path, job.restic_password, timeout_seconds
                )

            async with factory() as s:
                run = await s.get(BackupRun, str(run_id))
                if rc == 0:
                    run.prune_status = PruneStatus.passed
                    logger.info(
                        f"job_id={job_id} run_id={run_id} step=prune status=passed"
                    )
                else:
                    run.prune_status = PruneStatus.failed
                    run.prune_error_output = prune_err
                    logger.warning(
                        f"job_id={job_id} run_id={run_id} step=prune "
                        f"status=failed error=pruning_failed"
                    )
                await s.commit()

            # Step 9: Reconcile snapshots
            rc, snapshots, _ = await restic.restic_snapshots(
                repo_path, job.restic_password
            )
            run_finished_at: datetime = datetime.now(timezone.utc)
            if rc == 0:
                async with factory() as s:
                    result = await s.execute(
                        select(Snapshot).where(Snapshot.job_id == str(job_id))
                    )
                    existing_snaps: list[Any] = result.scalars().all()
                    snap_ids: Set[Any] = {snap["id"] for snap in snapshots}

                    # Delete pruned snapshots
                    for snap in existing_snaps:
                        if snap.snapshot_id not in snap_ids:
                            await s.delete(snap)

                    # Upsert snapshots
                    for snap in snapshots:
                        result = await s.execute(
                            select(Snapshot).where(Snapshot.snapshot_id == snap["id"])
                        )
                        existing = result.scalars().first()

                        snap_time_str: Optional[str] = snap.get("time")
                        snap_time: Optional[datetime] = None
                        if snap_time_str:
                            snap_time = datetime.fromisoformat(
                                snap_time_str.replace("Z", "+00:00")
                            )

                        if existing:
                            existing.snapshot_time = snap_time
                            existing.hostname = snap.get("hostname")
                            existing.paths = snap.get("paths")
                            existing.tags = snap.get("tags")
                            existing.size_bytes = snap.get("total_size")
                        else:
                            new_snap = Snapshot(
                                id=str(uuid.uuid4()),
                                job_id=str(job_id),
                                snapshot_id=snap["id"],
                                snapshot_time=snap_time,
                                hostname=snap.get("hostname"),
                                paths=snap.get("paths"),
                                tags=snap.get("tags"),
                                size_bytes=snap.get("total_size"),
                                captured_at=run_finished_at,
                            )
                            if snap["id"] == summary.get("snapshot_id"):
                                new_snap.run_id = str(run_id)
                            s.add(new_snap)

                    await s.commit()
        else:
            # If backup failed, skip steps 8-9 and mark them as skipped
            async with factory() as s:
                run = await s.get(BackupRun, str(run_id))
                if run.prune_status is None:
                    run.prune_status = PruneStatus.skipped
                if run.check_status is None:
                    run.check_status = CheckStatus.skipped
                await s.commit()
            run_finished_at = datetime.now(timezone.utc)

        # Step 10: Finalize run
        now: datetime = run_finished_at
        async with factory() as s:
            run = await s.get(BackupRun, str(run_id))
            if run.status == RunStatus.running:
                run.status = RunStatus.success
            run.finished_at = now
            now_naive: datetime = now.replace(tzinfo=None)
            run.duration_seconds = int((now_naive - run.started_at).total_seconds())
            if run.check_status is None:
                run.check_status = CheckStatus.skipped
            await s.commit()

        # Step 11: Completion notification
        if settings_dict.get("ntfy_topic"):
            if run.status == RunStatus.success and settings_dict.get(
                "notify_on_success"
            ):
                msg = f"Duration: {run.duration_seconds}s, Files: {run.files_changed}"
                await send_notification(
                    settings_dict.get("ntfy_server_url"),
                    settings_dict.get("ntfy_topic"),
                    f"Backup succeeded: {job.name}",
                    msg,
                    token=settings_dict.get("ntfy_token"),
                )
            elif run.status == RunStatus.failed and settings_dict.get(
                "notify_on_failure"
            ):
                error_excerpt: str = (
                    (run.error_output or "")[:200]
                    if run.error_output
                    else "Unknown error"
                )
                await send_notification(
                    settings_dict.get("ntfy_server_url"),
                    settings_dict.get("ntfy_topic"),
                    f"Backup failed: {job.name}",
                    error_excerpt,
                    token=settings_dict.get("ntfy_token"),
                )

        # Step 12: Integrity check
        if job.check_enabled and run.status == RunStatus.success:
            logger.info(
                f"job_id={job_id} run_id={run_id} step=integrity_check "
                f"mode={job.check_mode.value} enabled=true"
            )
            if settings_dict.get("notify_on_verification"):
                await send_notification(
                    settings_dict.get("ntfy_server_url"),
                    settings_dict.get("ntfy_topic"),
                    f"Verification started: {job.name}",
                    "Running integrity check...",
                    token=settings_dict.get("ntfy_token"),
                )

            check_timeout = (
                job.check_timeout_hours
                or settings_dict.get("default_job_timeout_hours", 24)
            ) * 3600
            rc, _, check_err = await restic.restic_check(
                repo_path,
                job.restic_password,
                job.check_mode.value,
                job.check_subset_percent,
                check_timeout,
            )

            async with factory() as s:
                run = await s.get(BackupRun, str(run_id))
                if rc == 0:
                    run.check_status = CheckStatus.passed
                    logger.info(
                        f"job_id={job_id} run_id={run_id} step=integrity_check "
                        f"status=passed"
                    )
                else:
                    run.check_status = CheckStatus.failed
                    run.check_error_output = check_err
                    logger.warning(
                        f"job_id={job_id} run_id={run_id} step=integrity_check "
                        f"status=failed error=check_failed"
                    )
                await s.commit()

            if settings_dict.get("notify_on_verification"):
                status_str: str = "passed" if rc == 0 else "failed"
                await send_notification(
                    settings_dict.get("ntfy_server_url"),
                    settings_dict.get("ntfy_topic"),
                    f"Verification {status_str}: {job.name}",
                    f"Check status: {status_str}",
                    token=settings_dict.get("ntfy_token"),
                )
        else:
            if not job.check_enabled:
                logger.debug(
                    f"job_id={job_id} run_id={run_id} step=integrity_check "
                    f"enabled=false skipped"
                )

    finally:
        # Cleanup: remove job from active set
        _active_jobs.discard(job_id)
        logger.info(f"job_id={job_id} run_id={run_id} backup_completed")

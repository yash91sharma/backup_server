"""Test database migrations."""

import tempfile
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy import inspect

from alembic import command


@pytest.mark.asyncio
async def test_migration_creates_all_tables():
    """Verify migration creates all tables and columns."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db_url = f"sqlite:///{db_path}"

        cfg = Config(Path(__file__).parent.parent / "alembic.ini")
        cfg.set_main_option("sqlalchemy.url", db_url)
        command.upgrade(cfg, "head")

        engine = sa.create_engine(db_url, echo=False)
        inspector = inspect(engine)

        # Verify tables exist
        tables = set(inspector.get_table_names())
        expected_tables = {"backup_jobs", "backup_runs", "snapshots", "app_settings"}
        assert expected_tables.issubset(tables)

        # Verify columns for each table
        job_cols = {col["name"] for col in inspector.get_columns("backup_jobs")}
        expected_job = {
            "id",
            "name",
            "source_label",
            "source_subpath",
            "destination_label",
            "restic_password",
            "schedule_type",
            "schedule_value",
            "enabled",
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
            "exclude_patterns",
            "exclude_caches",
            "exclude_if_present",
            "one_file_system",
            "no_scan",
            "tags",
            "compression",
            "pack_size",
            "read_concurrency",
            "timeout_hours",
            "check_enabled",
            "check_mode",
            "check_subset_percent",
            "check_timeout_hours",
            "created_at",
            "updated_at",
        }
        assert expected_job.issubset(job_cols)

        run_cols = {col["name"] for col in inspector.get_columns("backup_runs")}
        expected_run = {
            "id",
            "job_id",
            "status",
            "reason",
            "started_at",
            "finished_at",
            "duration_seconds",
            "snapshot_id",
            "files_new",
            "files_changed",
            "files_unmodified",
            "dirs_new",
            "dirs_changed",
            "dirs_unmodified",
            "data_added_bytes",
            "data_added_packed_bytes",
            "total_bytes_processed",
            "backup_output",
            "error_output",
            "prune_status",
            "prune_error_output",
            "check_status",
            "check_error_output",
            "triggered_by",
        }
        assert expected_run.issubset(run_cols)

        snap_cols = {col["name"] for col in inspector.get_columns("snapshots")}
        expected_snap = {
            "id",
            "job_id",
            "run_id",
            "snapshot_id",
            "snapshot_time",
            "hostname",
            "paths",
            "tags",
            "size_bytes",
            "captured_at",
        }
        assert expected_snap.issubset(snap_cols)

        settings_cols = {col["name"] for col in inspector.get_columns("app_settings")}
        expected_settings = {
            "id",
            "ntfy_server_url",
            "ntfy_topic",
            "ntfy_token",
            "notify_on_start",
            "notify_on_success",
            "notify_on_failure",
            "notify_on_verification",
            "restic_version",
            "default_job_timeout_hours",
        }
        assert expected_settings.issubset(settings_cols)

        # Verify foreign keys
        assert any(
            fk["constrained_columns"] == ["job_id"]
            for fk in inspector.get_foreign_keys("backup_runs")
        ), "Missing FK: backup_runs.job_id"

        assert any(
            fk["constrained_columns"] == ["job_id"]
            for fk in inspector.get_foreign_keys("snapshots")
        ), "Missing FK: snapshots.job_id"

        assert any(
            fk["constrained_columns"] == ["run_id"]
            for fk in inspector.get_foreign_keys("snapshots")
        ), "Missing FK: snapshots.run_id"

        engine.dispose()

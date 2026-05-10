import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class ScheduleType(str, Enum):
    cron = "cron"
    interval = "interval"


class RunStatus(str, Enum):
    running = "running"
    success = "success"
    failed = "failed"
    skipped = "skipped"


class RunReason(str, Enum):
    overlapping_run = "overlapping_run"
    container_restart = "container_restart"


class TriggeredBy(str, Enum):
    scheduler = "scheduler"
    manual = "manual"


class PruneStatus(str, Enum):
    passed = "passed"
    failed = "failed"
    skipped = "skipped"


class CheckStatus(str, Enum):
    passed = "passed"
    failed = "failed"
    skipped = "skipped"


class CheckMode(str, Enum):
    structural = "structural"
    subset = "subset"
    full = "full"


class CompressionMode(str, Enum):
    auto = "auto"
    max = "max"
    off = "off"


class BackupJob(Base):
    __tablename__ = "backup_jobs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(128), nullable=False)
    source_label = Column(String(64), nullable=False)
    source_subpath = Column(String(255), nullable=True)
    destination_label = Column(String(64), nullable=False)
    restic_password = Column(String, nullable=False)
    schedule_type = Column(SAEnum(ScheduleType), nullable=False)
    schedule_value = Column(String, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)

    retain_keep_last = Column(Integer, nullable=True)
    retain_keep_hourly = Column(Integer, nullable=True)
    retain_keep_daily = Column(Integer, nullable=True)
    retain_keep_weekly = Column(Integer, nullable=True)
    retain_keep_monthly = Column(Integer, nullable=True)
    retain_keep_yearly = Column(Integer, nullable=True)
    retain_keep_within = Column(String, nullable=True)
    retain_keep_within_hourly = Column(String, nullable=True)
    retain_keep_within_daily = Column(String, nullable=True)
    retain_keep_within_weekly = Column(String, nullable=True)
    retain_keep_within_monthly = Column(String, nullable=True)
    retain_keep_within_yearly = Column(String, nullable=True)

    exclude_patterns = Column(JSON, nullable=True)
    exclude_caches = Column(Boolean, nullable=False, default=False)
    exclude_if_present = Column(JSON, nullable=True)
    one_file_system = Column(Boolean, nullable=False, default=False)
    no_scan = Column(Boolean, nullable=False, default=False)
    tags = Column(JSON, nullable=True)
    compression = Column(SAEnum(CompressionMode), nullable=True)
    pack_size = Column(Integer, nullable=True)
    read_concurrency = Column(Integer, nullable=True)
    timeout_hours = Column(Integer, nullable=True)

    check_enabled = Column(Boolean, nullable=False, default=False)
    check_mode = Column(SAEnum(CheckMode), nullable=True)
    check_subset_percent = Column(Integer, nullable=True)
    check_timeout_hours = Column(Integer, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    runs = relationship("BackupRun", back_populates="job", cascade="all, delete-orphan")
    snapshots = relationship(
        "Snapshot", back_populates="job", cascade="all, delete-orphan"
    )


class BackupRun(Base):
    __tablename__ = "backup_runs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id = Column(
        String(36), ForeignKey("backup_jobs.id", ondelete="CASCADE"), nullable=False
    )
    status = Column(SAEnum(RunStatus), nullable=False)
    reason = Column(SAEnum(RunReason), nullable=True)
    started_at = Column(DateTime, nullable=False)
    finished_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    snapshot_id = Column(String(64), nullable=True)
    files_new = Column(Integer, nullable=True)
    files_changed = Column(Integer, nullable=True)
    files_unmodified = Column(Integer, nullable=True)
    dirs_new = Column(Integer, nullable=True)
    dirs_changed = Column(Integer, nullable=True)
    dirs_unmodified = Column(Integer, nullable=True)
    data_added_bytes = Column(BigInteger, nullable=True)
    data_added_packed_bytes = Column(BigInteger, nullable=True)
    total_bytes_processed = Column(BigInteger, nullable=True)
    backup_output = Column(Text, nullable=True)
    error_output = Column(Text, nullable=True)
    prune_status = Column(SAEnum(PruneStatus), nullable=True)
    prune_error_output = Column(Text, nullable=True)
    check_status = Column(SAEnum(CheckStatus), nullable=True)
    check_error_output = Column(Text, nullable=True)
    triggered_by = Column(SAEnum(TriggeredBy), nullable=False)

    job = relationship("BackupJob", back_populates="runs")
    snapshots = relationship("Snapshot", back_populates="run")


class Snapshot(Base):
    __tablename__ = "snapshots"
    __table_args__ = (UniqueConstraint("job_id", "snapshot_id"),)

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id = Column(
        String(36), ForeignKey("backup_jobs.id", ondelete="CASCADE"), nullable=False
    )
    run_id = Column(String(36), ForeignKey("backup_runs.id"), nullable=True)
    snapshot_id = Column(String(64), nullable=False)
    snapshot_time = Column(DateTime, nullable=False)
    hostname = Column(String, nullable=False)
    paths = Column(JSON, nullable=False)
    tags = Column(JSON, nullable=True)
    size_bytes = Column(BigInteger, nullable=True)
    captured_at = Column(DateTime, nullable=False)

    job = relationship("BackupJob", back_populates="snapshots")
    run = relationship("BackupRun", back_populates="snapshots")


class AppSettings(Base):
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True, default=1)
    ntfy_server_url = Column(String(512), nullable=False, default="https://ntfy.sh")
    ntfy_topic = Column(String(64), nullable=False, default="")
    ntfy_token = Column(String(512), nullable=True)
    notify_on_start = Column(Boolean, nullable=False, default=True)
    notify_on_success = Column(Boolean, nullable=False, default=True)
    notify_on_failure = Column(Boolean, nullable=False, default=True)
    notify_on_verification = Column(Boolean, nullable=False, default=True)
    restic_version = Column(String, nullable=True)
    default_job_timeout_hours = Column(Integer, nullable=False, default=24)

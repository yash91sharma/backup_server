import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def engine():
    from app.db.models import Base

    eng = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db_session(engine) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(engine) -> AsyncGenerator[AsyncClient, None]:
    from app.api.deps import get_session
    from app.main import app

    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_session():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
def mock_scheduler():
    m = MagicMock()
    m.running = True
    m.add_job = MagicMock()
    m.remove_job = MagicMock()
    m.get_job = MagicMock(return_value=None)
    m.get_jobs = MagicMock(return_value=[])
    return m


# ── helpers ──────────────────────────────────────────────────────────────────


def make_job_payload(**overrides) -> dict:
    base = {
        "name": "Test Backup",
        "source_label": "documents",
        "destination_label": "main",
        "restic_password": "secret123",
        "schedule_type": "interval",
        "schedule_value": "6h",
        "enabled": True,
    }
    base.update(overrides)
    return base


def make_run_row(db_session, job_id: str, **overrides):
    """Insert a BackupRun row directly into the test DB."""
    from app.db.models import BackupRun, RunStatus, TriggeredBy

    now = datetime.now(timezone.utc)
    run = BackupRun(
        id=str(uuid.uuid4()),
        job_id=job_id,
        status=overrides.pop("status", RunStatus.success),
        triggered_by=overrides.pop("triggered_by", TriggeredBy.manual),
        started_at=overrides.pop("started_at", now),
        finished_at=overrides.pop("finished_at", now),
        **overrides,
    )
    db_session.add(run)
    return run


def make_snapshot_row(db_session, job_id: str, **overrides):
    from app.db.models import Snapshot

    now = datetime.now(timezone.utc)
    snap = Snapshot(
        id=str(uuid.uuid4()),
        job_id=job_id,
        snapshot_id=overrides.pop("snapshot_id", "a" * 64),
        snapshot_time=overrides.pop("snapshot_time", now),
        hostname=overrides.pop("hostname", "testhost"),
        paths=overrides.pop("paths", ["/sources/documents"]),
        captured_at=overrides.pop("captured_at", now),
        **overrides,
    )
    db_session.add(snap)
    return snap

"""FastAPI routes for BackupRun history and detail."""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.api.schemas.jobs import RunSummarySchema
from app.api.schemas.runs import RunDetailSchema
from app.core.logging import log_call
from app.db.models import BackupJob, BackupRun

router = APIRouter(prefix="/runs", tags=["runs"])


# ── GET /api/runs/recent ──────────────────────────────────────────────────────
# Must be defined BEFORE /{id} to prevent FastAPI from treating "recent" as an
# id path parameter.


@router.get("/recent", response_model=List[RunSummarySchema])
@log_call
async def recent_runs(
    limit: int = Query(10, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> List[RunSummarySchema]:
    """Return the most recent runs across all jobs, newest first.

    Each entry includes job_name (joined from BackupJob).
    Output fields (backup_output, error_output, …) are excluded.
    """
    result = await session.execute(
        select(BackupRun, BackupJob.name.label("job_name"))
        .join(BackupJob, BackupRun.job_id == BackupJob.id)
        .order_by(BackupRun.started_at.desc())
        .limit(limit)
    )
    rows = result.all()

    response: list[RunSummarySchema] = []
    for run, job_name in rows:
        data = RunSummarySchema.model_validate(run)
        data.job_name = job_name
        response.append(data)

    return response


# ── GET /api/runs/{id} ────────────────────────────────────────────────────────


@router.get("/{run_id}", response_model=RunDetailSchema)
@log_call
async def get_run(
    run_id: str, session: AsyncSession = Depends(get_session)
) -> BackupRun:
    """Return a single run record with all output fields included."""
    run = await session.get(BackupRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Not found")
    return run

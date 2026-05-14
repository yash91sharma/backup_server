"""Pydantic schemas for BackupRun responses."""

from typing import Optional

from app.api.schemas.jobs import RunSummarySchema


class RunDetailSchema(RunSummarySchema):
    """Full run record including all subprocess output fields.

    Used only by GET /api/runs/{id}; list endpoints use the lighter
    RunSummarySchema which omits the potentially-large output strings.
    """

    backup_output: Optional[str] = None
    error_output: Optional[str] = None
    prune_error_output: Optional[str] = None
    check_error_output: Optional[str] = None

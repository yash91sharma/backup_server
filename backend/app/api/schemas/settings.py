"""Pydantic schemas for AppSettings and health/utility endpoints."""

import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator

_TOPIC_RE = re.compile(r"^[a-zA-Z0-9_-]{0,64}$")
_MAX_URL_LENGTH = 512


class SettingsUpdate(BaseModel):
    """Fields accepted when updating AppSettings via PUT /api/settings."""

    ntfy_server_url: str = Field(max_length=_MAX_URL_LENGTH)
    ntfy_topic: str = Field(default="", max_length=64)
    ntfy_token: Optional[str] = None
    notify_on_start: bool = True
    notify_on_success: bool = True
    notify_on_failure: bool = True
    notify_on_verification: bool = True
    default_job_timeout_hours: int = Field(24, ge=1, le=168)

    @field_validator("ntfy_server_url")
    @classmethod
    def validate_ntfy_url(cls, v: str) -> str:
        """Reject any URL that does not use http or https."""
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("ntfy_server_url must start with http:// or https://")
        return v

    @field_validator("ntfy_topic")
    @classmethod
    def validate_ntfy_topic(cls, v: str) -> str:
        """Allow only alphanumeric chars, hyphens, and underscores (or empty)."""
        if v and not _TOPIC_RE.match(v):
            raise ValueError(
                "ntfy_topic may only contain letters, digits, '-', and '_'"
            )
        return v


class SettingsResponse(SettingsUpdate):
    """Full settings record returned by GET/PUT /api/settings.

    ntfy_token is always returned as None — it is stored but never exposed.
    """

    id: int
    restic_version: Optional[str] = None
    ntfy_token: None = None  # type: ignore[assignment]


class HealthResponse(BaseModel):
    """Response shape for GET /api/health."""

    scheduler_running: bool
    restic_version: Optional[str]
    db_ok: bool


class NtfyTestResult(BaseModel):
    """Response shape for POST /api/settings/test-ntfy."""

    ok: bool
    error: Optional[str] = None


class ResticUpdateCheck(BaseModel):
    """Response shape for GET /api/settings/restic-update-check."""

    # Currently installed version (from AppSettings), or None if not detected.
    current: Optional[str]
    # Latest version from GitHub releases API, or None if the check failed.
    latest: Optional[str]
    # True when latest > current; None when comparison is not possible.
    update_available: Optional[bool]

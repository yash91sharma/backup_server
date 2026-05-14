"""Central logging infrastructure.

All modules must obtain loggers via get_logger() and annotate non-trivial
functions with @log_call.  Sensitive fields are redacted automatically.
"""

import functools
import logging
import os
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

# Fields whose values are replaced with "***" in any logged mapping.
SENSITIVE_FIELDS = {"restic_password", "ntfy_token"}


def sanitize(data: Any) -> Any:
    """Recursively redact sensitive keys from dicts; leave other types unchanged."""
    if isinstance(data, dict):
        return {
            k: "***" if k in SENSITIVE_FIELDS else sanitize(v) for k, v in data.items()
        }
    if isinstance(data, (list, tuple)):
        return type(data)(sanitize(item) for item in data)
    return data


def get_logger(name: str) -> logging.Logger:
    """Return a named logger for the given module."""
    return logging.getLogger(name)


def log_call(fn):
    """Decorator that logs entry, exit, and exceptions for any function.

    Works for both sync and async functions.  Sanitizes all logged args/kwargs
    so that sensitive field values are never written to logs.
    """
    logger = get_logger(fn.__module__)

    if not __import__("asyncio").iscoroutinefunction(fn):

        @functools.wraps(fn)
        def sync_wrapper(*args, **kwargs):
            logger.debug(
                "%s called args=%s kwargs=%s",
                fn.__name__,
                sanitize(args),
                sanitize(kwargs),
            )
            try:
                result = fn(*args, **kwargs)
                logger.debug("%s returned %s", fn.__name__, sanitize(result))
                return result
            except Exception as exc:
                logger.exception("%s raised %s", fn.__name__, exc)
                raise

        return sync_wrapper

    @functools.wraps(fn)
    async def async_wrapper(*args, **kwargs):
        logger.debug(
            "%s called args=%s kwargs=%s", fn.__name__, sanitize(args), sanitize(kwargs)
        )
        try:
            result = await fn(*args, **kwargs)
            logger.debug("%s returned %s", fn.__name__, sanitize(result))
            return result
        except Exception as exc:
            logger.exception("%s raised %s", fn.__name__, exc)
            raise

    return async_wrapper


def setup_logging() -> None:
    """Configure root logger from LOG_LEVEL env var (default INFO).

    Called once from the FastAPI lifespan handler.
    """
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every HTTP request with method, path, and response status."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        logger = get_logger(__name__)
        logger.info(
            "%s %s → %s", request.method, request.url.path, response.status_code
        )
        return response

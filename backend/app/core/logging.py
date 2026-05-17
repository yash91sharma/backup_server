"""Central logging infrastructure.

All modules must obtain loggers via get_logger() and annotate non-trivial
functions with @log_call.  Sensitive fields are redacted automatically.
"""

import functools
import inspect
import logging
import os
from typing import Awaitable, Callable, Dict, List, Set, Tuple, TypeVar, cast, overload

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

F = TypeVar("F", bound=Callable[..., object])

# Fields whose values are replaced with "***" in any logged mapping.
SENSITIVE_FIELDS: Set[str] = {"restic_password", "ntfy_token"}


@overload
def sanitize(data: Dict[str, object]) -> Dict[str, object]: ...


@overload
def sanitize(data: List[object]) -> List[object]: ...


@overload
def sanitize(data: Tuple[object, ...]) -> Tuple[object, ...]: ...


@overload
def sanitize(data: object) -> object: ...


def sanitize(data: object) -> object:
    """Recursively redact sensitive keys from dicts; leave other types unchanged."""
    if isinstance(data, dict):
        dict_data: Dict[str, object] = cast(Dict[str, object], data)
        sanitized_items: List[Tuple[str, object]] = [
            (k, "***" if k in SENSITIVE_FIELDS else sanitize(v))
            for k, v in dict_data.items()
        ]
        sanitized_dict: Dict[str, object] = dict(sanitized_items)
        return sanitized_dict
    if isinstance(data, list):
        list_data: List[object] = cast(List[object], data)
        sanitized_list: List[object] = [sanitize(item) for item in list_data]
        return sanitized_list
    if isinstance(data, tuple):
        tuple_data: Tuple[object, ...] = cast(Tuple[object, ...], data)
        sanitized_list: List[object] = [sanitize(item) for item in tuple_data]
        sanitized_tuple: Tuple[object, ...] = tuple(sanitized_list)
        return sanitized_tuple
    return data


def get_logger(name: str) -> logging.Logger:
    """Return a named logger for the given module."""
    return logging.getLogger(name)


def log_call(fn: F) -> F:
    """Decorator that logs entry, exit, and exceptions for any function.

    Works for both sync and async functions.  Sanitizes all logged args/kwargs
    so that sensitive field values are never written to logs.
    """
    logger: logging.Logger = get_logger(fn.__module__)

    if not inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        def sync_wrapper(*args: object, **kwargs: object) -> object:
            sanitized_args: object = sanitize(args)
            sanitized_kwargs: object = sanitize(kwargs)
            logger.debug(
                "%s called args=%s kwargs=%s",
                fn.__name__,
                sanitized_args,
                sanitized_kwargs,
            )
            try:
                result: object = fn(*args, **kwargs)
                sanitized_result: object = sanitize(result)
                logger.debug("%s returned %s", fn.__name__, sanitized_result)
                return result
            except Exception as exc:
                logger.exception("%s raised %s", fn.__name__, exc)
                raise

        return cast(F, sync_wrapper)

    @functools.wraps(fn)
    async def async_wrapper(*args: object, **kwargs: object) -> object:
        sanitized_args: object = sanitize(args)
        sanitized_kwargs: object = sanitize(kwargs)
        logger.debug(
            "%s called args=%s kwargs=%s", fn.__name__, sanitized_args, sanitized_kwargs
        )
        try:
            result: object = await fn(*args, **kwargs)
            sanitized_result: object = sanitize(result)
            logger.debug("%s returned %s", fn.__name__, sanitized_result)
            return result
        except Exception as exc:
            logger.exception("%s raised %s", fn.__name__, exc)
            raise

    return cast(F, async_wrapper)


def setup_logging() -> None:
    """Configure root logger from LOG_LEVEL env var (default INFO).

    Called once from the FastAPI lifespan handler.
    """
    level_str: str = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level_str,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every HTTP request with method, path, and response status."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response: Response = await call_next(request)
        logger: logging.Logger = get_logger(__name__)
        request_method: str = request.method
        request_path: str = request.url.path
        response_status: int = response.status_code
        logger.info("%s %s → %s", request_method, request_path, response_status)
        return response

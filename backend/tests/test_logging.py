"""Tests for app.core.logging — request ID traceability."""

import logging
import re

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.core.logging import (
    RequestIdFilter,
    RequestLoggingMiddleware,
    _request_id_var,
    get_logger,
    get_request_id,
    setup_logging,
)

# ── contextvar reset between tests ────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_request_id():
    token = _request_id_var.set(None)
    yield
    _request_id_var.reset(token)


# ── get_request_id ────────────────────────────────────────────────────────────


def test_get_request_id_returns_none_when_unset():
    assert get_request_id() is None


def test_get_request_id_returns_value_set_via_contextvar():
    _request_id_var.set("abc123def456")
    assert get_request_id() == "abc123def456"


# ── RequestIdFilter ──────────────────────────────────────────────────────────


def _make_record() -> logging.LogRecord:
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="hi",
        args=(),
        exc_info=None,
    )


def test_request_id_filter_injects_value_from_contextvar():
    _request_id_var.set("xyz789012abc")
    f = RequestIdFilter()
    record = _make_record()
    assert f.filter(record) is True
    assert record.request_id == "xyz789012abc"


def test_request_id_filter_defaults_to_none_string_when_unset():
    f = RequestIdFilter()
    record = _make_record()
    assert f.filter(record) is True
    assert record.request_id == "none"


# ── setup_logging format and propagation ─────────────────────────────────────


def test_setup_logging_format_contains_request_id_placeholder():
    setup_logging()
    handlers = logging.getLogger().handlers
    assert any(
        "%(request_id)s" in (h.formatter._fmt or "") for h in handlers if h.formatter
    )


def test_setup_logging_makes_request_id_available_on_records(caplog):
    setup_logging()
    _request_id_var.set("propagationid1")
    with caplog.at_level(logging.INFO):
        get_logger("test.propagation").info("hello")
    matching = [r for r in caplog.records if r.message == "hello"]
    assert matching
    assert matching[0].request_id == "propagationid1"


def test_setup_logging_default_request_id_is_none_string(caplog):
    setup_logging()
    with caplog.at_level(logging.INFO):
        get_logger("test.default").info("anonymous")
    matching = [r for r in caplog.records if r.message == "anonymous"]
    assert matching
    assert matching[0].request_id == "none"


# ── RequestLoggingMiddleware end-to-end ──────────────────────────────────────


def _make_app() -> tuple[Starlette, dict]:
    captured: dict = {}

    async def root(request):
        captured["id"] = get_request_id()
        get_logger("test.endpoint").info("endpoint hit")
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/", root)])
    app.add_middleware(RequestLoggingMiddleware)
    return app, captured


@pytest.mark.asyncio
async def test_middleware_sets_12_char_hex_request_id():
    app, captured = _make_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        resp = await ac.get("/")
    assert resp.status_code == 200
    rid = captured["id"]
    assert rid is not None
    assert re.fullmatch(r"[0-9a-f]{12}", rid)


@pytest.mark.asyncio
async def test_middleware_generates_distinct_ids_across_requests():
    app, captured = _make_app()
    seen: set[str] = set()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        for _ in range(3):
            await ac.get("/")
            seen.add(captured["id"])
    assert len(seen) == 3


@pytest.mark.asyncio
async def test_middleware_clears_request_id_after_response():
    app, _ = _make_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        await ac.get("/")
    assert get_request_id() is None


# ── Request traceability ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_all_logs_during_one_request_share_same_request_id(caplog):
    setup_logging()
    app, captured = _make_app()

    with caplog.at_level(logging.INFO):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            await ac.get("/")

    rid = captured["id"]
    request_records = [
        r for r in caplog.records if getattr(r, "request_id", None) == rid
    ]
    # At minimum: endpoint log + middleware's "GET / → 200" log
    assert len(request_records) >= 2
    for r in request_records:
        assert r.request_id == rid


@pytest.mark.asyncio
async def test_two_sequential_requests_have_isolated_request_ids(caplog):
    setup_logging()
    app, captured = _make_app()

    rids: list[str] = []
    with caplog.at_level(logging.INFO):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            await ac.get("/")
            rids.append(captured["id"])
            await ac.get("/")
            rids.append(captured["id"])

    assert rids[0] != rids[1]
    logged_rids = {getattr(r, "request_id", None) for r in caplog.records}
    assert rids[0] in logged_rids
    assert rids[1] in logged_rids


# ── Full-stack traceability via FastAPI ──────────────────────────────────────


@pytest.mark.asyncio
async def test_request_id_propagates_through_route_and_log_call(caplog, client):
    """Verify a real FastAPI route + @log_call helpers all share the same ID.

    Uses GET /api/jobs which exercises the @log_call-decorated route handler
    and helper functions defined in app.api.routes.jobs.
    """
    setup_logging()
    with caplog.at_level(logging.DEBUG):
        resp = await client.get("/api/jobs")
    assert resp.status_code == 200

    # Identify the middleware's request log to extract the ID
    middleware_records = [
        r
        for r in caplog.records
        if r.name == "app.core.logging" and "GET /api/jobs" in r.getMessage()
    ]
    assert middleware_records, "middleware did not log the request"
    rid = middleware_records[0].request_id
    assert re.fullmatch(r"[0-9a-f]{12}", rid)

    # Every record originating from app.api.routes.jobs during this request
    # must carry the same request_id.
    route_records = [r for r in caplog.records if r.name == "app.api.routes.jobs"]
    assert route_records, "no @log_call records from jobs route"
    for r in route_records:
        assert r.request_id == rid, (
            f"record {r.funcName!r} had request_id={r.request_id!r}, expected {rid!r}"
        )

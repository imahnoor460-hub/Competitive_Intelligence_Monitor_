"""Defensive error handling for unexpected failures — see app/core/errors.py.

These are NOT the performance fix for pool pressure. They only make the
failure legible: before this, an exception raised while resolving
get_current_user (pool exhaustion being the case in the field) unwound past
CORSMiddleware without producing a response, so the bare 500 that
ServerErrorMiddleware emitted carried no Access-Control-Allow-Origin and the
browser reported a CORS policy error rather than the 500 it actually was.
"""
import pytest
from sqlalchemy.exc import TimeoutError as SATimeoutError

from app.database import get_db
from app.main import app

# One of settings.cors_origins — the dev frontend origin.
ALLOWED_ORIGIN = "http://localhost:3000"
CORS_HEADER = "access-control-allow-origin"


def _register_and_login(client, email="alice@example.com"):
    client.post(
        "/auth/register",
        json={"email": email, "password": "supersecret1", "full_name": "Alice"},
    )
    login = client.post("/auth/login", json={"email": email, "password": "supersecret1"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _break_get_db(exc: Exception):
    """Replaces the request-scoped session with one that raises, reproducing a
    failure during dependency resolution rather than inside a route body."""

    def _raising():
        raise exc

    app.dependency_overrides[get_db] = _raising


@pytest.fixture()
def authed(client):
    # Log in *before* breaking get_db — the login itself needs a working one.
    headers = _register_and_login(client)
    headers["Origin"] = ALLOWED_ORIGIN
    return headers


def test_unexpected_exception_returns_json_500(client, authed):
    _break_get_db(RuntimeError("something nobody anticipated"))

    res = client.get("/workspaces/", headers=authed)

    assert res.status_code == 500
    assert res.json() == {"detail": "Internal server error"}
    # A consistent JSON body, not Starlette's bare text/plain "Internal Server
    # Error", so the frontend's apiFetch can read .detail like any other error.
    assert res.headers["content-type"].startswith("application/json")


def test_pool_timeout_returns_503_not_500(client, authed):
    _break_get_db(
        SATimeoutError(
            "QueuePool limit of size 5 overflow 10 reached, "
            "connection timed out, timeout 30.00"
        )
    )

    res = client.get("/workspaces/", headers=authed)

    # Transient capacity pressure is retryable, so it must not be reported as a
    # server fault.
    assert res.status_code == 503
    assert res.json() == {"detail": "Server busy, retry shortly"}


def test_unexpected_500_still_carries_cors_headers(client, authed):
    # The actual bug: without UnhandledErrorMiddleware sitting beneath
    # CORSMiddleware, this response has no ACAO header and the browser
    # surfaces it as a CORS policy error instead of a 500.
    _break_get_db(RuntimeError("something nobody anticipated"))

    res = client.get("/workspaces/", headers=authed)

    assert res.status_code == 500
    assert res.headers.get(CORS_HEADER) == ALLOWED_ORIGIN


def test_pool_timeout_503_still_carries_cors_headers(client, authed):
    _break_get_db(SATimeoutError("QueuePool limit of size 5 overflow 10 reached"))

    res = client.get("/workspaces/", headers=authed)

    assert res.status_code == 503
    assert res.headers.get(CORS_HEADER) == ALLOWED_ORIGIN


def test_successful_response_still_carries_cors_headers(client, authed):
    # Control: the error handling must not have disturbed the normal path.
    res = client.get("/workspaces/", headers=authed)

    assert res.status_code == 200
    assert res.headers.get(CORS_HEADER) == ALLOWED_ORIGIN


def test_preflight_still_answered(client):
    res = client.options(
        "/workspaces/",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "GET",
        },
    )

    assert res.status_code == 200
    assert res.headers.get(CORS_HEADER) == ALLOWED_ORIGIN


def test_disallowed_origin_gets_no_cors_headers_on_error(client, authed):
    # CORS policy is still CORS policy — error handling must not turn into a
    # blanket allow-origin.
    _break_get_db(RuntimeError("boom"))
    authed["Origin"] = "https://not-allowed.example.com"

    res = client.get("/workspaces/", headers=authed)

    assert res.status_code == 500
    assert res.headers.get(CORS_HEADER) is None


def test_cors_middleware_wraps_the_error_middleware():
    """Structural guard for the ordering the fix depends on.

    add_middleware inserts at index 0, so user_middleware[0] is outermost.
    CORSMiddleware must be outside UnhandledErrorMiddleware; swapping the two
    add_middleware calls in main.py silently reintroduces the header-less 500.
    """
    from fastapi.middleware.cors import CORSMiddleware
    from app.core.errors import UnhandledErrorMiddleware

    classes = [m.cls for m in app.user_middleware]

    assert CORSMiddleware in classes and UnhandledErrorMiddleware in classes
    assert classes.index(CORSMiddleware) < classes.index(UnhandledErrorMiddleware)

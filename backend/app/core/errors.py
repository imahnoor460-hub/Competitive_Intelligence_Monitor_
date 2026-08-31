import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

__all__ = [
    "UnhandledErrorMiddleware",
    "pool_timeout_handler",
    "unhandled_exception_handler",
    "INTERNAL_ERROR_BODY",
    "POOL_EXHAUSTED_BODY",
]

logger = logging.getLogger(__name__)

# Deliberately generic: a raw traceback or a QueuePool message is internal
# detail, and this API is called straight from a browser. The real cause goes
# to the logs instead.
INTERNAL_ERROR_BODY = {"detail": "Internal server error"}
POOL_EXHAUSTED_BODY = {"detail": "Server busy, retry shortly"}


async def pool_timeout_handler(request: Request, exc: Exception) -> JSONResponse:
    """SQLAlchemy could not hand out a pooled connection within pool_timeout.

    That is transient capacity pressure rather than a server fault, so it
    answers 503 — the status clients and proxies already read as "retry
    shortly" — instead of a blanket 500. Logged at warning, not exception: the
    traceback is the same QueuePool timeout every time and says nothing about
    the cause.

    This is registered for a specific exception type, so Starlette puts it on
    ExceptionMiddleware, which sits *below* CORSMiddleware in the stack. The
    response therefore travels back out through CORS and keeps its headers.
    """

    logger.warning("Connection pool exhausted on %s: %s", request.url.path, exc)

    return JSONResponse(status_code=503, content=POOL_EXHAUSTED_BODY)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Backstop so an unexpected error still returns a consistent JSON body.

    Starlette routes an Exception/500 handler onto ServerErrorMiddleware, which
    is the *outermost* middleware — above CORSMiddleware — and writes its
    response with the outer `send`. So this handler on its own cannot restore
    CORS headers; UnhandledErrorMiddleware is what does that, and this only
    catches whatever escapes above it.
    """

    logger.exception("Unhandled error on %s", request.url.path)

    return JSONResponse(status_code=500, content=INTERNAL_ERROR_BODY)


class UnhandledErrorMiddleware:
    """Turns an unhandled exception into a JSON 500 from *inside* the CORS
    middleware.

    Without this, an exception escaping a route — or a dependency, which is the
    case this was written for: pool exhaustion while resolving get_current_user
    — unwinds past CORSMiddleware without ever producing a response, so
    CORSMiddleware's send wrapper never runs. ServerErrorMiddleware then emits
    a bare 500 from above it carrying no Access-Control-Allow-Origin, and the
    browser reports a CORS policy error rather than the 500 it actually is.

    Must be registered *before* CORSMiddleware: add_middleware inserts at index
    0, so the last one added ends up outermost.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def _send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, _send)
        except Exception:
            if response_started:
                # Headers are already on the wire — there is nothing left to
                # replace them with, so let ServerErrorMiddleware handle it.
                raise

            logger.exception("Unhandled error on %s", scope.get("path", ""))

            response = JSONResponse(status_code=500, content=INTERNAL_ERROR_BODY)
            await response(scope, receive, send)

import logging
from time import monotonic

from fastapi import FastAPI

logger = logging.getLogger("uvicorn.error")


class RequestTimingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        started = monotonic()
        status = 500
        logged = False

        def finish(complete):
            nonlocal logged
            if logged:
                return
            logged = True
            route = getattr(scope.get("route"), "path", "unmatched")
            method = scope.get("method", "OTHER")
            if method not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}:
                method = "OTHER"
            logger.info(
                "http_request method=%s route=%s status=%d duration_ms=%.2f complete=%s",
                method, route, status, (monotonic() - started) * 1000, complete,
            )

        async def timed_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                duration_ms = (monotonic() - started) * 1000
                message["headers"].append(
                    (b"server-timing", f"app;dur={duration_ms:.2f}".encode("ascii"))
                )
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                finish(True)

        try:
            await self.app(scope, receive, timed_send)
        finally:
            finish(False)


class TimedFastAPI(FastAPI):
    def build_middleware_stack(self):
        return RequestTimingMiddleware(super().build_middleware_stack())

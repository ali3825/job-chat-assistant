"""One error envelope for the whole API: {"error": {code, message, recovery}}."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("app.errors")


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str, recovery: str | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.recovery = status, code, message, recovery


def _body(code: str, message: str, recovery: str | None = None) -> dict:
    return {"error": {"code": code, "message": message, "recovery": recovery}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        return JSONResponse(status_code=exc.status, content=_body(exc.code, exc.message, exc.recovery))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        fields = sorted({".".join(str(p) for p in e["loc"][1:]) or str(e["loc"][0]) for e in exc.errors()})
        return JSONResponse(
            status_code=422,
            content=_body("invalid_request", f"Invalid request: check {', '.join(fields)}.",
                          "Fix the highlighted fields and try again."),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        return JSONResponse(status_code=exc.status_code, content=_body("http_error", str(exc.detail)))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        # Log type + path only: never the body (may contain resume text) or a traceback to the client.
        log.error("unhandled %s on %s %s", type(exc).__name__, request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=_body("internal_error", "Something went wrong on our side.", "Please try again in a moment."),
        )

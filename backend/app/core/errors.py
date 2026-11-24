"""Errors that services raise for the API to turn into HTTP responses."""

from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    status_code = 400

    def __init__(
        self, detail: str, *, code: str | None = None, extra: Mapping[str, Any] | None = None
    ) -> None:
        """``extra`` adds fields to the JSON body next to ``detail`` and ``code`` (it can never
        replace them). Use it only where the design allows (docs/M2-DESIGN.md D27)."""
        super().__init__(detail)
        self.detail = detail
        self.code = code
        self.extra: dict[str, Any] = dict(extra or {})


class NotFound(AppError):
    status_code = 404


class Conflict(AppError):
    status_code = 409


class Unauthorized(AppError):
    status_code = 401


class Forbidden(AppError):
    status_code = 403


class Gone(AppError):
    status_code = 410


class TooManyRequests(AppError):
    status_code = 429


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle(_: Request, exc: AppError) -> JSONResponse:
        body: dict[str, Any] = {"detail": exc.detail}
        if exc.code:
            body["code"] = exc.code
        body.update((k, v) for k, v in exc.extra.items() if k not in ("detail", "code"))
        return JSONResponse(status_code=exc.status_code, content=body)

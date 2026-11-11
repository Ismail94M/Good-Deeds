"""Errors that services raise for the API to turn into HTTP responses."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    status_code = 400

    def __init__(self, detail: str, *, code: str | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.code = code


class NotFound(AppError):
    status_code = 404


class Conflict(AppError):
    status_code = 409


class Unauthorized(AppError):
    status_code = 401


class Forbidden(AppError):
    status_code = 403


class TooManyRequests(AppError):
    status_code = 429


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle(_: Request, exc: AppError) -> JSONResponse:
        body: dict[str, str] = {"detail": exc.detail}
        if exc.code:
            body["code"] = exc.code
        return JSONResponse(status_code=exc.status_code, content=body)

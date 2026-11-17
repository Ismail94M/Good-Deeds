"""``AppError`` bodies (``app.core.errors``): ``{detail, code}``, plus ``extra`` fields where the
design allows them (docs/M2-DESIGN.md D27). ``extra`` can never replace ``detail`` or ``code``."""

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.errors import Conflict, NotFound, install_error_handlers


def _app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/plain")
    async def plain() -> None:
        raise NotFound("Nothing here.", code="thing_not_found")

    @app.get("/extra")
    async def extra() -> None:
        raise Conflict(
            "Already done.",
            code="already_done",
            extra={"doer": {"handle": "ahmed", "display_name": "Ahmed"}},
        )

    @app.get("/override")
    async def override() -> None:
        raise Conflict(
            "The real detail.",
            code="real_code",
            extra={"detail": "forged", "code": "forged", "other": 1},
        )

    @app.get("/no-code")
    async def no_code() -> None:
        raise Conflict("No code.", extra={"code": "forged"})

    return app


async def _get(path: str) -> tuple[int, object]:
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        response = await c.get(path)
    return response.status_code, response.json()


async def test_plain_error_is_detail_and_code() -> None:
    assert await _get("/plain") == (404, {"detail": "Nothing here.", "code": "thing_not_found"})


async def test_extra_is_merged_after_detail_and_code() -> None:
    status, body = await _get("/extra")
    assert status == 409
    assert body == {
        "detail": "Already done.",
        "code": "already_done",
        "doer": {"handle": "ahmed", "display_name": "Ahmed"},
    }
    assert isinstance(body, dict) and list(body) == ["detail", "code", "doer"]


async def test_extra_cannot_override_detail_or_code() -> None:
    assert await _get("/override") == (
        409,
        {"detail": "The real detail.", "code": "real_code", "other": 1},
    )
    # Even without a code, ``extra`` can't add one.
    assert await _get("/no-code") == (409, {"detail": "No code."})

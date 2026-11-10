from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core import health
from app.core.config import get_settings
from app.modules.registry import load_modules, module_routers


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Good Deeds API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    load_modules()
    app.include_router(health.router, prefix="/api")
    for router in module_routers():
        app.include_router(router, prefix="/api")
    return app


app = create_app()

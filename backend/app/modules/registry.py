"""The list of feature modules. Each lives in app/modules/<name>/.

A module may provide:
  - router.py   with `router: APIRouter`   (mounted under /api)
  - models.py   SQLAlchemy models          (imported so Alembic sees them)
  - handlers.py event subscribers          (imported so the worker registers them)

Modules talk to each other only through another module's service.py or through events.
"""

import importlib
import importlib.util

from fastapi import APIRouter

MODULES: list[str] = [
    # Added milestone by milestone, e.g. "identity", "deeds", "verification", ...
]

_OPTIONAL_PARTS = ("models", "handlers")


def _import_if_exists(name: str) -> object | None:
    if importlib.util.find_spec(name) is None:
        return None
    return importlib.import_module(name)


def load_modules() -> None:
    """Import every module's models and handlers."""
    for module in MODULES:
        for part in _OPTIONAL_PARTS:
            _import_if_exists(f"app.modules.{module}.{part}")


def module_routers() -> list[APIRouter]:
    routers: list[APIRouter] = []
    for module in MODULES:
        mod = _import_if_exists(f"app.modules.{module}.router")
        if mod is not None:
            routers.append(mod.router)  # type: ignore[attr-defined]
    return routers

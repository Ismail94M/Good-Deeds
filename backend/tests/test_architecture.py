"""Enforce the modular-monolith boundaries described in CLAUDE.md.

- app.core never imports feature modules (except the module registry).
- A feature module imports another module only through that module's `service`.
"""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
MODULES_DIR = APP / "modules"


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
            # `from app.modules.x import service` imports the submodule `service`.
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _feature_modules() -> list[str]:
    return sorted(
        p.name for p in MODULES_DIR.iterdir() if p.is_dir() and (p / "__init__.py").exists()
    )


def _violations_for(module: str) -> list[str]:
    problems: list[str] = []
    for path in (MODULES_DIR / module).rglob("*.py"):
        for name in _imports(path):
            parts = name.split(".")
            if parts[:2] != ["app", "modules"] or len(parts) < 3:
                continue
            other = parts[2]
            if other in (module, "registry") or other not in _feature_modules():
                continue
            # Allowed: `app.modules.other` (package) and `app.modules.other.service`.
            if len(parts) == 3 or parts[3] == "service":
                continue
            problems.append(f"{path.relative_to(APP)} imports {name}")
    return problems


def test_feature_modules_only_use_other_modules_services() -> None:
    problems = [p for m in _feature_modules() for p in _violations_for(m)]
    assert problems == [], "Cross-module imports must go through service.py:\n" + "\n".join(
        problems
    )


def test_core_does_not_import_feature_modules() -> None:
    problems = [
        f"{path.relative_to(APP)} imports {name}"
        for path in (APP / "core").rglob("*.py")
        for name in _imports(path)
        if name.startswith("app.modules") and not name.startswith("app.modules.registry")
    ]
    assert problems == [], "\n".join(problems)


def test_every_feature_module_is_registered() -> None:
    from app.modules.registry import MODULES

    assert sorted(MODULES) == _feature_modules()

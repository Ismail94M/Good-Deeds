"""Enforce the modular-monolith boundaries described in CLAUDE.md.

- app.core never imports feature modules (except the module registry).
- A feature module imports another module only through that module's `service`.
- M3 import direction (docs/M3-DESIGN.md D30, D36): `media/service.py` and media's private
  files never import `feed` (only `media/router.py` may read `feed.service`), and no scoring,
  points, rewards or leaderboard code may import `feed` (visibility must never reach scoring).
"""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
MODULES_DIR = APP / "modules"


def _module_name(path: Path, app_dir: Path) -> list[str]:
    """`app/modules/media/service.py` -> ["app", "modules", "media", "service"]."""
    parts = ["app", *path.relative_to(app_dir).with_suffix("").parts]
    return parts[:-1] if parts[-1] == "__init__" else parts


def _imports(path: Path, app_dir: Path = APP) -> list[str]:
    """Absolute names of everything a file imports; relative imports are resolved."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module or ""
            else:
                package = _module_name(path, app_dir)
                if path.name != "__init__.py":
                    package = package[:-1]
                package = package[: len(package) - (node.level - 1)]
                base = ".".join([*package, *([node.module] if node.module else [])])
            if not base:
                continue
            names.append(base)
            # `from app.modules.x import service` imports the submodule `service`.
            names.extend(f"{base}.{alias.name}" for alias in node.names)
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


# --- M3: feed visibility never flows into media's core or into scoring (D30, D36) ---

FEED = "app.modules.feed"
# Names that mean scoring code, now or later (module packages, files or folders).
SCORING_NAMES = frozenset({"scoring", "points", "rewards", "leaderboard"})


def _imports_feed(path: Path, app_dir: Path) -> list[str]:
    return [n for n in _imports(path, app_dir) if n == FEED or n.startswith(FEED + ".")]


def _media_core_files(app_dir: Path) -> list[Path]:
    """`media/service.py` and media's private files (`_*.py`, any depth). The router may read
    `feed.service`; it is the only media file allowed to."""
    media = app_dir / "modules" / "media"
    if not media.is_dir():
        return []
    return sorted(
        p
        for p in media.rglob("*.py")
        if p.name == "service.py" or (p.name.startswith("_") and p.name != "__init__.py")
    )


def _media_feed_violations(app_dir: Path) -> list[str]:
    return [
        f"{path.relative_to(app_dir).as_posix()} imports {name}"
        for path in _media_core_files(app_dir)
        for name in _imports_feed(path, app_dir)
    ]


def _is_scoring_code(path: Path, app_dir: Path) -> bool:
    parts = path.relative_to(app_dir).with_suffix("").parts
    return any(part.lstrip("_") in SCORING_NAMES for part in parts)


def _scoring_feed_violations(app_dir: Path) -> list[str]:
    return [
        f"{path.relative_to(app_dir).as_posix()} imports {name}"
        for path in sorted(app_dir.rglob("*.py"))
        if _is_scoring_code(path, app_dir)
        for name in _imports_feed(path, app_dir)
    ]


def test_media_service_and_private_files_never_import_feed() -> None:
    problems = _media_feed_violations(APP)
    assert problems == [], (
        "media/service.py and media's private files must not import feed "
        "(only media/router.py may read feed.service):\n" + "\n".join(problems)
    )


def test_scoring_code_never_imports_feed() -> None:
    problems = _scoring_feed_violations(APP)
    assert problems == [], (
        "Scoring, points, rewards and leaderboard code must never read feed visibility:\n"
        + "\n".join(problems)
    )


def _write(root: Path, rel: str, source: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_feed_rules_catch_violations(tmp_path: Path) -> None:
    """The two rules above pass trivially while the modules are small; prove they bite."""
    app_dir = tmp_path / "app"
    _write(app_dir, "modules/media/service.py", "from app.modules.feed import service\n")
    _write(app_dir, "modules/media/_pipeline.py", "from ..feed.service import visible\n")
    _write(app_dir, "modules/media/_ok.py", "from app.modules.deeds import service\n")
    _write(app_dir, "modules/media/router.py", "from app.modules.feed import service\n")
    _write(app_dir, "modules/points/handlers.py", "import app.modules.feed.models\n")
    _write(app_dir, "modules/rewards/service.py", "from app.modules import feed\n")
    _write(app_dir, "modules/deeds/_scoring.py", "from app.modules.feed.service import x\n")
    _write(app_dir, "modules/leaderboard/service.py", "from app.modules.deeds import service\n")

    assert _media_feed_violations(app_dir) == [
        "modules/media/_pipeline.py imports app.modules.feed.service",
        "modules/media/_pipeline.py imports app.modules.feed.service.visible",
        "modules/media/service.py imports app.modules.feed",
        "modules/media/service.py imports app.modules.feed.service",
    ]
    assert {v.split(" imports ")[0] for v in _scoring_feed_violations(app_dir)} == {
        "modules/deeds/_scoring.py",
        "modules/points/handlers.py",
        "modules/rewards/service.py",
    }


def test_feed_rules_pass_without_media_or_feed_packages(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    _write(app_dir, "modules/deeds/service.py", "import app.core.db\n")
    assert _media_feed_violations(app_dir) == []
    assert _scoring_feed_violations(app_dir) == []


# --- M4: scoring reads neither visibility nor photo state, and AI never reaches points ---
# (docs/M4-DESIGN.md D47, D62, §3.4 #8)

MEDIA = "app.modules.media"
# The only name scoring may import from media: the event class it subscribes to.
SCORING_MEDIA_ALLOWED = ("app.modules.media.service", ("DeedPhotoReady",))
# Scoring files that decide points, ranks or titles: they never import the AI evaluator.
NO_AI_FILES = ("_ledger.py", "_rules.py", "_leaderboard.py", "_finalize.py", "_badges.py")
# Exactly the events scoring handles (§5); never a photo removal or any feed event.
SCORING_EVENTS = frozenset(
    {
        "deeds.deed_verified",
        "deeds.counts_for_points_changed",
        "deeds.prize_rank_changed",
        "deeds.deed_edited",
        "deeds.deed_withdrawn",
        "deeds.deed_rejected",
        "media.deed_photo_ready",
    }
)


def _media_imports(path: Path, app_dir: Path) -> list[str]:
    """Every media import of a file, except ``from app.modules.media.service import
    DeedPhotoReady`` (alone)."""
    problems: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0:
            module = node.module or ""
            names = tuple(alias.name for alias in node.names)
            if (module, names) == SCORING_MEDIA_ALLOWED:
                continue
            if module == MEDIA or module.startswith(MEDIA + "."):
                problems.append(f"{module} ({', '.join(names)})")
            elif module == "app.modules" and "media" in names:
                problems.append("app.modules.media")
        elif isinstance(node, ast.Import):
            problems.extend(
                a.name for a in node.names if a.name == MEDIA or a.name.startswith(MEDIA + ".")
            )
        elif isinstance(node, ast.ImportFrom) and node.level > 0:
            # Relative imports resolve inside scoring; any reaching media is listed.
            problems.extend(n for n in _imports(path, app_dir) if n.startswith(MEDIA))
    return problems


def _scoring_media_violations(app_dir: Path) -> list[str]:
    scoring = app_dir / "modules" / "scoring"
    if not scoring.is_dir():
        return []
    return [
        f"{path.relative_to(app_dir).as_posix()} imports {name}"
        for path in sorted(scoring.rglob("*.py"))
        for name in _media_imports(path, app_dir)
    ]


def _scoring_ai_violations(app_dir: Path) -> list[str]:
    scoring = app_dir / "modules" / "scoring"
    return [
        f"{(scoring / name).relative_to(app_dir).as_posix()} imports {imported}"
        for name in NO_AI_FILES
        if (scoring / name).exists()
        for imported in _imports(scoring / name, app_dir)
        if any(part.startswith("_ai") for part in imported.split("."))
    ]


def test_scoring_imports_only_deed_photo_ready_from_media() -> None:
    problems = _scoring_media_violations(APP)
    assert problems == [], (
        "scoring may import only DeedPhotoReady from media.service (D47, §3.4 #8):\n"
        + "\n".join(problems)
    )


def test_points_and_ranks_never_import_the_ai_evaluator() -> None:
    problems = _scoring_ai_violations(APP)
    assert problems == [], "Shadow AI must never reach points or ranks (D62):\n" + "\n".join(
        problems
    )


def test_scoring_subscribes_to_exactly_its_events() -> None:
    from app.core import events
    from app.modules.registry import MODULES, load_modules

    assert "scoring" in MODULES
    load_modules()
    handled = {
        event_type
        for event_type in events._event_types
        for name in events.handlers_for(event_type)
        if name.startswith("app.modules.scoring.")
    }
    assert handled == SCORING_EVENTS
    assert not any(t.startswith("feed.") for t in handled)
    assert not any("remov" in t or "moderation" in t for t in handled)


def test_scoring_rules_catch_violations(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    ok = "from app.modules.media.service import DeedPhotoReady\n"
    _write(app_dir, "modules/scoring/handlers.py", ok)
    _write(
        app_dir,
        "modules/scoring/_evidence.py",
        "from app.modules.media.service import DeedPhotoReady, public_photos\n",
    )
    _write(app_dir, "modules/scoring/_x.py", "import app.modules.media.models\n")
    _write(app_dir, "modules/scoring/_y.py", "from app.modules import media\n")
    _write(app_dir, "modules/scoring/_ledger.py", "from app.modules.scoring import _ai\n")
    _write(app_dir, "modules/scoring/_finalize.py", "from ._ai_tiebreak import run\n")
    _write(app_dir, "modules/scoring/_ai.py", "from app.modules.scoring import _rules\n")

    assert _scoring_media_violations(app_dir) == [
        "modules/scoring/_evidence.py imports app.modules.media.service "
        "(DeedPhotoReady, public_photos)",
        "modules/scoring/_x.py imports app.modules.media.models",
        "modules/scoring/_y.py imports app.modules.media",
    ]
    assert {v.split(" imports ")[0] for v in _scoring_ai_violations(app_dir)} == {
        "modules/scoring/_ledger.py",
        "modules/scoring/_finalize.py",
    }

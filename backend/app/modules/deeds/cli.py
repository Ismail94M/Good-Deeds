"""Deeds command line (M4 D65). Regions are added only here; no HTTP route changes them.

    uv run python -m app.modules.deeds.cli add-region <kind> <code> <name> [--parent <code>] \
        --reason "..." --operator "<your name>"

``<kind>`` is ``country``, ``state``, ``city`` or ``ward``. A country has no ``--parent``; a
state's parent is a country, a city's a state and a ward's a city (also enforced by the
``regions_check_parent`` trigger). A code is upper-case letters, digits and dashes, and a child's
code starts with its parent's code and a dash (``IN-KA-MYS`` under ``IN-KA``). The closure rows
are written by the database (trigger ``regions_add_closure``).

A ward region is linked to its boundary when its ``areas`` row is imported: the row takes the
ward region coded ``<city code>-<ward_code>`` (trigger ``areas_assign_region``).

Every addition writes ``audit_log`` (``region.added``, with the reason and the operator's name;
``actor_id`` stays null because no signed-in account runs it).

In Docker: ``docker compose exec api python -m app.modules.deeds.cli add-region ...``.
"""

import argparse
import asyncio
import re
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.db import SessionLocal
from app.modules.deeds.models import Region, RegionKind

CODE_RE = re.compile(r"^[A-Z0-9]+(-[A-Z0-9]+)*$")
CODE_MAX = 40
NAME_MAX = 80
PARENT_KIND: dict[RegionKind, RegionKind | None] = {
    RegionKind.COUNTRY: None,
    RegionKind.STATE: RegionKind.COUNTRY,
    RegionKind.CITY: RegionKind.STATE,
    RegionKind.WARD: RegionKind.CITY,
}


class RegionError(ValueError):
    """The region can't be added; the message is shown to the operator."""


async def add_region(
    session: AsyncSession,
    *,
    kind: RegionKind,
    code: str,
    name: str,
    parent_code: str | None,
    reason: str,
    operator: str,
) -> Region:
    """Add one region under ``parent_code`` and audit it. Raises ``RegionError`` (and writes
    nothing) when the input breaks a rule. The caller commits."""
    code, name, reason, operator = code.strip(), name.strip(), reason.strip(), operator.strip()
    if not reason:
        raise RegionError("a reason is required")
    if not operator:
        raise RegionError("an operator is required")
    if not (len(code) <= CODE_MAX and CODE_RE.fullmatch(code)):
        raise RegionError(
            f"code {code!r} must be upper-case letters, digits and dashes (at most {CODE_MAX})"
        )
    if not 1 <= len(name) <= NAME_MAX:
        raise RegionError(f"name must be 1-{NAME_MAX} characters")
    expected = PARENT_KIND[kind]
    parent: Region | None = None
    if expected is None:
        if parent_code is not None:
            raise RegionError("a country has no parent")
    else:
        if parent_code is None:
            raise RegionError(f"a {kind.value} needs --parent (a {expected.value})")
        parent = await session.scalar(select(Region).where(Region.code == parent_code.strip()))
        if parent is None:
            raise RegionError(f"no region with code {parent_code!r}")
        if parent.kind != expected:
            raise RegionError(
                f"a {kind.value} must be under a {expected.value}; "
                f"{parent.code} is a {parent.kind.value}"
            )
        if not code.startswith(parent.code + "-"):
            raise RegionError(f"code {code!r} must start with {parent.code + '-'!r}")
    if await session.scalar(select(Region.id).where(Region.code == code)) is not None:
        raise RegionError(f"a region with code {code!r} already exists")

    parent_id = None if parent is None else parent.id
    region = Region(kind=kind, code=code, name=name, parent_id=parent_id)
    session.add(region)
    await session.flush()
    record_audit(
        session,
        entity_type="region",
        entity_id=str(region.id),
        action="region.added",
        actor_id=None,
        new_value={
            "kind": kind.value,
            "code": code,
            "name": name,
            "parent_code": None if parent is None else parent.code,
            # Who ran the command (the CLI has no signed-in actor, so ``actor_id`` stays null).
            "operator": operator,
        },
        reason=reason,
    )
    await session.flush()
    return region


async def _add_region(
    kind: RegionKind, code: str, name: str, parent_code: str | None, reason: str, operator: str
) -> int:
    async with SessionLocal() as session:
        try:
            region = await add_region(
                session,
                kind=kind,
                code=code,
                name=name,
                parent_code=parent_code,
                reason=reason,
                operator=operator,
            )
        except RegionError as exc:
            print(f"Not added: {exc}.", file=sys.stderr)
            return 1
        await session.commit()
    print(f"Added {kind.value} {region.code} ({region.name}), id {region.id}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.modules.deeds.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    cmd = commands.add_parser("add-region", help="add a country, state, city or ward (audited)")
    cmd.add_argument("kind", choices=[k.value for k in RegionKind])
    cmd.add_argument("code", help="stable code, e.g. IN-KA-MYS (upper case, digits, dashes)")
    cmd.add_argument("name", help="display name, e.g. Mysuru")
    cmd.add_argument("--parent", help="the parent region's code (not for a country)")
    cmd.add_argument("--reason", required=True, help="why (kept in the audit log)")
    cmd.add_argument(
        "--operator", required=True, help="your name: who added the region (kept in the audit log)"
    )
    args = parser.parse_args(argv)
    if not args.reason.strip():
        parser.error("--reason must not be empty")
    if not args.operator.strip():
        parser.error("--operator must not be empty")
    run = _add_region(
        RegionKind(args.kind), args.code, args.name, args.parent, args.reason, args.operator
    )
    if sys.platform == "win32":
        # psycopg's async mode needs a selector event loop on Windows.
        return asyncio.run(run, loop_factory=asyncio.SelectorEventLoop)
    return asyncio.run(run)


if __name__ == "__main__":
    raise SystemExit(main())

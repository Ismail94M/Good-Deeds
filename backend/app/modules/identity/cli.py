"""Identity command line (M3 D43). Roles are granted only here; no HTTP route changes a role.

    uv run python -m app.modules.identity.cli set-role <handle> <role> --reason "..."

``<role>`` is ``admin``, ``moderator`` or ``member``.

Every change writes ``audit_log`` (``role.changed``, with the reason) and bumps the user's
``token_version`` (their current access tokens stop working).
"""

import argparse
import asyncio
import sys

from app.core.db import SessionLocal
from app.modules.identity.models import Role
from app.modules.identity.service import get_user_by_handle, set_role


async def _set_role(handle: str, role: Role, reason: str) -> int:
    async with SessionLocal() as session:
        user = await get_user_by_handle(session, handle)
        if user is None:
            print(f"No user with handle {handle!r}.", file=sys.stderr)
            return 1
        changed = await set_role(session, user, role, reason=reason)
        await session.commit()
    print(f"@{handle} is now {role.value}." if changed else f"@{handle} is already {role.value}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.modules.identity.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    cmd = commands.add_parser("set-role", help="change a user's role (audited)")
    cmd.add_argument("handle")
    cmd.add_argument("role", choices=[r.value for r in Role])
    cmd.add_argument("--reason", required=True, help="why (kept in the audit log)")
    args = parser.parse_args(argv)
    if not args.reason.strip():
        parser.error("--reason must not be empty")
    run = _set_role(args.handle.lstrip("@"), Role(args.role), args.reason)
    if sys.platform == "win32":
        # psycopg's async mode needs a selector event loop on Windows.
        return asyncio.run(run, loop_factory=asyncio.SelectorEventLoop)
    return asyncio.run(run)


if __name__ == "__main__":
    raise SystemExit(main())

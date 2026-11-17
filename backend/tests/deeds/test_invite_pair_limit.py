"""D21: the pair limit on invite acceptance (docs/M2-DESIGN.md §3.2 #9 steps 0 and 3b).

Accept counts the reporter's deeds about the accepter in the IST month of the invite deed's
``reported_at`` (the month the report already counts in for the total). At or above
``reporter_doer_monthly_limit`` it returns 429 ``pair_monthly_limit`` and the invite stays
pending. Lock order: unlocked reporter read -> ``reporter_month`` -> invite row (re-checking the
row's reporter) -> ``shared_device`` -> pending deed -> ``same_act``.

Invites are switched off by default (D17): these tests turn them on with ``invites_on``. The
concurrency tests at the end commit for real in separate sessions (``committed_b6`` cleans up);
they call the service directly, which doesn't read the flag (the router does), so they don't
hold the rolled-back ``invites_on`` transaction open alongside.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal
from app.core.errors import AppError
from app.modules.deeds import _invites, _qr_deeds
from app.modules.deeds.models import (
    Deed,
    DeedStatus,
    InviteStatus,
    QrScan,
    VerificationEvent,
)
from tests.deeds.helpers import (
    Member,
    count,
    make_member,
    publish_limits,
    report,
    scan,
    seed_deeds,
)
from tests.deeds.invite_helpers import accept, create_invite, invite_row, share_device
from tests.deeds.test_concurrency import _in_own_session
from tests.deeds.test_invite_concurrency import Committed, committed_b6  # noqa: F401

PAIR = 15  # the seeded pair limit (D19)
OCT_1 = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)  # 11:30 IST
OCT_20 = datetime(2026, 10, 20, 6, 0, tzinfo=UTC)
NOV_1_IST = datetime(2026, 10, 31, 18, 30, tzinfo=UTC)  # 00:00 IST on 1 November
DAY = timedelta(days=1)


async def _seed_pair(
    session: AsyncSession, reporter: Member, doer: Member, n: int, start: datetime = OCT_1
) -> None:
    """``n`` deeds by ``reporter`` about ``doer``, one per IST day from ``start``."""
    await seed_deeds(session, reporter, n, start=start, step=DAY, doer=doer)


async def _assert_still_pending(session: AsyncSession, deed_id: uuid.UUID) -> None:
    invite = await invite_row(session, deed_id)
    assert invite.status == InviteStatus.PENDING and invite.accepted_by is None
    deed = await session.get(Deed, deed_id, populate_existing=True)
    assert deed is not None and deed.status == DeedStatus.PENDING and deed.doer_user_id is None
    assert await count(session, VerificationEvent, VerificationEvent.deed_id == deed_id) == 0


# ======================================================================= boundaries


@pytest.mark.usefixtures("invites_on")
@pytest.mark.parametrize("already", [PAIR - 2, PAIR - 1])
async def test_accept_succeeds_below_the_pair_limit(session: AsyncSession, already: int) -> None:
    """With 13 or 14 deeds about the accepter this month, accepting makes the 14th or 15th."""
    reporter, doer = await make_member(session), await make_member(session)
    await _seed_pair(session, reporter, doer, already)
    _, token = await create_invite(session, reporter, at=OCT_20)
    accepted = await accept(session, token, doer, at=OCT_20 + timedelta(hours=1))
    assert accepted.status == "verified"
    about_doer = await count(
        session, Deed, Deed.reporter_user_id == reporter.user_id, Deed.doer_user_id == doer.user_id
    )
    assert about_doer == already + 1


@pytest.mark.usefixtures("invites_on")
async def test_accept_at_the_pair_limit_is_refused_and_the_invite_stays_pending(
    session: AsyncSession,
) -> None:
    """With 15, the 16th is refused. Creating the invite was allowed (the doer was unknown);
    the invite stays pending, so someone else can still accept it."""
    reporter, doer, someone_else = [await make_member(session) for _ in range(3)]
    await _seed_pair(session, reporter, doer, PAIR)
    out, token = await create_invite(session, reporter, at=OCT_20)
    with pytest.raises(AppError) as exc:
        await accept(session, token, doer, at=OCT_20 + timedelta(hours=1))
    assert exc.value.code == "pair_monthly_limit" and exc.value.status_code == 429
    assert exc.value.extra == {}  # no ``doer`` in accept errors (D27 is scan-time only)
    await _assert_still_pending(session, out.id)

    # The same invite is still usable by a different accepter.
    accepted = await accept(session, token, someone_else, at=OCT_20 + timedelta(hours=2))
    assert accepted.status == "verified"


@pytest.mark.usefixtures("invites_on")
async def test_pair_counts_every_status_and_ignores_other_reporters(
    session: AsyncSession,
) -> None:
    reporter, other_reporter, doer = [await make_member(session) for _ in range(3)]
    await seed_deeds(
        session, reporter, PAIR, start=OCT_1, step=DAY, doer=doer, status=DeedStatus.WITHDRAWN
    )
    _, token = await create_invite(session, reporter, at=OCT_20)
    with pytest.raises(AppError) as exc:
        await accept(session, token, doer, at=OCT_20 + timedelta(hours=1))
    assert exc.value.code == "pair_monthly_limit"

    # Another reporter's deeds about the same doer don't count towards this pair.
    await _seed_pair(session, other_reporter, doer, PAIR - 1)
    _, other_token = await create_invite(session, other_reporter, at=OCT_20)
    assert (await accept(session, other_token, doer, at=OCT_20)).status == "verified"


@pytest.mark.usefixtures("invites_on")
async def test_pair_limit_comes_from_config(session: AsyncSession) -> None:
    reporter, doer = await make_member(session), await make_member(session)
    await publish_limits(session, reporter_doer_monthly_limit=1)
    await _seed_pair(session, reporter, doer, 1)
    _, token = await create_invite(session, reporter, at=OCT_20)
    with pytest.raises(AppError) as exc:
        await accept(session, token, doer, at=OCT_20)
    assert exc.value.code == "pair_monthly_limit"
    await publish_limits(session, reporter_doer_monthly_limit=2)
    assert (await accept(session, token, doer, at=OCT_20)).status == "verified"


# ======================================================================= the deed's month


@pytest.mark.usefixtures("invites_on")
async def test_counts_in_the_month_of_the_deeds_reported_at_not_of_acceptance(
    session: AsyncSession,
) -> None:
    """Created on 30 October, accepted on 2 November: October's pair count decides."""
    reporter, doer = await make_member(session), await make_member(session)
    oct_30 = datetime(2026, 10, 30, 6, 0, tzinfo=UTC)
    nov_2 = datetime(2026, 11, 2, 6, 0, tzinfo=UTC)
    await _seed_pair(session, reporter, doer, PAIR)  # 1-15 October
    _, token = await create_invite(session, reporter, at=oct_30)
    with pytest.raises(AppError) as exc:
        await accept(session, token, doer, at=nov_2)
    assert exc.value.code == "pair_monthly_limit"


@pytest.mark.usefixtures("invites_on")
async def test_a_full_acceptance_month_does_not_refuse_an_earlier_months_deed(
    session: AsyncSession,
) -> None:
    """The reverse: a full November doesn't block accepting an October deed on 2 November."""
    reporter, doer = await make_member(session), await make_member(session)
    oct_30 = datetime(2026, 10, 30, 6, 0, tzinfo=UTC)
    _, token = await create_invite(session, reporter, at=oct_30)
    # 15 deeds about the doer reported in November (unrelated report days: November is young).
    await seed_deeds(
        session,
        reporter,
        PAIR,
        start=NOV_1_IST + timedelta(hours=1),
        step=timedelta(minutes=1),
        doer=doer,
        unrelated_days=True,
    )
    out = await accept(session, token, doer, at=datetime(2026, 11, 2, 6, 0, tzinfo=UTC))
    assert out.status == "verified" and out.reported_at == oct_30


# ======================================================================= order of checks


@pytest.mark.usefixtures("invites_on")
async def test_unusable_invite_and_own_invite_come_before_the_pair_check(
    session: AsyncSession,
) -> None:
    reporter, doer = await make_member(session), await make_member(session)
    await _seed_pair(session, reporter, doer, PAIR)
    _, token = await create_invite(session, reporter, at=OCT_20)
    # Past its 7-day expiry: 410, not 429.
    with pytest.raises(AppError) as exc:
        await accept(session, token, doer, at=OCT_20 + timedelta(days=8))
    assert exc.value.code == "invite_expired"
    # The reporter accepting their own invite: 403.
    with pytest.raises(AppError) as own:
        await accept(session, token, reporter, at=OCT_20)
    assert own.value.code == "cannot_accept_own_invite"
    # An unknown token: 404 from the unlocked read, before any lock.
    with pytest.raises(AppError) as unknown:
        await accept(session, "x" * 43, doer, at=OCT_20)
    assert unknown.value.code == "invite_invalid"


@pytest.mark.usefixtures("invites_on")
async def test_pair_check_comes_before_the_shared_device_limit(session: AsyncSession) -> None:
    reporter, doer = await make_member(session), await make_member(session)
    await share_device(session, reporter, doer)
    await publish_limits(session, shared_device_pair_monthly_limit=0)
    await _seed_pair(session, reporter, doer, PAIR)
    _, token = await create_invite(session, reporter, at=OCT_20)
    with pytest.raises(AppError) as exc:
        await accept(session, token, doer, at=OCT_20)
    assert exc.value.code == "pair_monthly_limit"


# ======================================================================= step 0 and the re-check


@pytest.mark.usefixtures("invites_on")
async def test_token_rotated_between_the_unlocked_read_and_the_lock_is_404(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An invite's reporter never changes, but a reissue can rotate the token after step 0
    read it: the locked lookup then finds nothing, and accept is 404 with nothing changed."""
    reporter, doer = await make_member(session), await make_member(session)
    out, token = await create_invite(session, reporter, at=OCT_20)
    assert out.invite is not None
    invite_id = out.invite.id
    real = _invites._invite_reporter

    async def read_then_reissue(s: AsyncSession, raw: str) -> uuid.UUID | None:
        found = await real(s, raw)
        await _invites.reissue(s, reporter.user_id, invite_id, now=OCT_20)
        return found

    monkeypatch.setattr(_invites, "_invite_reporter", read_then_reissue)
    with pytest.raises(AppError) as exc:
        await accept(session, token, doer, at=OCT_20 + timedelta(minutes=5))
    assert exc.value.code == "invite_invalid"
    await _assert_still_pending(session, out.id)


@pytest.mark.usefixtures("invites_on")
async def test_locked_invite_with_a_different_reporter_is_404(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defense in depth (§8): if step 0 ever returned a reporter other than the locked row's,
    accept refuses rather than run under the wrong ``reporter_month`` lock."""
    reporter, doer = await make_member(session), await make_member(session)
    out, token = await create_invite(session, reporter, at=OCT_20)

    async def wrong_reporter(s: AsyncSession, raw: str) -> uuid.UUID | None:
        return uuid.uuid4()

    monkeypatch.setattr(_invites, "_invite_reporter", wrong_reporter)
    with pytest.raises(AppError) as exc:
        await accept(session, token, doer, at=OCT_20)
    assert exc.value.code == "invite_invalid"
    await _assert_still_pending(session, out.id)


# ======================================================================= over the API


@pytest.mark.usefixtures("invites_on")
async def test_api_accept_at_the_pair_limit_is_429(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter, doer = await make_member(session), await make_member(session)
    now = datetime.now(UTC)
    step = timedelta(milliseconds=50)
    # Reported this IST month; unrelated report days because the month may be young.
    await seed_deeds(
        session,
        reporter,
        PAIR,
        start=now - (PAIR + 1) * step,
        step=step,
        doer=doer,
        unrelated_days=True,
        day_offset=1,
    )
    out, token = await create_invite(session, reporter, at=now)
    await session.commit()
    response = await client.post(f"/api/invites/by-token/{token}/accept", headers=doer.headers)
    assert response.status_code == 429, response.text
    assert response.json() == {
        "detail": response.json()["detail"],
        "code": "pair_monthly_limit",
    }
    await _assert_still_pending(session, out.id)
    listed = await client.get("/api/me/invites?status=pending", headers=reporter.headers)
    assert [i["deed_id"] for i in listed.json()["items"]] == [str(out.id)]


# ======================================================================= concurrency


def _meet_together(
    monkeypatch: pytest.MonkeyPatch, targets: list[tuple[object, str]], parties: int = 2
) -> None:
    """One shared barrier for ``parties`` callers before any of ``targets`` (module, function),
    so callers on different paths really start their locking at the same moment."""
    barrier = asyncio.Barrier(parties)
    for module, name in targets:
        real = getattr(module, name)

        def make(real_fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                async with asyncio.timeout(10):
                    await barrier.wait()
                return await real_fn(*args, **kwargs)

            return wrapper

        monkeypatch.setattr(module, name, make(real))


async def _pair_deeds(reporter: Member, doer: Member) -> int:
    async with SessionLocal() as s:
        n = await s.scalar(
            select(func.count())
            .select_from(Deed)
            .where(Deed.reporter_user_id == reporter.user_id, Deed.doer_user_id == doer.user_id)
        )
    return int(n or 0)


async def test_accept_racing_the_reporters_own_qr_submit_at_14_allows_one(
    committed_b6: Committed,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The doer accepts the reporter's invite while the reporter sends a QR report about the
    same doer, at 14/15. Both take ``reporter_month`` first, so there's no deadlock and exactly
    one becomes the 15th; the other gets ``pair_monthly_limit``."""
    reporter, doer = await committed_b6.member(), await committed_b6.member()
    at = datetime(2026, 9, 21, 6, 0, tzinfo=UTC)
    async with SessionLocal() as s:
        await _seed_pair(s, reporter, doer, PAIR - 1, start=datetime(2026, 9, 1, 6, tzinfo=UTC))
        out, token = await create_invite(s, reporter, at=at - DAY)  # pending, 20 Sept
        scanned = await scan(s, reporter, doer, at=at)  # 21 Sept, passes the pre-check
        await s.commit()
    _meet_together(
        monkeypatch, [(_invites, "_invite_reporter"), (_qr_deeds, "ensure_under_monthly_limits")]
    )

    sent = at + timedelta(minutes=1)
    results = await asyncio.gather(
        _in_own_session(lambda s: accept(s, token, doer, at=sent)),
        _in_own_session(lambda s: report(s, reporter, scanned.scan_id, at=sent)),
    )
    errors = [r for r in results if isinstance(r, AppError)]
    assert len(errors) == 1, results
    assert errors[0].code == "pair_monthly_limit"
    assert await _pair_deeds(reporter, doer) == PAIR
    async with SessionLocal() as s:
        if errors[0] is results[0]:  # the accept lost: the invite is still pending
            await _assert_still_pending(s, out.id)
        else:  # the QR report lost: its scan is still unused
            row = await s.get(QrScan, scanned.scan_id)
            assert row is not None and row.consumed_at is None


async def test_two_accepts_of_one_reporters_invites_at_14_allow_one(
    committed_b6: Committed,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The doer accepts two of the reporter's invites (created on different days) at once, at
    14/15: ``reporter_month`` serialises them and the second gets ``pair_monthly_limit``."""
    reporter, doer = await committed_b6.member(), await committed_b6.member()
    at = datetime(2026, 9, 21, 6, 0, tzinfo=UTC)
    async with SessionLocal() as s:
        await _seed_pair(s, reporter, doer, PAIR - 1, start=datetime(2026, 9, 1, 6, tzinfo=UTC))
        first, t_1 = await create_invite(s, reporter, at=at - 2 * DAY)
        second, t_2 = await create_invite(s, reporter, at=at - DAY)
        await s.commit()
    _meet_together(monkeypatch, [(_invites, "_invite_reporter")])

    results = await asyncio.gather(
        _in_own_session(lambda s: accept(s, t_1, doer, at=at)),
        _in_own_session(lambda s: accept(s, t_2, doer, at=at)),
    )
    errors = [r for r in results if isinstance(r, AppError)]
    assert len(errors) == 1, results
    assert errors[0].code == "pair_monthly_limit"
    assert await _pair_deeds(reporter, doer) == PAIR
    loser = first if errors[0] is results[0] else second
    async with SessionLocal() as s:
        await _assert_still_pending(s, loser.id)

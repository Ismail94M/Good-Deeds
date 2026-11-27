"""Revoking titles and badges (docs/M5-DESIGN.md D90, D92, D100, Q67, Q72; §3.4 #14 to #16;
§8 "Overrides and revocation"). Revocation needs a confirmed case whose decision is final for the
person; the title is not passed on, the final board stays frozen and its row shows as
"Unavailable account"."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.deeds import service as deeds
from app.modules.deeds.models import Deed
from app.modules.fraud._cases import subject_key
from app.modules.fraud.models import (
    Appeal,
    AppealOutcome,
    DecisionNotice,
    FlagCase,
    FlagCaseDeed,
    FlagCaseOutcome,
    FlagCaseStatus,
    FlagCaseType,
    FlagCaseUser,
    NoticeKind,
)
from app.modules.identity.models import Role
from app.modules.scoring._finalize import finalize_month
from app.modules.scoring._limits import load_leaderboard
from app.modules.scoring.models import HeroTitle, UserBadge
from app.modules.scoring.service import badges_for, final_snapshot, titles_for
from tests.deeds.helpers import Member, members, scan_and_report
from tests.fraud.helpers import days_ago, deed
from tests.fraud.review_helpers import admin, with_role
from tests.scoring.board_helpers import counted, ist, month, ward
from tests.scoring.override_helpers import audits, events

pytestmark = pytest.mark.usefixtures("city_board_on")

SEP = month(2026, 9)
NOTE = "Confirmed reciprocal recognitions."


async def _hero(session: AsyncSession) -> tuple[Member, Member, int, HeroTitle]:
    """``a`` is September's ward Hero of a test ward (3 deeds, 2 reporters); ``b`` is second."""
    a, b, r1, r2 = await members(session, 4)
    kora = await ward(session, "RV1")
    for day, reporter in ((3, r1), (4, r2), (5, r1)):
        await counted(session, a, reporter, region_id=kora, verified_at=ist(2026, 9, day))
    for day, reporter in ((6, r1), (7, r2), (8, r1)):
        await counted(session, b, reporter, region_id=kora, verified_at=ist(2026, 9, day), points=5)
    await finalize_month(session, SEP, now=ist(2026, 10, 3))
    # ``counted`` writes ledger rows only; give a's deeds their September verification time so
    # a case about them covers the title's month (SEC5-L4, ``fraud.service.case_covers_month``).
    await session.execute(
        update(Deed).where(Deed.doer_user_id == a.user_id).values(verified_at=Deed.reported_at)
    )
    await session.commit()
    title = await session.scalar(
        select(HeroTitle).where(
            HeroTitle.user_id == a.user_id, HeroTitle.region_id == kora, HeroTitle.place == 1
        )
    )
    assert title is not None
    return a, b, kora, title


async def _case(
    session: AsyncSession,
    user: Member,
    decider: Member,
    *,
    status: FlagCaseStatus = FlagCaseStatus.RESOLVED,
    outcome: FlagCaseOutcome | None = FlagCaseOutcome.CONFIRMED,
    notice: bool = True,
    final: bool = True,
    with_deeds: bool = True,
    others: tuple[Member, ...] = (),
) -> tuple[uuid.UUID, uuid.UUID | None]:
    """A case about ``user`` (and ``others`` as co-subjects) with, if ``with_deeds``, every deed
    ``user`` did as case deeds, and, if ``notice``, their decision notice (final or not)."""
    now = datetime.now(UTC)
    resolved = status == FlagCaseStatus.RESOLVED
    case = FlagCase(
        id=uuid.uuid4(),
        type=FlagCaseType.RECIPROCAL,
        status=status,
        outcome=outcome,
        subject_key=subject_key("reciprocal", [user.user_id, uuid.uuid4()]),
        signal={},
        detector_config_version=1,
        deed_count=0,
        held_count=0,
        resolved_at=now if resolved else None,
        resolved_by=decider.user_id if resolved else None,
    )
    session.add(case)
    await session.flush()
    for subject in (user, *others):
        session.add(FlagCaseUser(case_id=case.id, user_id=subject.user_id))
    if with_deeds:
        for deed_id in await session.scalars(
            select(Deed.id).where(Deed.doer_user_id == user.user_id)
        ):
            session.add(FlagCaseDeed(case_id=case.id, deed_id=deed_id))
    await session.flush()
    notice_id = None
    if notice:
        row = DecisionNotice(
            id=uuid.uuid4(),
            user_id=user.user_id,
            kind=NoticeKind.CASE,
            case_id=case.id,
            items=[],
            reason_key="case",
            appeal_until=now + timedelta(days=30),
            final_at=datetime.now(UTC) if final else None,
        )
        session.add(row)
        await session.flush()
        notice_id = row.id
    await session.commit()
    return case.id, notice_id


async def _revoke(
    client: AsyncClient, who: Member, title_id: uuid.UUID, case_id: uuid.UUID, kind: str = "titles"
) -> tuple[int, dict[str, Any]]:
    res = await client.post(
        f"/api/admin/scoring/{kind}/{title_id}/revoke",
        json={"case_id": str(case_id), "note": NOTE},
        headers=who.headers,
    )
    return res.status_code, res.json()


async def test_revocation_needs_a_final_confirmed_decision(
    client: AsyncClient, session: AsyncSession
) -> None:
    a, _, _, title = await _hero(session)
    adm, decider = await admin(session), await admin(session)

    open_case, _ = await _case(
        session, a, decider, status=FlagCaseStatus.OPEN, outcome=None, notice=False
    )
    dismissed, _ = await _case(session, a, decider, outcome=FlagCaseOutcome.DISMISSED, notice=False)
    not_final, _ = await _case(session, a, decider, final=False)
    for case_id, code in (
        (open_case, "case_not_confirmed"),
        (dismissed, "case_not_confirmed"),
        (uuid.uuid4(), "case_not_confirmed"),
        (not_final, "decision_not_final"),
    ):
        status, body = await _revoke(client, adm, title.id, case_id)
        assert (status, body["code"]) == (409, code)

    # Overturned on appeal: never revocable for that case.
    overturned, notice_id = await _case(session, a, decider)
    assert notice_id is not None
    session.add(
        Appeal(
            notice_id=notice_id,
            user_id=a.user_id,
            reason="These were genuine deeds.",
            reviewer_id=adm.user_id,
            outcome=AppealOutcome.OVERTURNED,
            reviewer_note="The deeds were genuine.",
            decided_at=datetime.now(UTC),
        )
    )
    await session.commit()
    status, body = await _revoke(client, adm, title.id, overturned)
    assert (status, body["code"]) == (409, "case_not_confirmed")

    short = await client.post(
        f"/api/admin/scoring/titles/{title.id}/revoke",
        json={"case_id": str(open_case), "note": "no"},
        headers=adm.headers,
    )
    assert (short.status_code, short.json()["code"]) == (422, "note_required")
    await session.refresh(title)
    assert title.revoked_at is None


async def test_revoked_title_is_not_passed_on_and_the_final_board_is_frozen(
    client: AsyncClient, session: AsyncSession
) -> None:
    a, b, kora, title = await _hero(session)
    adm, decider = await admin(session), await admin(session)
    case_id, _ = await _case(session, a, decider)
    before = await final_snapshot(session, kora, SEP)
    titles_before = await session.scalar(select(func.count()).select_from(HeroTitle))

    status, body = await _revoke(client, adm, title.id, case_id)
    assert status == 200, body
    revoked = [t for t in body["titles"] if t["id"] == str(title.id)]
    assert revoked and revoked[0]["revoked_at"] and revoked[0]["revoked_reason"] == NOTE

    # Gone from the profile and the heroes list; nobody else got it; the board didn't move.
    assert all(t.region_id != kora for t in (await titles_for(session, [a.user_id]))[a.user_id])
    assert await session.scalar(select(func.count()).select_from(HeroTitle)) == titles_before
    assert await final_snapshot(session, kora, SEP) == before
    code = (await deeds.regions_by_id(session, {kora}))[kora].code
    heroes = await client.get(
        f"/api/leaderboards/heroes?region={code}&year=2026", headers=b.headers
    )
    hero_users = [u for m in heroes.json()["items"] for t in m["titles"] for u in t["users"]]
    assert a.handle not in {u["handle"] for u in hero_users}
    board = (
        await client.get(f"/api/leaderboards?region={code}&month=2026-09", headers=b.headers)
    ).json()
    assert board["final"] is True
    first = board["items"][0]
    assert (first["rank"], first["user"]) == (
        1,
        {"handle": None, "display_name": "Unavailable account"},
    )
    assert board["items"][1]["user"]["handle"] == b.handle
    assert all(a.handle not in {u["handle"] for u in t["users"]} for t in board["titles"])

    (audit,) = await audits(session, "hero_title", str(title.id))
    assert (audit.action, audit.actor_id, audit.reason) == ("hero_title.revoked", adm.user_id, NOTE)
    (event,) = await events(session, "scoring.hero_title_revoked")
    assert event["title_id"] == str(title.id) and event["case_id"] == str(case_id)

    status, body = await _revoke(client, adm, title.id, case_id)
    assert (status, body["code"]) == (409, "already_revoked")


async def test_own_and_related_titles_are_404_and_moderators_403(
    client: AsyncClient, session: AsyncSession
) -> None:
    a, _, _, title = await _hero(session)
    decider = await admin(session)
    case_id, _ = await _case(session, a, decider)

    moderator = await with_role(session, (await members(session, 1))[0], Role.MODERATOR)
    assert (await _revoke(client, moderator, title.id, case_id))[0] == 403
    assert (
        await client.get(f"/api/admin/scoring/users/{a.handle}", headers=moderator.headers)
    ).status_code == 403

    await with_role(session, a, Role.ADMIN)
    status, body = await _revoke(client, a, title.id, case_id)
    assert (status, body["code"]) == (404, "title_not_found")
    me = await client.get(f"/api/admin/scoring/users/{a.handle}", headers=a.headers)
    assert (me.status_code, me.json()["code"]) == (404, "user_not_found")

    related = await admin(session)
    await scan_and_report(session, related, a)
    await session.commit()
    status, body = await _revoke(client, related, title.id, case_id)
    assert (status, body["code"]) == (404, "title_not_found")
    view = await client.get(f"/api/admin/scoring/users/{a.handle}", headers=related.headers)
    assert view.status_code == 404
    unknown = await client.get("/api/admin/scoring/users/nobody-here", headers=decider.headers)
    assert unknown.status_code == 404
    missing = await _revoke(client, decider, uuid.uuid4(), case_id)
    assert missing[0] == 404


async def test_user_view_and_badge_revocation(client: AsyncClient, session: AsyncSession) -> None:
    a, _, _, title = await _hero(session)
    adm, decider = await admin(session), await admin(session)
    _, config = await load_leaderboard(session)
    slug = config.badges[0].slug
    badge = UserBadge(user_id=a.user_id, badge_slug=slug, config_version=1)
    session.add(badge)
    await session.commit()

    view = await client.get(f"/api/admin/scoring/users/{a.handle}", headers=adm.headers)
    assert view.status_code == 200, view.json()
    body = view.json()
    assert body["user"]["handle"] == a.handle
    assert body["doer_points"] == 30 and body["reporter_points"] == 0
    assert str(title.id) in {t["id"] for t in body["titles"]}
    assert [(x["slug"], x["revoked_at"]) for x in body["badges"]] == [(slug, None)]

    not_final, _ = await _case(session, a, decider, final=False)
    status, out = await _revoke(client, adm, badge.id, not_final, kind="badges")
    assert (status, out["code"]) == (409, "decision_not_final")

    case_id, _ = await _case(session, a, decider)
    status, out = await _revoke(client, adm, badge.id, case_id, kind="badges")
    assert status == 200, out
    assert out["badges"][0]["revoked_reason"] == NOTE
    assert (await badges_for(session, [a.user_id]))[a.user_id] == []
    (event,) = await events(session, "scoring.badge_revoked")
    assert (event["badge_id"], event["badge_slug"]) == (str(badge.id), slug)
    status, out = await _revoke(client, adm, badge.id, case_id, kind="badges")
    assert (status, out["code"]) == (409, "already_revoked")


# --------------------------------------------------- SEC5-L4


async def test_title_needs_a_case_about_its_month(
    client: AsyncClient, session: AsyncSession
) -> None:
    """A confirmed, final case with no deed verified in September can't revoke September's
    title (409 ``case_not_for_this_month``); badges have no month."""
    a, _, _, title = await _hero(session)
    adm, decider = await admin(session), await admin(session)
    no_deeds, _ = await _case(session, a, decider, with_deeds=False)
    status, body = await _revoke(client, adm, title.id, no_deeds)
    assert (status, body["code"]) == (409, "case_not_for_this_month")
    await session.refresh(title)
    assert title.revoked_at is None

    _, config = await load_leaderboard(session)
    badge = UserBadge(user_id=a.user_id, badge_slug=config.badges[0].slug, config_version=1)
    session.add(badge)
    await session.commit()
    status, body = await _revoke(client, adm, badge.id, no_deeds, kind="badges")
    assert status == 200, body


async def test_admin_related_to_another_subject_gets_404(
    client: AsyncClient, session: AsyncSession
) -> None:
    """The admin has no deed with the title owner, but recognised a co-subject of the case:
    conflicted with the case, so 404 as for an unknown title or badge (D90)."""
    a, _, _, title = await _hero(session)
    decider, related = await admin(session), await admin(session)
    (partner,) = await members(session, 1)
    await scan_and_report(session, related, partner)
    await session.commit()
    case_id, _ = await _case(session, a, decider, others=(partner,))

    status, body = await _revoke(client, related, title.id, case_id)
    assert (status, body["code"]) == (404, "title_not_found")
    _, config = await load_leaderboard(session)
    badge = UserBadge(user_id=a.user_id, badge_slug=config.badges[0].slug, config_version=1)
    session.add(badge)
    await session.commit()
    status, body = await _revoke(client, related, badge.id, case_id, kind="badges")
    assert (status, body["code"]) == (404, "badge_not_found")

    # A non-conflicted admin may revoke with the same case.
    status, body = await _revoke(client, decider, title.id, case_id)
    assert status == 200, body


async def test_admin_who_reported_an_old_case_deed_gets_404(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Review N5: the admin reported one of the case's deeds 200 days ago (outside the
    lookback, so not related to the owner now). The case queue hides the case from them
    (``fraud.service.case_visible_to``), so revoking with it is 404 as for an unknown title."""
    a, _, _, title = await _hero(session)
    decider = await admin(session)
    (person,) = await members(session, 1)
    who = await with_role(session, person, Role.ADMIN)
    await deed(session, who, a, days_ago(200))
    await session.commit()
    case_id, _ = await _case(session, a, decider)

    status, body = await _revoke(client, who, title.id, case_id)
    assert (status, body["code"]) == (404, "title_not_found")
    status, body = await _revoke(client, decider, title.id, case_id)
    assert status == 200, body

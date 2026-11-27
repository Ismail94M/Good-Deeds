"""D24 / Q18 (PO, 2026-11-17): the doer sees only ``verified`` deeds about them, plus (M5 D101)
deeds ``rejected`` after review.

``GET /api/me/deeds?role=doer`` lists verified deeds (counted or not) and rejected ones, and
``GET /api/deeds/{id}`` is 404 ``deed_not_found`` to the doer for every other status. A
withdrawn, removed or unverified report disappears from the doer's view; the reporter still sees
it. A rejected deed stays, so My deeds can show "Removed after review" linked to its notice; it is
owner-only and never public. Pending deeds have no doer, so they never reach the doer's view.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.deeds.models import Deed, DeedStatus
from app.modules.deeds.schemas import DeedOut
from tests.deeds.helpers import Member, make_member, members, scan_and_report
from tests.deeds.invite_helpers import withdraw


async def _pages(client: AsyncClient, who: Member, role: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"role": role, "limit": 1}
        if cursor:
            params["cursor"] = cursor
        response = await client.get("/api/me/deeds", params=params, headers=who.headers)
        assert response.status_code == 200, response.text
        body = response.json()
        items += body["items"]
        cursor = body["next_cursor"]
        if cursor is None:
            return items


async def _one_act(
    session: AsyncSession, doer: Member, n: int
) -> tuple[list[Member], list[DeedOut]]:
    """``n`` witnesses report one act (same place, minutes apart): with the cap of 5, reports
    6 and later are verified but don't count."""
    witnesses = await members(session, n)
    base = datetime.now(UTC) - timedelta(minutes=30)
    deeds = [
        await scan_and_report(session, w, doer, at=base + timedelta(minutes=i), east_m=i * 5)
        for i, w in enumerate(witnesses)
    ]
    return witnesses, deeds


async def _force(session: AsyncSession, deed: DeedOut, status: DeedStatus) -> None:
    await session.execute(
        update(Deed).where(Deed.id == deed.id).values(status=status, counts_for_points=False)
    )


async def test_doer_sees_only_verified_and_rejected_deeds_in_list_and_detail(
    client: AsyncClient, session: AsyncSession
) -> None:
    doer = await make_member(session)
    witnesses, deeds = await _one_act(session, doer, 7)
    assert [d.same_act.counts_for_points for d in deeds if d.same_act] == [True] * 5 + [False] * 2
    # 1: withdrawn by its reporter (promotes report 6, D13); 2-4: closed by later stages.
    await withdraw(session, witnesses[0], deeds[0].id)
    await _force(session, deeds[1], DeedStatus.REJECTED)
    await _force(session, deeds[2], DeedStatus.REMOVED)
    await _force(session, deeds[3], DeedStatus.UNVERIFIED)
    await session.commit()
    hidden, visible = [deeds[0], deeds[2], deeds[3]], [deeds[1], *deeds[4:]]

    listed = await _pages(client, doer, "doer")
    # Newest first; includes report 7, which is verified but not counted, and the rejected one.
    assert [d["id"] for d in listed] == [str(d.id) for d in reversed(visible)]
    assert [d["status"] for d in listed] == ["verified"] * 3 + ["rejected"]
    assert listed[0]["same_act"]["counts_for_points"] is False
    # Nothing to change on a rejected deed, and the doer never gets edit or withdraw anyway.
    assert listed[-1]["can_edit"] is False and listed[-1]["can_withdraw"] is False
    for item in listed:
        # A person ref carries the handle, name and avatar ref (M6A D117), nothing else.
        assert set(item["reporter"]) == {"handle", "display_name", "avatar"}

    for deed in hidden:
        response = await client.get(f"/api/deeds/{deed.id}", headers=doer.headers)
        assert response.status_code == 404, response.text
        assert response.json() == {
            "detail": "We couldn't find that deed.",
            "code": "deed_not_found",
        }
    for deed in visible:
        response = await client.get(f"/api/deeds/{deed.id}", headers=doer.headers)
        assert response.status_code == 200, response.text


async def test_reporter_still_sees_every_status(client: AsyncClient, session: AsyncSession) -> None:
    doer = await make_member(session)
    witnesses, deeds = await _one_act(session, doer, 2)
    await withdraw(session, witnesses[0], deeds[0].id)
    await _force(session, deeds[1], DeedStatus.REJECTED)
    await session.commit()
    for witness, deed, status in zip(witnesses, deeds, ("withdrawn", "rejected"), strict=True):
        detail = await client.get(f"/api/deeds/{deed.id}", headers=witness.headers)
        assert detail.status_code == 200 and detail.json()["status"] == status
        own = await _pages(client, witness, "reporter")
        assert [(d["id"], d["status"]) for d in own] == [(str(deed.id), status)]
    # The doer keeps only the rejected one (D101); the withdrawn one is gone (Q18).
    assert [(d["id"], d["status"]) for d in await _pages(client, doer, "doer")] == [
        (str(deeds[1].id), "rejected")
    ]


async def test_a_rejected_deed_stays_private_to_its_doer_and_reporter(
    client: AsyncClient, session: AsyncSession
) -> None:
    """D101 shows a rejected deed to its owner only: a stranger gets the same 404 as for any
    deed, it doesn't count on the doer's public profile, and the stranger's own list is empty."""
    doer, stranger = await make_member(session), await make_member(session)
    _, deeds = await _one_act(session, doer, 1)
    await _force(session, deeds[0], DeedStatus.REJECTED)
    await session.commit()
    as_doer = await client.get(f"/api/deeds/{deeds[0].id}", headers=doer.headers)
    assert as_doer.status_code == 200 and as_doer.json()["status"] == "rejected"
    as_stranger = await client.get(f"/api/deeds/{deeds[0].id}", headers=stranger.headers)
    assert as_stranger.status_code == 404
    assert as_stranger.json()["code"] == "deed_not_found"
    assert await _pages(client, stranger, "doer") == []
    counts = await client.get(f"/api/users/{doer.handle}/deed-counts", headers=stranger.headers)
    assert counts.status_code == 200 and counts.json() == {"verified": 0}


async def test_a_doers_hidden_deed_looks_like_a_strangers(
    client: AsyncClient, session: AsyncSession
) -> None:
    """The doer's 404 for a withdrawn deed is the same as anyone's for a deed they can't see."""
    doer, stranger = await make_member(session), await make_member(session)
    witnesses, deeds = await _one_act(session, doer, 1)
    await withdraw(session, witnesses[0], deeds[0].id)
    await session.commit()
    as_doer = await client.get(f"/api/deeds/{deeds[0].id}", headers=doer.headers)
    as_stranger = await client.get(f"/api/deeds/{deeds[0].id}", headers=stranger.headers)
    assert as_doer.status_code == as_stranger.status_code == 404
    assert as_doer.json() == as_stranger.json()

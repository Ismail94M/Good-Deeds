"""Reporter edits of a deed (docs/M2-DESIGN.md §3.2 #14, §2.6b, §7 Edit and withdraw)."""

import uuid
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.deeds.models import Deed, DeedRevision, DeedStatus
from tests.deeds.helpers import (
    Member,
    count,
    make_member,
    members,
    outbox,
    publish_limits,
    scan_and_report,
)
from tests.deeds.invite_helpers import create_invite, withdraw


async def _patch(client: AsyncClient, who: Member, deed_id: Any, body: dict[str, Any]) -> Response:
    return await client.patch(f"/api/deeds/{deed_id}", json=body, headers=who.headers)


async def _verified(session: AsyncSession) -> tuple[Member, Member, uuid.UUID]:
    reporter, doer = await members(session, 2)
    deed = await scan_and_report(session, reporter, doer)
    await session.commit()
    return reporter, doer, deed.id


async def _revisions(session: AsyncSession, deed_id: uuid.UUID) -> list[DeedRevision]:
    rows = await session.scalars(
        select(DeedRevision)
        .where(DeedRevision.deed_id == deed_id)
        .order_by(DeedRevision.created_at)
    )
    return list(rows)


async def test_edit_writes_revision_and_event(client: AsyncClient, session: AsyncSession) -> None:
    reporter, _, deed_id = await _verified(session)
    response = await _patch(
        client,
        reporter,
        deed_id,
        {"category": "community", "description": "  Carried shopping bags up the stairs  "},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["category"] == "community"
    assert body["description"] == "Carried shopping bags up the stairs"
    assert body["beneficiaries_count"] is None
    assert body["last_edited_at"] is not None
    assert body["status"] == "verified"

    revisions = await _revisions(session, deed_id)
    assert len(revisions) == 1
    rev = revisions[0]
    assert rev.editor_user_id == reporter.user_id
    # Only the changed fields.
    assert rev.old_values == {
        "category": "animals",
        "description": "Helped a stray dog across the road",
    }
    assert rev.new_values == {
        "category": "community",
        "description": "Carried shopping bags up the stairs",
    }
    events = await outbox(session, "deeds.deed_edited", deed_id)
    assert len(events) == 1
    assert events[0].payload["revision_id"] == str(rev.id)
    assert events[0].payload["changed_fields"] == ["category", "description"]
    # Field names only, never the text.
    assert "shopping" not in str(events[0].payload)


async def test_each_edit_is_one_revision(client: AsyncClient, session: AsyncSession) -> None:
    reporter, _, deed_id = await _verified(session)
    for value in (3, None):
        response = await _patch(client, reporter, deed_id, {"beneficiaries_count": value})
        assert response.status_code == 200, response.text
    revisions = await _revisions(session, deed_id)
    assert [(r.old_values, r.new_values) for r in revisions] == [
        ({"beneficiaries_count": None}, {"beneficiaries_count": 3}),
        ({"beneficiaries_count": 3}, {"beneficiaries_count": None}),
    ]
    assert len(await outbox(session, "deeds.deed_edited", deed_id)) == 2


async def test_noop_edit_writes_nothing(client: AsyncClient, session: AsyncSession) -> None:
    reporter, _, deed_id = await _verified(session)
    for body in (
        {},
        {"category": "animals"},
        {"description": "Helped a stray dog across the road "},
        {"beneficiaries_count": None},
    ):
        response = await _patch(client, reporter, deed_id, body)
        assert response.status_code == 200, response.text
        assert response.json()["last_edited_at"] is None
    assert await _revisions(session, deed_id) == []
    assert await outbox(session, "deeds.deed_edited", deed_id) == []


async def test_only_the_reporter_can_edit(client: AsyncClient, session: AsyncSession) -> None:
    reporter, doer, deed_id = await _verified(session)
    stranger = await make_member(session)
    for who in (doer, stranger):
        response = await _patch(client, who, deed_id, {"category": "community"})
        assert response.status_code == 404
        assert response.json()["code"] == "deed_not_found"
    missing = await _patch(client, reporter, uuid.uuid4(), {"category": "community"})
    assert missing.status_code == 404
    assert await _revisions(session, deed_id) == []


@pytest.mark.parametrize(
    "body",
    [
        {"occurred_at": "2026-11-01T10:00:00+05:30"},
        {"location": {"lat": 12.97, "lng": 77.59, "accuracy_m": 10}},
        {"status": "verified"},
        {"doer_user_id": str(uuid.uuid4())},
        {"verification_method": "qr_scan"},
        {"counts_for_points": True},
        {"prize_rank_eligible": True},
        {"category": None},
        {"description": None},
        {"description": ""},
        {"beneficiaries_count": 0},
    ],
)
async def test_evidence_fields_cannot_be_edited(
    client: AsyncClient, session: AsyncSession, body: dict[str, Any]
) -> None:
    reporter, _, deed_id = await _verified(session)
    assert (await _patch(client, reporter, deed_id, body)).status_code == 422


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"category": "nope"}, "invalid_category"),
        ({"description": "short"}, "invalid_description"),
    ],
)
async def test_edit_is_validated_against_config(
    client: AsyncClient, session: AsyncSession, body: dict[str, Any], code: str
) -> None:
    reporter, _, deed_id = await _verified(session)
    response = await _patch(client, reporter, deed_id, body)
    assert response.status_code == 400 and response.json()["code"] == code


async def test_beneficiaries_max_from_config(client: AsyncClient, session: AsyncSession) -> None:
    reporter, _, deed_id = await _verified(session)
    await publish_limits(session, beneficiaries_max=5)
    response = await _patch(client, reporter, deed_id, {"beneficiaries_count": 6})
    assert response.status_code == 400 and response.json()["code"] == "invalid_beneficiaries"
    assert (await _patch(client, reporter, deed_id, {"beneficiaries_count": 5})).status_code == 200


async def test_pending_invite_deed_can_be_edited(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter = await make_member(session)
    created, _ = await create_invite(session, reporter)
    await session.commit()
    response = await _patch(client, reporter, created.id, {"category": "community"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "pending" and body["doer"] is None
    # The reporter sees the invite, but never its link again.
    assert body["invite"]["status"] == "pending" and body["invite"]["url"] is None


@pytest.mark.parametrize("status", [DeedStatus.UNVERIFIED, DeedStatus.REJECTED])
async def test_closed_deeds_cannot_be_edited(
    client: AsyncClient, session: AsyncSession, status: DeedStatus
) -> None:
    reporter = await make_member(session)
    created, _ = await create_invite(session, reporter)
    await session.execute(update(Deed).where(Deed.id == created.id).values(status=status))
    await session.commit()
    response = await _patch(client, reporter, created.id, {"category": "community"})
    assert response.status_code == 409 and response.json()["code"] == "deed_not_editable"


async def test_withdrawn_deed_cannot_be_edited(client: AsyncClient, session: AsyncSession) -> None:
    reporter, _, deed_id = await _verified(session)
    await withdraw(session, reporter, deed_id)
    await session.commit()
    response = await _patch(client, reporter, deed_id, {"category": "community"})
    assert response.status_code == 409 and response.json()["code"] == "deed_not_editable"
    assert await count(session, DeedRevision, DeedRevision.deed_id == deed_id) == 0


async def test_edit_account_and_ip_limits(
    client: AsyncClient, other_ip: AsyncClient, session: AsyncSession
) -> None:
    await publish_limits(session, rate_deed_edit_user="1/86400", rate_deed_edit_ip="2/86400")
    a, b, c = await members(session, 3)
    url = f"/api/deeds/{uuid.uuid4()}"
    assert (await client.patch(url, json={}, headers=a.headers)).status_code == 404
    # The account key holds on another IP; withdraw shares it.
    limited = await other_ip.post(f"{url}/withdraw", headers=a.headers)
    assert limited.status_code == 429 and limited.json()["code"] == "rate_limited"
    # The IP key holds across accounts.
    assert (await client.patch(url, json={}, headers=b.headers)).status_code == 404
    assert (await client.post(f"{url}/withdraw", headers=c.headers)).status_code == 429

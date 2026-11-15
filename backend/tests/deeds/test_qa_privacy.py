"""QA1: privacy sweeps across the whole M2 surface (docs/M2-DESIGN.md §7, §8 Privacy).

Independent of the developer tests: every flow runs through the HTTP API with a distinctive
location, device ID and client IP, and then every response body, outbox payload, ledger row and
audit row the flow produced is searched for coordinates, phone numbers, device or IP data, and
(in event payloads) free text.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient, Response
from sqlalchemy import String, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.events import OutboxEvent
from app.core.security import keyed_hash
from app.modules.deeds import _invites
from app.modules.deeds.models import (
    Deed,
    DeedRevision,
    DeedStatus,
    Invite,
    InviteStatus,
    SameActGroup,
    VerificationEvent,
)
from app.modules.identity import service
from app.modules.identity.models import User
from tests.deeds.helpers import (
    Member,
    code_for,
    deed_body,
    location,
    make_member,
    members,
    offset,
    publish_limits,
    scan_and_report,
    token_for,
)
from tests.deeds.invite_helpers import create_invite, invite_body, token_of

# A distinctive place about 3.2 km north and 4.3 km east of the service-area centre.
NORTH, EAST = 3210.0, 4321.0
DEVICE = "qa-device-7f3a91"
OTHER_IP = "203.0.113.9"  # the `other_ip` fixture's address


def _coordinate_strings(north_m: float, east_m: float) -> list[str]:
    lat, lng = offset(north_m, east_m)
    out = []
    for value in (lat, lng):
        out += [f"{value:.4f}", f"{value:.5f}", repr(value)[:9]]
    return out


async def _phones(session: AsyncSession, people: list[Member]) -> list[str]:
    phones = []
    for m in people:
        user = await session.get(User, m.user_id)
        assert user is not None and user.phone_e164 is not None
        phones += [user.phone_e164, user.phone_e164.removeprefix("+91"), m.phone_hash]
    return phones


def _assert_absent(haystack: str, needles: list[str], where: str) -> None:
    for needle in needles:
        assert needle not in haystack, f"{needle!r} leaked in {where}: {haystack[:400]}"


class Recorder:
    """Keeps every response body so the whole flow can be searched afterwards."""

    def __init__(self) -> None:
        self.bodies: list[tuple[str, str]] = []

    def __call__(self, response: Response, expected: int) -> Any:
        assert response.status_code == expected, response.text
        self.bodies.append(
            (f"{response.request.method} {response.request.url.path}", response.text)
        )
        return response.json() if response.content else None


async def test_no_coordinates_phone_device_or_ip_anywhere(
    client: AsyncClient, other_ip: AsyncClient, session: AsyncSession
) -> None:
    # cap 1 so the flow also exercises promotion and both prize-rank flips.
    await publish_limits(session, same_act_cap=1)
    doer, w1, w2, inviter, lapsed_inviter = await members(session, 5)
    everyone = [doer, w1, w2, inviter, lapsed_inviter]
    rec = Recorder()
    dev = {"X-Device-Id": DEVICE}
    here = location(NORTH, EAST)

    # 1. QR scan + deed (w1), manual code scan + deed (w2, another IP, 40 m away).
    scan1 = rec(
        await client.post(
            "/api/scans",
            json={"token": await token_for(session, doer), "location": here},
            headers={**w1.headers, **dev},
        ),
        201,
    )
    d1 = rec(
        await client.post("/api/deeds", json=deed_body(scan1["scan_id"]), headers=w1.headers), 201
    )
    scan2 = rec(
        await other_ip.post(
            "/api/scans",
            json={
                "short_id": doer.short_id,
                "code": await code_for(session, doer),
                "location": location(NORTH + 40, EAST),
            },
            headers={**w2.headers, **dev},
        ),
        201,
    )
    d2 = rec(
        await other_ip.post("/api/deeds", json=deed_body(scan2["scan_id"]), headers=w2.headers),
        201,
    )

    # 2. An invite deed of the same act, with a location; previewed and accepted by the doer.
    created = rec(
        await client.post(
            "/api/deeds",
            json=invite_body(
                occurred_at=(datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
                location=location(NORTH, EAST + 30),
            ),
            headers=inviter.headers,
        ),
        201,
    )
    raw = created["invite"]["url"].rsplit("/", 1)[1]
    rec(await client.get(f"/api/invites/by-token/{raw}"), 200)
    accepted = rec(
        await client.post(f"/api/invites/by-token/{raw}/accept", headers={**doer.headers, **dev}),
        200,
    )
    assert accepted["prize_rank_eligible"] is True  # w1/w2 are independent reporters

    # 3. Edit, withdraw (promotion), withdraw again (prize-rank revoke), reissue errors.
    rec(
        await client.patch(
            f"/api/deeds/{d1['id']}", json={"category": "community"}, headers=w1.headers
        ),
        200,
    )
    rec(await client.post(f"/api/deeds/{d1['id']}/withdraw", headers=w1.headers), 200)
    rec(await client.post(f"/api/deeds/{d2['id']}/withdraw", headers=w2.headers), 200)

    # 4. An invite that lapses (DeedRejected).
    lapsed, _ = await create_invite(
        session, lapsed_inviter, at=datetime.now(UTC) - timedelta(days=8), north_m=NORTH
    )
    await session.commit()
    await _invites.expire_invites(session)
    await session.commit()

    # 5. A self-log with a location, and every read endpoint.
    rec(
        await client.post(
            "/api/me/self-logs",
            json={
                "category": "animals",
                "description": "Fed the street dogs near the park",
                "occurred_at": datetime.now(UTC).isoformat(),
                "location": here,
            },
            headers=doer.headers,
        ),
        201,
    )
    rec(await client.get("/api/me/self-logs", headers=doer.headers), 200)
    for who in (w1, w2, doer, inviter):
        for role in ("reporter", "doer"):
            rec(await client.get(f"/api/me/deeds?role={role}", headers=who.headers), 200)
        rec(await client.get("/api/me/invites", headers=who.headers), 200)
    for deed_id in (d1["id"], d2["id"], created["id"]):
        rec(await client.get(f"/api/deeds/{deed_id}", headers=doer.headers), 200)
    rec(await client.get(f"/api/scans/{scan1['scan_id']}", headers=w1.headers), 200)
    rec(await client.get("/api/deeds/options", headers=w1.headers), 200)
    rec(await client.get(f"/api/users/{doer.handle}/deed-counts"), 200)

    # ---------------------------------------------------------------- the sweep
    coords = _coordinate_strings(NORTH, EAST) + _coordinate_strings(NORTH + 40, EAST)
    coords += _coordinate_strings(NORTH, EAST + 30)
    secrets_ = (
        await _phones(session, everyone)
        + [DEVICE, service.device_hash(DEVICE)]
        + [OTHER_IP, keyed_hash("ip", OTHER_IP), keyed_hash("ip", "127.0.0.1")]
    )
    for where, body in rec.bodies:
        _assert_absent(body, coords + secrets_, f"response {where}")
        assert '"lat"' not in body and '"lng"' not in body, where
    # The raw invite token is in the create response only (the DB keeps only its hash).
    assert [w for w, b in rec.bodies if raw in b] == ["POST /api/deeds"]
    invite = (
        await session.scalars(select(Invite).where(Invite.deed_id == uuid.UUID(created["id"])))
    ).one()
    assert invite.token_hash != raw and raw not in invite.token_hash

    deed_ids = [uuid.UUID(d1["id"]), uuid.UUID(d2["id"]), uuid.UUID(created["id"]), lapsed.id]
    user_ids = [str(m.user_id) for m in everyone]
    payload_text = cast(OutboxEvent.payload, String)
    rows = (
        await session.scalars(
            select(OutboxEvent).where(
                or_(*(payload_text.contains(str(i)) for i in [*deed_ids, *user_ids]))
            )
        )
    ).all()
    types = {r.event_type for r in rows}
    # The flow produced every M2 event type, so the sweep covers all of them.
    assert types == {
        "deeds.deed_submitted",
        "deeds.deed_verified",
        "deeds.deed_rejected",
        "deeds.prize_rank_changed",
        "deeds.deed_edited",
        "deeds.deed_withdrawn",
        "deeds.counts_for_points_changed",
    }, types
    free_text = ["Helped a stray dog", "stray dog", "street dogs"]
    handles = [m.handle for m in everyone]
    for row in rows:
        text = str(row.payload)
        _assert_absent(
            text, [*coords, *secrets_, *free_text, *handles, raw], f"outbox {row.event_type}"
        )
        assert not {"lat", "lng", "location", "description", "phone"} & set(row.payload)

    for ev in (
        await session.scalars(
            select(VerificationEvent).where(VerificationEvent.deed_id.in_(deed_ids))
        )
    ).all():
        _assert_absent(str(ev.evidence), coords + secrets_, f"evidence {ev.kind}")
    audit_rows = (
        await session.scalars(
            select(AuditLog).where(
                AuditLog.entity_type == "deed",
                AuditLog.entity_id.in_([str(i) for i in deed_ids]),
            )
        )
    ).all()
    assert {a.action for a in audit_rows} == {
        "prize_rank_eligible.granted",
        "prize_rank_eligible.revoked",
        "counts_for_points.granted",
    }
    for a in audit_rows:
        _assert_absent(f"{a.old_value}{a.new_value}{a.reason}", coords + secrets_, "audit_log")
    for rev in (
        await session.scalars(select(DeedRevision).where(DeedRevision.deed_id.in_(deed_ids)))
    ).all():
        _assert_absent(f"{rev.old_values}{rev.new_values}", coords + secrets_, "deed_revisions")


# --------------------------------------------------- self-log content (D3, D4)


async def test_self_log_content_never_leaks_through_public_or_other_endpoints(
    client: AsyncClient, session: AsyncSession
) -> None:
    owner, stranger, witness = await members(session, 3)
    secret = "QA private self log 5b1e: fed the cats on 4th cross"
    created = await client.post(
        "/api/me/self-logs",
        json={
            "category": "animals",
            "description": secret,
            "occurred_at": "2026-06-01T08:00:00+05:30",
            "location": location(NORTH, EAST),
        },
        headers=owner.headers,
    )
    assert created.status_code == 201
    log_id = created.json()["id"]
    # The owner is also a doer of a real deed, so their deed views aren't empty.
    await scan_and_report(session, witness, owner)
    await session.commit()

    responses = [
        await client.get(f"/api/users/{owner.handle}/deed-counts"),
        await client.get(f"/api/users/{owner.handle}"),
        await client.get(f"/api/users/{owner.handle}", headers=stranger.headers),
        await client.get("/api/me/self-logs", headers=stranger.headers),
        await client.patch(
            f"/api/me/self-logs/{log_id}", json={"category": "community"}, headers=stranger.headers
        ),
        await client.delete(f"/api/me/self-logs/{log_id}", headers=stranger.headers),
        # A self-log is never a deed, not even for its owner.
        await client.get(f"/api/deeds/{log_id}", headers=owner.headers),
        await client.get(f"/api/deeds/{log_id}", headers=stranger.headers),
        await client.get("/api/me/deeds?role=doer", headers=owner.headers),
        await client.get("/api/me/deeds?role=reporter", headers=owner.headers),
        await client.get(f"/api/users/{owner.handle}/deed-counts", headers=witness.headers),
    ]
    for r in responses:
        assert secret not in r.text and "fed the cats" not in r.text, (r.request.url, r.text)
        _assert_absent(r.text, _coordinate_strings(NORTH, EAST), str(r.request.url))
        assert "2026-06-01" not in r.text, r.text  # not even the date
    counts = responses[0].json()
    assert counts == {"self_logged": 1, "verified": 1}
    doer_view = responses[8].json()["items"]
    assert len(doer_view) == 1 and doer_view[0]["description"] != secret
    assert responses[4].status_code == responses[5].status_code == 404
    assert responses[6].status_code == responses[7].status_code == 404


# --------------------------------------------------- pending deeds never reveal a doer


async def test_failed_accept_leaves_no_trace_of_the_would_be_doer(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Accept fails late (pair-day unique, after grouping has run): the deed must stay pending
    with no doer, no group and nothing visible to the would-be doer."""
    reporter, doer = await members(session, 2)
    created, token = await create_invite(session, reporter, north_m=0)
    await scan_and_report(session, reporter, doer)  # same pair, same report day
    await session.commit()
    groups_before = (
        await session.scalars(
            select(SameActGroup.id).where(SameActGroup.doer_user_id == doer.user_id)
        )
    ).all()

    response = await client.post(f"/api/invites/by-token/{token}/accept", headers=doer.headers)
    assert response.status_code == 409 and response.json()["code"] == "already_recognized_today"

    deed = await session.get(Deed, created.id, populate_existing=True)
    assert deed is not None
    assert deed.status == DeedStatus.PENDING and deed.doer_user_id is None
    assert deed.same_act_group_id is None and deed.counts_for_points is False
    invite = (
        await session.scalars(
            select(Invite)
            .where(Invite.deed_id == created.id)
            .execution_options(populate_existing=True)
        )
    ).one()
    assert invite.status == InviteStatus.PENDING and invite.accepted_by is None
    groups_after = (
        await session.scalars(
            select(SameActGroup.id).where(SameActGroup.doer_user_id == doer.user_id)
        )
    ).all()
    assert sorted(groups_after) == sorted(groups_before)

    as_reporter = (await client.get(f"/api/deeds/{created.id}", headers=reporter.headers)).json()
    assert as_reporter["status"] == "pending" and as_reporter["doer"] is None
    assert doer.handle not in str(as_reporter)
    assert (await client.get(f"/api/deeds/{created.id}", headers=doer.headers)).status_code == 404
    doer_list = (await client.get("/api/me/deeds?role=doer", headers=doer.headers)).json()
    assert str(created.id) not in str(doer_list)
    counts = (await client.get(f"/api/users/{doer.handle}/deed-counts")).json()
    assert counts["verified"] == 1


async def test_pending_doer_is_null_in_every_reporter_view(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter = await make_member(session)
    created, token = await create_invite(session, reporter)
    await session.commit()
    views = [
        (await client.get(f"/api/deeds/{created.id}", headers=reporter.headers)).json(),
        (await client.get("/api/me/deeds?role=reporter", headers=reporter.headers)).json()["items"][
            0
        ],
        (
            await client.patch(
                f"/api/deeds/{created.id}",
                json={"description": "Helped an old man carry his shopping"},
                headers=reporter.headers,
            )
        ).json(),
    ]
    for view in views:
        assert view["status"] == "pending" and view["doer"] is None, view
        assert view["same_act"] is None
    preview = (await client.get(f"/api/invites/by-token/{token}")).json()
    assert set(preview) == {
        "status",
        "reporter",
        "category",
        "description",
        "occurred_at",
        "expires_at",
    }
    assert token_of(created) == token

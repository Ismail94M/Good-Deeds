"""QA10: roles, conflict of interest and privacy across every M5 surface (docs/M5-DESIGN.md D90,
D95, D101, §3.1, §3.5, §8 "API and privacy", "COI"). The endpoint lists come from the app's
OpenAPI schema, so a new endpoint can't be added without falling under these checks:

- every admin endpoint added in M5 is 401 signed out and 403 for members and moderators;
- a conflicted admin (a subject, the reporter of a case deed, or someone with a verified deed
  with a subject inside the lookback) gets 404 on the case, its appeals, the override and
  override-request endpoints and the user view, never 403/409, and the lists leave them out;
- no member or public GET endpoint says anything about fraud, cases, holds, detectors or the
  admins involved, before a decision, after it and after a deed is rejected.
"""

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.modules.fraud.models import FlagCaseStatus, FlagCaseType
from app.modules.identity.models import Role
from tests.deeds.helpers import Member, make_member, members
from tests.fraud.appeal_helpers import appeal, appeal_of, confirm, drain, finalize, notice_of
from tests.fraud.helpers import cases_for, days_ago, deed, run_fraud
from tests.fraud.review_helpers import (
    NOTE,
    admin,
    ask,
    case_row,
    confirm_body,
    decisions,
    with_role,
)
from tests.scoring.override_helpers import override

LATER = timedelta(days=31)

# Admin endpoints from M3 (photo/comment reports, ReviewerUser: moderators allowed) and M4.
OLDER_ADMIN = {
    ("GET", "/api/admin/photo-reports"),
    ("GET", "/api/admin/photo-reports/{case_id}"),
    ("POST", "/api/admin/photo-reports/{case_id}/decision"),
    ("POST", "/api/admin/photos/{photo_id}/restore"),
    ("GET", "/api/admin/comment-reports"),
    ("GET", "/api/admin/comment-reports/{case_id}"),
    ("POST", "/api/admin/comment-reports/{case_id}/decision"),
    ("POST", "/api/admin/comments/{comment_id}/restore"),
    ("GET", "/api/admin/scoring/deeds/{deed_id}"),
    ("GET", "/api/admin/scoring/months/{month}"),
}
# Every admin endpoint M5 adds (§3.3 #1 to #12, §3.4 #13 to #18).
M5_ADMIN = {
    ("GET", "/api/admin/fraud/options"),
    ("GET", "/api/admin/fraud/status"),
    ("GET", "/api/admin/fraud/cases"),
    ("GET", "/api/admin/fraud/cases/{case_id}"),
    ("POST", "/api/admin/fraud/cases/{case_id}/park"),
    ("POST", "/api/admin/fraud/cases/{case_id}/unpark"),
    ("POST", "/api/admin/fraud/cases/{case_id}/decision"),
    ("POST", "/api/admin/fraud/cases/{case_id}/explanations"),
    ("GET", "/api/admin/audit"),
    ("GET", "/api/admin/fraud/appeals"),
    ("GET", "/api/admin/fraud/appeals/{appeal_id}"),
    ("POST", "/api/admin/fraud/appeals/{appeal_id}/decision"),
    ("POST", "/api/admin/scoring/deeds/{deed_id}/overrides"),
    ("GET", "/api/admin/scoring/override-requests"),
    ("POST", "/api/admin/scoring/override-requests/{request_id}/decision"),
    ("GET", "/api/admin/scoring/users/{handle}"),
    ("POST", "/api/admin/scoring/titles/{title_id}/revoke"),
    ("POST", "/api/admin/scoring/badges/{badge_id}/revoke"),
}

# Member and public reads: path parameters we can fill, and the ones that need objects this
# scenario doesn't make (a scan, an invite, a self-log, a comment), which are skipped.
SKIPPED_PARAMS = {"scan_id", "token", "self_log_id", "log_id", "comment_id", "key"}

FORBIDDEN = re.compile(
    r"\b(fraud\w*|flag\w*|cases?|case_id|ring|reciprocal|concentration|invite_anomaly"
    r"|detector\w*|held|holds?|prize_holds?|suspicio\w*|collu\w*)\b",
    re.IGNORECASE,
)


def _routes() -> set[tuple[str, str]]:
    return {
        (method.upper(), path)
        for path, ops in app.openapi()["paths"].items()
        for method in ops
        if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}
    }


def _fill(path: str, values: dict[str, str]) -> str | None:
    names = re.findall(r"{(\w+)}", path)
    if any(n not in values for n in names):
        return None
    for name in names:
        path = path.replace("{" + name + "}", values[name])
    return path


# --------------------------------------------------- roles on every M5 admin endpoint


def test_the_m5_admin_endpoint_list_is_complete() -> None:
    admin_routes = {r for r in _routes() if r[1].startswith("/api/admin")}
    assert admin_routes - OLDER_ADMIN == M5_ADMIN


async def test_every_m5_admin_endpoint_refuses_members_and_moderators(
    client: AsyncClient, session: AsyncSession
) -> None:
    member = await make_member(session)
    moderator = await with_role(session, await make_member(session), Role.MODERATOR)
    values = {
        name: str(uuid.uuid4())
        for name in ("case_id", "appeal_id", "deed_id", "request_id", "title_id", "badge_id")
    }
    values["handle"] = member.handle
    for method, path in sorted(M5_ADMIN):
        url = _fill(path, values)
        assert url is not None, path
        body = {"note": NOTE} if method == "POST" else None
        anon = await client.request(method, url, json=body)
        assert anon.status_code == 401, (method, path, anon.status_code)
        for who in (member, moderator):
            res = await client.request(method, url, json=body, headers=who.headers)
            assert res.status_code == 403, (method, path, who.handle, res.status_code)


# --------------------------------------------------- conflict of interest everywhere


async def _conflict_scenario(client: AsyncClient, session: AsyncSession) -> dict[str, Any]:
    """A resolved reciprocal case a <-> b (a -> b excluded), a's open appeal, and a pending
    big-cut request on a -> b, made by admins who are not conflicted."""
    a, b = await members(session, 2)
    decider, requester = await admin(session), await admin(session)
    ab = await deed(session, a, b, days_ago(4))
    ba = await deed(session, b, a, days_ago(3))
    await run_fraud(session, ab, ba)
    await session.commit()
    (case,) = await cases_for(session, FlagCaseType.RECIPROCAL, a, b)
    await drain(session, [ab, ba])
    await confirm(session, decider, case.id, {ab: "exclude", ba: "release"})
    notice = await notice_of(session, case.id, b)
    assert (await appeal(client, b, notice.id)).status_code == 200
    appeal_id = (await appeal_of(session, notice.id)).id
    res = await override(client, requester, ab, points=0)
    assert res.status_code == 202, res.text
    return {
        "a": a,
        "b": b,
        "ab": ab,
        "case_id": case.id,
        "appeal_id": appeal_id,
        "request_id": res.json()["id"],
    }


async def _assert_hidden(client: AsyncClient, who: Member, s: dict[str, Any]) -> None:
    case_id, appeal_id, request_id = s["case_id"], s["appeal_id"], s["request_id"]
    h = who.headers
    # Lists leave everything out (counts too).
    for status in ("open", "parked", "resolved"):
        page = (
            await client.get("/api/admin/fraud/cases", params={"status": status}, headers=h)
        ).json()
        assert str(case_id) not in {i["case_id"] for i in page["items"]}
    resolved = (
        await client.get("/api/admin/fraud/cases", params={"status": "resolved"}, headers=h)
    ).json()
    assert resolved["counts"]["resolved"] == 0
    by_user = await client.get(
        "/api/admin/fraud/cases",
        params={"status": "resolved", "user": s["b"].handle},
        headers=h,
    )
    assert str(case_id) not in by_user.text
    appeals = (await client.get("/api/admin/fraud/appeals", headers=h)).json()
    assert appeals["items"] == [] and appeals["counts"]["open"] == 0
    requests = (await client.get("/api/admin/scoring/override-requests", headers=h)).json()
    assert requests["items"] == [] and requests["counts"]["pending"] == 0

    # Every object endpoint is 404, even where an eligible admin would get 409 (the case is
    # resolved): a conflicted admin must not learn its state.
    base = f"/api/admin/fraud/cases/{case_id}"
    calls: list[tuple[str, str, dict[str, Any] | None]] = [
        ("GET", base, None),
        ("POST", f"{base}/park", {"note": NOTE}),
        ("POST", f"{base}/unpark", {"note": NOTE}),
        ("POST", f"{base}/decision", {"outcome": "dismissed", "note": NOTE}),
        ("POST", f"{base}/explanations", {"note": NOTE}),
        ("GET", f"/api/admin/fraud/appeals/{appeal_id}", None),
        (
            "POST",
            f"/api/admin/fraud/appeals/{appeal_id}/decision",
            {"outcome": "overturned", "note": NOTE},
        ),
        ("GET", f"/api/admin/scoring/deeds/{s['ab']}", None),
        (
            "POST",
            f"/api/admin/scoring/deeds/{s['ab']}/overrides",
            {"role": "doer", "points": 9, "reason_code": "fake_photo", "note": NOTE},
        ),
        (
            "POST",
            f"/api/admin/scoring/override-requests/{request_id}/decision",
            {"decision": "approve", "note": NOTE},
        ),
        ("GET", f"/api/admin/scoring/users/{s['b'].handle}", None),
    ]
    for method, path, body in calls:
        res = await client.request(method, path, json=body, headers=h)
        assert res.status_code == 404, (who.handle, method, path, res.status_code, res.text)


async def test_conflicted_admins_get_404_everywhere(
    client: AsyncClient, session: AsyncSession
) -> None:
    s = await _conflict_scenario(client, session)
    eligible = await admin(session)
    # A subject (made admin after the fact), and an admin who recognised b 89 days ago.
    subject = await with_role(session, s["a"], Role.ADMIN)
    related = await admin(session)
    await deed(session, related, s["b"], days_ago(89))
    await session.commit()
    for who in (subject, related):
        await _assert_hidden(client, who, s)
    # Nothing changed, and an eligible admin still sees all of it.
    assert (await case_row(session, s["case_id"])).status == FlagCaseStatus.RESOLVED
    assert (
        await appeal_of(session, (await notice_of(session, s["case_id"], s["b"])).id)
    ).outcome is None
    h = eligible.headers
    assert (
        await client.get(f"/api/admin/fraud/cases/{s['case_id']}", headers=h)
    ).status_code == 200
    assert (
        await client.get(f"/api/admin/fraud/appeals/{s['appeal_id']}", headers=h)
    ).status_code == 200
    assert (await client.get(f"/api/admin/scoring/deeds/{s['ab']}", headers=h)).status_code == 200
    listed = (await client.get("/api/admin/scoring/override-requests", headers=h)).json()
    assert [r["id"] for r in listed["items"]] == [s["request_id"]]


async def test_lookback_boundary_for_appeals_and_overrides(
    client: AsyncClient, session: AsyncSession
) -> None:
    """91 days ago is outside the 90-day lookback: that admin may review the appeal and the
    request (the case-page boundary is in test_review)."""
    s = await _conflict_scenario(client, session)
    old = await admin(session)
    await deed(session, old, s["b"], days_ago(91))
    await session.commit()
    h = old.headers
    assert (
        await client.get(f"/api/admin/fraud/appeals/{s['appeal_id']}", headers=h)
    ).status_code == 200
    listed = (await client.get("/api/admin/scoring/override-requests", headers=h)).json()
    assert [r["id"] for r in listed["items"]] == [s["request_id"]]


async def test_the_audit_view_is_open_to_a_conflicted_admin(
    client: AsyncClient, session: AsyncSession
) -> None:
    """§3.5: the audit view has no conflict rule (own actions included)."""
    s = await _conflict_scenario(client, session)
    subject = await with_role(session, s["a"], Role.ADMIN)
    res = await client.get("/api/admin/audit", headers=subject.headers)
    assert res.status_code == 200
    assert await decisions(session, s["case_id"]) == ["request_explanation", "confirm"]


# --------------------------------------------------- D95 / D101 scan of member and public reads


async def _scan_everything(
    client: AsyncClient,
    viewers: list[Member | None],
    values: dict[str, list[str]],
    banned: list[str],
) -> int:
    """Call every non-admin GET endpoint as each viewer with every filling of its path
    parameters, and check no body mentions fraud, cases, holds, detectors or ``banned``."""
    scanned = 0  # successful reads
    gets = sorted(p for m, p in _routes() if m == "GET" and not p.startswith("/api/admin"))
    for path in gets:
        names = re.findall(r"{(\w+)}", path)
        if any(n in SKIPPED_PARAMS for n in names):
            continue
        assert all(n in values for n in names), f"no values for {path}"
        fillings: list[dict[str, str]] = [{}]
        for name in names:
            fillings = [{**f, name: v} for f in fillings for v in values[name]]
        for filling in fillings:
            url = _fill(path, filling)
            assert url is not None
            for viewer in viewers:
                res = await client.get(url, headers=viewer.headers if viewer else {})
                assert res.status_code < 500, (url, res.status_code)
                text = res.text
                hit = FORBIDDEN.search(text)
                assert hit is None, (url, viewer.handle if viewer else "anon", hit, text[:400])
                for value in banned:
                    assert value not in text, (url, value)
                if path == "/api/scoring/me/ledger":
                    # D95: a hold or release is never shown as a points-history line.
                    assert "prize_rank_changed" not in text, (url, text[:400])
                scanned += res.status_code == 200
    return scanned


async def test_no_member_or_public_read_mentions_cases_holds_or_admins(
    client: AsyncClient, session: AsyncSession
) -> None:
    """A 4-ring a -> b -> c -> d -> a, scanned (as a subject, an outsider and signed out) after
    detection and an explanation request, after a confirm with an appeal, and after the
    rejection is final."""
    people = await members(session, 4)
    a, b, _, _ = people
    outsider = await make_member(session)
    decider, reviewer = await admin(session), await admin(session)
    t0 = days_ago(2)
    made = [
        await deed(session, people[i], people[(i + 1) % 4], t0 + timedelta(hours=6 * i))
        for i in range(4)
    ]
    await drain(session, made)
    await run_fraud(session, *made)
    await session.commit()
    (case,) = await cases_for(session, FlagCaseType.RING, *people)
    banned = [
        str(case.id),
        decider.handle,
        reviewer.handle,
        "fraud_confirmed",
    ]
    values = {
        "handle": [p.handle for p in people],
        "deed_id": [str(m) for m in made],
        "photo_id": [str(uuid.uuid4())],
        "provider": ["google"],
        "invite_id": [str(uuid.uuid4())],
    }
    viewers: list[Member | None] = [a, b, outsider, None]

    await ask(session, decider, case.id, now=datetime.now(UTC) - timedelta(days=20))
    ok = await _scan_everything(client, viewers, values, banned)
    assert ok > 50

    res = await client.post(
        f"/api/admin/fraud/cases/{case.id}/decision",
        json=confirm_body(
            {made[0]: "reject", made[1]: "exclude", made[2]: "release", made[3]: "release"}
        ),
        headers=decider.headers,
    )
    assert res.status_code == 200, res.text
    await drain(session, made, [case.id])
    notice = await notice_of(session, case.id, a)
    assert (await appeal(client, a, notice.id)).status_code == 200
    await _scan_everything(client, viewers, values, banned)

    res = await client.post(
        f"/api/admin/fraud/appeals/{(await appeal_of(session, notice.id)).id}/decision",
        json={"outcome": "upheld", "note": NOTE},
        headers=reviewer.headers,
    )
    assert res.status_code == 200
    result = await finalize(session, datetime.now(UTC) + LATER)
    assert result.rejected == {case.id: [made[0]]}
    await drain(session, made, [case.id])
    await _scan_everything(client, viewers, values, banned)

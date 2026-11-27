"""The admin fraud review (docs/M5-DESIGN.md §3.2, §3.3 #1 to #7, D88, D90, D95, D99, D100).

- Queue (#2): open and parked cases by ``review_by`` (nulls last), then ``opened_at``; resolved
  ones newest first. Cases the admin is conflicted for are left out of items and counts (D90).
- Detail (#3): people, deeds, edges with shared-device/network booleans, decisions,
  explanations and the months' finalization. No coordinates, phone numbers or hashes (D95); no
  points (D78).
- Park / unpark (#4, #5) and the decision (#6). Every action writes one ``flag_case_decisions``
  row and one ``audit_log`` row (``_actions.record``).

Decision effects (#6), one transaction, case row locked ``FOR UPDATE`` first (§4.1):

- ``dismissed``: no per-deed actions. Open and parked cases hold nothing (D80); any hold the case
  has anyway (only with a PO-approved ``hold_on_open``) is released.
- ``confirmed``: every case deed needs an action, and at least one must be adverse (else 422
  ``use_dismiss``). D99 is checked for every subject with an adverse deed. ``exclude`` and
  ``reject`` place this case's hold (``reject`` is rejected later, once final: D89, D100);
  ``release`` releases this case's hold if it has one. One notice per subject with an adverse
  item (D100).
- Holds, outcomes, notices and the decision row are written before the update that resolves the
  case: a trigger freezes resolved cases. ``DeedHoldChanged`` is emitted for each deed whose
  held state changed, then ``FlagCaseResolved``.
"""

import math
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Literal, Protocol

from sqlalchemy import and_, exists, func, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict
from app.core.events import emit
from app.modules.deeds import service as deeds
from app.modules.fraud import _actions, _appeals, _coi, _cursor, _explain, _notices
from app.modules.fraud._cases import LIVE, month_review_deadline
from app.modules.fraud._common import Invalid, clean_note, user_not_found
from app.modules.fraud._limits import FraudConfig
from app.modules.fraud.detectors import DETECTORS
from app.modules.fraud.events import DeedHoldChanged, FlagCaseResolved
from app.modules.fraud.models import (
    DeedOutcome,
    DetectorRunStatus,
    DetectorRunTrigger,
    FlagCase,
    FlagCaseAction,
    FlagCaseDecision,
    FlagCaseDeed,
    FlagCaseDeedOutcome,
    FlagCaseOutcome,
    FlagCaseStatus,
    FlagCaseType,
    FlagCaseUser,
    FraudDetectorRun,
    HoldReleaseReason,
    PrizeHold,
)
from app.modules.fraud.schemas import (
    CaseCounts,
    CaseDecision,
    CaseDeed,
    CaseDetail,
    CaseEdge,
    CaseExplanation,
    CaseHeader,
    CasePage,
    CaseRow,
    CaseTypeOption,
    CaseUser,
    CountWindow,
    DecisionIn,
    DeedAction,
    DetectorRunOut,
    DetectorStatus,
    FinalizationMonth,
    FraudOptions,
    FraudStatus,
    ReasonCodeOption,
    SecondAdminRule,
    UserRef,
)
from app.modules.identity import service as identity

type Queue = Literal["open", "parked", "resolved"]

# Admin labels (admin screens may say "fraud", D101). Unknown codes fall back to the code.
TYPE_LABELS: dict[str, str] = {
    "reciprocal": "Reciprocal",
    "ring": "Ring",
    "concentration": "Concentration",
    "invite_anomaly": "Invite anomaly",
    "content": "Content",
}
REASON_CODE_LABELS: dict[str, str] = {
    "fake_photo": "Fake photo",
    "rule_misfire": "Rule misfire",
    "fraud_confirmed": "Fraud confirmed",
    "other": "Other",
}
LIST_USERS = 5
_GONE = UserRef(handle="", display_name="")
_HOUR = timedelta(hours=1)

# The admin status page counts truncated per-deed checks over this period (SEC5-M6).
TRUNCATED_CHECKS_PERIOD = timedelta(days=7)


class _Named(Protocol):
    @property
    def handle(self) -> str: ...
    @property
    def display_name(self) -> str: ...


def _label(labels: Mapping[str, str], code: str) -> str:
    return labels.get(code) or code.replace("_", " ").capitalize()


def _ref(people: Mapping[uuid.UUID, _Named], user_id: uuid.UUID | None) -> UserRef:
    summary = people.get(user_id) if user_id is not None else None
    if summary is None:
        return _GONE
    return UserRef(handle=summary.handle, display_name=summary.display_name)


def _count(n: int, word: str) -> str:
    """``1 day``, ``3 days``."""
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _times(n: int) -> str:
    return "once" if n == 1 else f"{n} times"


def _names(names: Sequence[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


# --------------------------------------------------- #1 options, #7 status


def options(config: FraudConfig) -> FraudOptions:
    return FraudOptions(
        types=[
            CaseTypeOption(
                id=t.value,
                label=_label(TYPE_LABELS, t.value),
                enabled=config.enabled(t.value),
                holds_on_open=config.holds_on_open(t.value),
            )
            for t in FlagCaseType
        ],
        statuses=[s.value for s in FlagCaseStatus],
        outcomes=[o.value for o in FlagCaseOutcome],
        deed_outcomes=[o.value for o in DeedOutcome],
        note_min_chars=config.note_min_chars,
        note_max_chars=config.note_max_chars,
        override_reason_codes=[
            ReasonCodeOption(id=c, label=_label(REASON_CODE_LABELS, c))
            for c in config.override_reason_codes
        ],
        override_max_points=config.override_max_points,
        override_second_admin=SecondAdminRule(
            max_cut_points=config.override_second_admin.max_cut_points,
            cut_to_zero=config.override_second_admin.cut_to_zero,
            expire_days=config.override_second_admin.expire_days,
        ),
        explanation_reply_days=config.explanation.reply_days,
        appeal_window_days=config.appeal.window_days,
    )


async def status(
    session: AsyncSession,
    admin_id: uuid.UUID,
    *,
    config: FraudConfig,
    config_version: int,
    now: datetime,
) -> FraudStatus:
    """Detector switches and last runs, and the open (and parked) cases this admin may see."""
    names = [d.name for d in DETECTORS]
    names += sorted(k for k in config.detectors_enabled if k not in names)
    detectors: list[DetectorStatus] = []
    for name in names:
        run = await session.scalar(
            select(FraudDetectorRun)
            .where(
                FraudDetectorRun.detector == name,
                FraudDetectorRun.trigger != DetectorRunTrigger.EVENT,
            )
            .order_by(FraudDetectorRun.started_at.desc(), FraudDetectorRun.id.desc())
            .limit(1)
        )
        truncated = await session.scalar(
            select(func.count())
            .select_from(FraudDetectorRun)
            .where(
                FraudDetectorRun.detector == name,
                FraudDetectorRun.trigger == DetectorRunTrigger.EVENT,
                FraudDetectorRun.status == DetectorRunStatus.TRUNCATED,
                FraudDetectorRun.started_at >= now - TRUNCATED_CHECKS_PERIOD,
            )
        )
        detectors.append(
            DetectorStatus(
                detector=name,
                enabled=config.enabled(name),
                last_run=None
                if run is None
                else DetectorRunOut(
                    status=run.status.value,
                    started_at=run.started_at,
                    finished_at=run.finished_at,
                    edges_scanned=run.edges_scanned,
                    cases_opened=run.cases_opened,
                ),
                truncated_checks_7d=int(truncated or 0),
            )
        )
    visible = await _coi.visibility(session, admin_id, config, now)
    rows = await session.execute(
        select(FlagCase.type, func.count())
        .where(FlagCase.status.in_(LIVE), visible.condition())
        .group_by(FlagCase.type)
    )
    by_type = {t.value: 0 for t in FlagCaseType}
    by_type.update({t.value: int(n) for t, n in rows})
    return FraudStatus(
        config_version=config_version,
        detectors=detectors,
        open_cases_by_type=by_type,
        appeals_waiting_for_second_admin=await _appeals.waiting_for_second_admin(
            session, config=config, now=now
        ),
    )


# --------------------------------------------------- rows (#2) and summaries


async def _subjects(
    session: AsyncSession, case_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, set[uuid.UUID]]:
    out: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    ids = set(case_ids)
    if ids:
        for case_id, user_id in await session.execute(
            select(FlagCaseUser.case_id, FlagCaseUser.user_id).where(FlagCaseUser.case_id.in_(ids))
        ):
            out[case_id].add(user_id)
    return out


async def _deeds_of(
    session: AsyncSession, case_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, set[uuid.UUID]]:
    out: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    ids = set(case_ids)
    if ids:
        for case_id, deed_id in await session.execute(
            select(FlagCaseDeed.case_id, FlagCaseDeed.deed_id).where(FlagCaseDeed.case_id.in_(ids))
        ):
            out[case_id].add(deed_id)
    return out


def _summary(
    case: FlagCase,
    subjects: set[uuid.UUID],
    views: Sequence[deeds.DeedView],
    people: Mapping[uuid.UUID, _Named],
) -> str:
    """One line for the queue, from ids, numbers and names only."""

    def name(user_id: uuid.UUID | None) -> str:
        return _ref(people, user_id).display_name or "Someone"

    names = sorted(name(u) for u in subjects)
    # Without any subject's name, a sentence would start with "between" or a lowercase word.
    named = any(_ref(people, u).display_name for u in subjects)
    signal = case.signal or {}
    times = [v.verified_at for v in views if v.verified_at is not None]
    n = case.deed_count
    neutral = f"Unusual pattern: {_count(n, 'deed')}"
    if case.type == FlagCaseType.RECIPROCAL and named:
        days = max(1, math.ceil((max(times) - min(times)) / (24 * _HOUR))) if times else 1
        return f"{_names(names)} recognised each other {_times(n)} in {_count(days, 'day')}"
    if case.type == FlagCaseType.RING and named:
        share = signal.get("internal_share_pct")
        tail = f", {share}% of what they received came from each other" if share else ""
        return f"{_names(names)} recognised each other in a ring ({_count(n, 'deed')}){tail}"
    if case.type == FlagCaseType.CONCENTRATION and views:
        doer = views[0].doer_id
        reporters = [u for u in subjects if u != doer]
        share_bp = signal.get("share_bp")
        month = signal.get("period_month")
        share = f"{int(share_bp) // 100}%" if isinstance(share_bp, int) else "A large share"
        when = f" in {month}" if month else ""
        reporter = name(reporters[0]) if reporters else "one person"
        return f"{share} of {name(doer)}'s leaderboard points{when} came from {reporter}"
    if case.type == FlagCaseType.CONTENT and views:
        return f"A report about {name(views[0].doer_id)} may be spam or empty"
    if not named:
        return neutral
    return f"Unusual pattern between {_names(names)} ({_count(n, 'deed')})"


async def _rows(session: AsyncSession, cases: Sequence[FlagCase]) -> list[CaseRow]:
    ids = [c.id for c in cases]
    subjects = await _subjects(session, ids)
    deed_map = await _deeds_of(session, ids)
    views = await deeds.get_deeds(session, {d for ds in deed_map.values() for d in ds})
    people = await identity.public_summaries(session, {u for us in subjects.values() for u in us})
    # Live, not the row's held_count: a resolved row is frozen (flag_cases_guard), but an
    # overturned appeal still releases its holds afterwards.
    held = dict(
        (
            await session.execute(
                select(PrizeHold.case_id, func.count())
                .where(PrizeHold.case_id.in_(ids), PrizeHold.released_at.is_(None))
                .group_by(PrizeHold.case_id)
            )
        )
        .tuples()
        .all()
    )
    rows: list[CaseRow] = []
    for case in cases:
        users = sorted(
            (_ref(people, u) for u in subjects[case.id]),
            key=lambda r: (r.display_name.lower(), r.handle),
        )
        case_views = sorted(
            (views[d] for d in deed_map[case.id] if d in views),
            key=lambda v: (v.verified_at is None, v.verified_at, str(v.id)),
        )
        rows.append(
            CaseRow(
                case_id=case.id,
                type=case.type.value,
                status=case.status.value,
                outcome=case.outcome.value if case.outcome else None,
                opened_at=case.opened_at,
                updated_at=case.updated_at,
                review_by=case.review_by,
                resolved_at=case.resolved_at,
                users=users[:LIST_USERS],
                user_count=len(users),
                deed_count=case.deed_count,
                held_count=held.get(case.id, 0),
                summary=_summary(case, subjects[case.id], case_views, people),
            )
        )
    return rows


async def list_cases(
    session: AsyncSession,
    admin_id: uuid.UUID,
    *,
    queue: Queue,
    case_type: str | None,
    user_handle: str | None,
    cursor: str | None,
    config: FraudConfig,
    now: datetime,
) -> CasePage:
    visible = await _coi.visibility(session, admin_id, config, now)
    filters = [visible.condition()]
    if case_type is not None:
        filters.append(FlagCase.type == case_type)
    if user_handle is not None:
        user = await identity.get_user_by_handle(session, user_handle.strip().lstrip("@"))
        if user is None:
            raise user_not_found()
        filters.append(
            exists().where(FlagCaseUser.case_id == FlagCase.id, FlagCaseUser.user_id == user.id)
        )

    stmt = select(FlagCase).where(*filters, FlagCase.status == FlagCaseStatus(queue))
    if queue == "resolved":
        if cursor:
            at, last_id = _cursor.decode_desc(cursor, "resolved")
            stmt = stmt.where(tuple_(FlagCase.resolved_at, FlagCase.id) < tuple_(at, last_id))
        stmt = stmt.order_by(FlagCase.resolved_at.desc(), FlagCase.id.desc())
    else:
        if cursor:
            due, opened, last_id = _cursor.decode_live(cursor, queue)
            after = tuple_(FlagCase.opened_at, FlagCase.id) > tuple_(opened, last_id)
            if due is None:
                stmt = stmt.where(FlagCase.review_by.is_(None), after)
            else:
                stmt = stmt.where(
                    or_(
                        FlagCase.review_by > due,
                        and_(FlagCase.review_by == due, after),
                        FlagCase.review_by.is_(None),
                    )
                )
        stmt = stmt.order_by(FlagCase.review_by.asc().nulls_last(), FlagCase.opened_at, FlagCase.id)
    cases = list(await session.scalars(stmt.limit(config.page_size + 1)))
    more = len(cases) > config.page_size
    cases = cases[: config.page_size]

    count_rows = await session.execute(
        select(FlagCase.status, func.count()).where(*filters).group_by(FlagCase.status)
    )
    counts = {s.value: int(n) for s, n in count_rows}

    next_cursor = None
    if more and cases:
        last = cases[-1]
        if queue == "resolved":
            assert last.resolved_at is not None
            next_cursor = _cursor.encode_desc("resolved", last.resolved_at, last.id)
        else:
            next_cursor = _cursor.encode_live(queue, last.review_by, last.opened_at, last.id)
    return CasePage(
        items=await _rows(session, cases),
        counts=CaseCounts(
            open=counts.get("open", 0),
            parked=counts.get("parked", 0),
            resolved=counts.get("resolved", 0),
        ),
        next_cursor=next_cursor,
    )


# --------------------------------------------------- #3 detail


async def _held(session: AsyncSession, deed_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
    ids = set(deed_ids)
    if not ids:
        return set()
    rows = await session.scalars(
        select(PrizeHold.deed_id)
        .where(PrizeHold.deed_id.in_(ids), PrizeHold.released_at.is_(None))
        .distinct()
    )
    return set(rows)


async def _independent(
    session: AsyncSession, views: Iterable[deeds.DeedView], subjects: set[uuid.UUID]
) -> set[uuid.UUID]:
    """Case deeds whose act was also reported (verified) by someone outside the case."""
    by_group: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for view in views:
        if view.same_act_group_id is not None:
            by_group[view.same_act_group_id].append(view.id)
    out: set[uuid.UUID] = set()
    for group_id, members in by_group.items():
        group = await deeds.same_act_group_view(session, group_id)
        if group is None:
            continue
        others = await deeds.get_deeds(session, set(group.verified_deed_ids) - set(members))
        if any(v.reporter_id not in subjects for v in others.values()):
            out.update(members)
    return out


def _count_window(case: FlagCase, config: FraudConfig, now: datetime) -> CountWindow:
    days = (
        config.ring.window_days if case.type == FlagCaseType.RING else config.reciprocal.window_days
    )
    until = case.resolved_at or now
    return CountWindow(since=until - timedelta(days=days), until=until)


async def case_detail(
    session: AsyncSession,
    admin_id: uuid.UUID,
    case_id: uuid.UUID,
    *,
    config: FraudConfig,
    now: datetime,
) -> CaseDetail:
    case = await _actions.visible_case(session, admin_id, case_id, config, now)
    (row,) = await _rows(session, [case])
    subjects = await _actions.subject_ids(session, case.id)
    deed_ids = await _actions.deed_ids(session, case.id)
    views = await deeds.get_deeds(session, deed_ids)
    held = await _held(session, deed_ids)
    outcomes = {
        o.deed_id: o.outcome
        for o in await session.scalars(
            select(FlagCaseDeedOutcome).where(FlagCaseDeedOutcome.case_id == case.id)
        )
    }
    independent = await _independent(session, views.values(), subjects)
    labels = await deeds.category_labels(session)
    decisions = list(
        await session.scalars(
            select(FlagCaseDecision)
            .where(FlagCaseDecision.case_id == case.id)
            .order_by(FlagCaseDecision.created_at, FlagCaseDecision.id)
        )
    )
    explanations = sorted(
        (await _explain.requests_for(session, case.id)).values(),
        key=lambda r: (r.requested_at, str(r.user_id)),
    )
    people = await identity.public_summaries(
        session,
        subjects
        | {v.reporter_id for v in views.values()}
        | {v.doer_id for v in views.values() if v.doer_id is not None}
        | {d.actor_id for d in decisions},
    )

    ordered = sorted(
        views.values(), key=lambda v: (v.verified_at is None, v.verified_at, str(v.id))
    )
    case_deeds = [
        CaseDeed(
            deed_id=v.id,
            reporter=_ref(people, v.reporter_id),
            doer=_ref(people, v.doer_id),
            category_label=labels.get(v.category, v.category),
            area_name=deeds.area_name(v),
            description=v.description,
            verified_at=v.verified_at,
            status=v.status.value,
            counts_for_points=v.counts_for_points,
            held=v.id in held,
            outcome=outcomes[v.id].value if v.id in outcomes else None,
            independent_witness=v.id in independent,
        )
        for v in ordered
    ]

    pairs = Counter((v.reporter_id, v.doer_id) for v in ordered if v.doer_id is not None)
    signals = await deeds.pair_signals(session, pairs.keys())
    edges = [
        CaseEdge(
            from_=_ref(people, r),
            to=_ref(people, d),
            deeds=n,
            shared_device=signals[(r, d)].shared_device,
            shared_network=signals[(r, d)].shared_network,
        )
        for (r, d), n in sorted(pairs.items(), key=lambda p: (-p[1], str(p[0][0]), str(p[0][1])))
    ]

    window = _count_window(case, config, now)
    edges_in_window = await deeds.verified_edges(
        session, since=window.since, until=window.until, user_ids=subjects
    )
    as_doer = Counter(e.doer_id for e in edges_in_window)
    as_reporter = Counter(e.reporter_id for e in edges_in_window)
    other_open: Counter[uuid.UUID] = Counter()
    confirmed: Counter[uuid.UUID] = Counter()
    if subjects:
        for user_id, case_status, outcome in await session.execute(
            select(FlagCaseUser.user_id, FlagCase.status, FlagCase.outcome)
            .join(FlagCase, FlagCase.id == FlagCaseUser.case_id)
            .where(FlagCaseUser.user_id.in_(subjects), FlagCase.id != case.id)
        ):
            if case_status in LIVE:
                other_open[user_id] += 1
            elif outcome == FlagCaseOutcome.CONFIRMED:
                confirmed[user_id] += 1
    users: list[CaseUser] = []
    for user_id in subjects:
        account = await identity.get_user(session, user_id)
        users.append(
            CaseUser(
                user=_ref(people, user_id),
                joined_at=account.created_at if account else case.opened_at,
                verified_as_doer_in_window=as_doer[user_id],
                verified_as_reporter_in_window=as_reporter[user_id],
                other_open_cases=other_open[user_id],
                earlier_confirmed_cases=confirmed[user_id],
            )
        )
    users.sort(key=lambda u: (u.user.display_name.lower(), u.user.handle))

    months: dict[str, datetime] = {}
    for v in ordered:
        if v.verified_at is not None:
            months[v.verified_at.astimezone(config.zone).strftime("%Y-%m")] = month_review_deadline(
                v.verified_at, config.zone
            )
    finalization = [
        # ``fraud`` can't read scoring's finalizations (D78): a month counts as final once its
        # cutoff (month end + 24 h) has passed, as for ``review_by``.
        FinalizationMonth(period_month=m, finalizes_at=at, final=at <= now)
        for m, at in sorted(months.items())
    ]

    return CaseDetail(
        case=CaseHeader(
            **row.model_dump(),
            signal=dict(case.signal or {}),
            detector_config_version=case.detector_config_version,
        ),
        users=users,
        deeds=case_deeds,
        edges=edges,
        decisions=[
            CaseDecision(
                action=d.action.value,
                note=d.note,
                actor=_ref(people, d.actor_id),
                created_at=d.created_at,
            )
            for d in decisions
        ],
        explanations=[
            CaseExplanation(
                user=_ref(people, r.user_id),
                requested_at=r.requested_at,
                reply_due_at=r.reply_due_at,
                reply=r.reply,
                replied_at=r.replied_at,
            )
            for r in explanations
        ],
        finalization=finalization,
        window=window,
    )


# --------------------------------------------------- #4, #5 park and unpark


async def park(
    session: AsyncSession,
    admin_id: uuid.UUID,
    case_id: uuid.UUID,
    note: str,
    *,
    config: FraudConfig,
    now: datetime,
) -> None:
    await _move(session, admin_id, case_id, note, park=True, config=config, now=now)


async def unpark(
    session: AsyncSession,
    admin_id: uuid.UUID,
    case_id: uuid.UUID,
    note: str,
    *,
    config: FraudConfig,
    now: datetime,
) -> None:
    await _move(session, admin_id, case_id, note, park=False, config=config, now=now)


async def _move(
    session: AsyncSession,
    admin_id: uuid.UUID,
    case_id: uuid.UUID,
    note: str,
    *,
    park: bool,
    config: FraudConfig,
    now: datetime,
) -> None:
    text = clean_note(note, config)
    case = await _actions.visible_case(session, admin_id, case_id, config, now, lock=True)
    source, target = (
        (FlagCaseStatus.OPEN, FlagCaseStatus.PARKED)
        if park
        else (FlagCaseStatus.PARKED, FlagCaseStatus.OPEN)
    )
    if case.status != source:
        if park:
            raise Conflict("Only an open case can be parked.", code="case_not_open")
        raise Conflict("Only a parked case can be unparked.", code="case_not_parked")
    _actions.record(
        session,
        case,
        FlagCaseAction.PARK if park else FlagCaseAction.UNPARK,
        actor_id=admin_id,
        note=text,
        now=now,
        old_value={"status": source.value},
        new_value={"status": target.value},
    )
    case.status = target
    case.updated_at = now
    await session.flush()


# --------------------------------------------------- #6 decision


def _deed_actions(
    given: list[DeedAction] | None, case_deeds: set[uuid.UUID]
) -> dict[uuid.UUID, DeedOutcome]:
    """Every case deed exactly once, nothing else (422 ``deed_actions_invalid``)."""
    actions = {a.deed_id: DeedOutcome(a.action) for a in given or []}
    if given is None or len(actions) != len(given) or set(actions) != case_deeds:
        raise Invalid("Choose an outcome for every deed in this case.", code="deed_actions_invalid")
    return actions


async def _release(
    session: AsyncSession, case_id: uuid.UUID, deed_ids: Iterable[uuid.UUID], now: datetime
) -> None:
    ids = set(deed_ids)
    if ids:
        await session.execute(
            update(PrizeHold)
            .where(
                PrizeHold.case_id == case_id,
                PrizeHold.deed_id.in_(ids),
                PrizeHold.released_at.is_(None),
            )
            .values(released_at=now, release_reason=HoldReleaseReason.RELEASED_ON_CONFIRM)
        )


async def decide(
    session: AsyncSession,
    admin_id: uuid.UUID,
    case_id: uuid.UUID,
    data: DecisionIn,
    *,
    config: FraudConfig,
    now: datetime,
) -> None:
    """Confirm or dismiss. The caller commits."""
    note = clean_note(data.note, config)
    case = await _actions.visible_case(session, admin_id, case_id, config, now, lock=True)
    if case.status == FlagCaseStatus.RESOLVED:
        raise _explain.case_resolved()
    subjects = await _actions.subject_ids(session, case.id)
    case_deeds = await _actions.deed_ids(session, case.id)
    confirm = data.outcome == "confirmed"

    actions: dict[uuid.UUID, DeedOutcome] = {}
    adverse: dict[uuid.UUID, list[uuid.UUID]] = {}
    if confirm:
        actions = _deed_actions(data.deeds, case_deeds)
        if all(a == DeedOutcome.RELEASE for a in actions.values()):
            raise Invalid("Every deed is released: dismiss the case instead.", code="use_dismiss")
        views = await deeds.get_deeds(session, case_deeds)
        adverse = _notices.adverse_people(actions, views, subjects)
        await _explain.require_explanations(session, case.id, set(adverse), now)
    elif data.deeds:
        raise Invalid("A dismissal has no per-deed outcomes.", code="deed_actions_invalid")

    held_before = await _held(session, case_deeds)
    decision = _actions.record(
        session,
        case,
        FlagCaseAction.CONFIRM if confirm else FlagCaseAction.DISMISS,
        actor_id=admin_id,
        note=note,
        now=now,
        old_value={"status": case.status.value},
        new_value=(
            {
                "outcome": "confirmed",
                **{o.value: sum(1 for a in actions.values() if a == o) for o in DeedOutcome},
                "notices": len(adverse),
            }
            if confirm
            else {"outcome": "dismissed", "deeds": len(case_deeds)}
        ),
    )
    await session.flush()

    by_outcome: dict[DeedOutcome, list[uuid.UUID]] = {o: [] for o in DeedOutcome}
    for deed_id in sorted(actions):
        by_outcome[actions[deed_id]].append(deed_id)
    if confirm:
        session.add_all(
            FlagCaseDeedOutcome(
                case_id=case.id,
                deed_id=deed_id,
                decision_id=decision.id,
                outcome=outcome,
                created_at=now,
            )
            for deed_id, outcome in sorted(actions.items())
        )
        to_hold = by_outcome[DeedOutcome.EXCLUDE] + by_outcome[DeedOutcome.REJECT]
        await session.execute(
            insert(PrizeHold)
            .values([{"deed_id": d, "case_id": case.id, "held_at": now} for d in sorted(to_hold)])
            .on_conflict_do_nothing(index_elements=[PrizeHold.deed_id, PrizeHold.case_id])
        )
        await _release(session, case.id, by_outcome[DeedOutcome.RELEASE], now)
        _notices.create_case_notices(session, case.id, actions, adverse, config=config, now=now)
    else:
        await _release(session, case.id, case_deeds, now)
    await session.flush()

    held_after = await _held(session, case_deeds)
    held_count = int(
        await session.scalar(
            select(func.count())
            .select_from(PrizeHold)
            .where(PrizeHold.case_id == case.id, PrizeHold.released_at.is_(None))
        )
        or 0
    )
    # The last write to the case: once resolved, the row never changes (flag_cases_guard).
    case.status = FlagCaseStatus.RESOLVED
    case.outcome = FlagCaseOutcome.CONFIRMED if confirm else FlagCaseOutcome.DISMISSED
    case.resolved_at = now
    case.resolved_by = admin_id
    case.held_count = held_count
    case.updated_at = now
    await session.flush()

    for deed_id in sorted(held_after - held_before):
        emit(session, DeedHoldChanged(deed_id=deed_id, held=True, case_id=case.id))
    for deed_id in sorted(held_before - held_after):
        emit(session, DeedHoldChanged(deed_id=deed_id, held=False, case_id=case.id))
    emit(
        session,
        FlagCaseResolved(
            case_id=case.id,
            type=case.type.value,
            outcome="confirmed" if confirm else "dismissed",
            user_ids=sorted(subjects),
            pending_reject_deed_ids=by_outcome[DeedOutcome.REJECT],
            excluded_deed_ids=by_outcome[DeedOutcome.EXCLUDE],
            released_deed_ids=by_outcome[DeedOutcome.RELEASE] if confirm else sorted(case_deeds),
            actor_id=admin_id,
        ),
    )

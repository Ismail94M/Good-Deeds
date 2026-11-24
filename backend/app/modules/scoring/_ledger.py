"""The points ledger: ``reconcile_deed`` (docs/M4-DESIGN.md D48 to D58, §4.1, §4.2).

``reconcile_deed(session, deed_id)`` is the only writer of ``points_ledger``. It computes the
target doer and reporter entries from the deed's current state (read live through
``deeds.service``), the evidence record and the inputs frozen on the deed's first entry, and
writes a reversal and a new entry only where the target differs from the active entry. Every
handler calls it, so handlers may run late, twice or in any order (D49).

Rules:

- **Doer:** a verified, counted report earns ``base`` (category points, or ``min_points`` for
  ``spam_or_empty``) plus the photo bonus, times the pair multiplier, rounded half up, at least
  1 (D53). It counts for prize rank when the deed is prize-rank eligible; reporter rows never do
  (D57).
- **Reporter:** a verified report earns ``reporter_points`` if counted, else
  ``reporter_points_beyond_cap``, times the pair multiplier.
- **Daily caps (D52, PO QA3):** strict order by verification time. The user's deeds that take a
  place on the IST day of verification (``deeds.day_order``: as doer, every report ever verified
  and counted; as reporter, every report ever verified) are ordered by ``verified_at``, ties by
  deed id. The first ``cap`` earn points; the rest get a zero-point ``decision``. A withdrawal
  keeps its place, so it never moves a later report up. The order comes from the deeds, not from
  which handler ran first, so a worker running late or out of order gives the same result: when
  a deed takes a place ahead of one that already has points (a later commit of an earlier
  verification, or a same-act promotion), the later deed's award is reversed in the same
  transaction (``_settle_day``), and the day never holds more than ``cap`` awards.
- **Award-time freeze (D50, D66):** the pair ordinal, multiplier and region are taken from the
  deed's first award (region: first entry) on every later re-score.
- **Window (D56, D51):** content is read as of ``verified_at + withdraw_window_hours`` (later
  edits are undone), and the photo bonus needs photo evidence recorded by then. So a re-score
  after the window, for any reason, can't move points through content or photos.
- Days and months are in the configured zone (Asia/Kolkata) from the server's ``verified_at``,
  never the client's ``occurred_at`` (D58).

Lock order (§4.1): advisory ``points_doer_day:(doer, day)``, then
``points_reporter_day:(reporter, day)``, then reads and writes. The database trigger refuses a
second active entry per (deed, role), so the reversal is flushed before the new entry.

Never imports or reads AI evaluations (D62).
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.db import advisory_xact_lock
from app.core.events import emit
from app.modules.deeds import service as deeds
from app.modules.scoring._evidence import first_photo_ready_at
from app.modules.scoring._limits import PairRole, ScoringConfig, load_scoring
from app.modules.scoring._rules import RuleResult, evaluate
from app.modules.scoring.events import PointsChanged
from app.modules.scoring.models import (
    EvaluationMethod,
    EvaluationMode,
    EvidenceLevel,
    ImpactEvaluation,
    LedgerKind,
    LedgerReason,
    LedgerRole,
    PointsLedger,
)

_ACTIVE_KINDS = (LedgerKind.AWARD, LedgerKind.DECISION)
_REMOVAL_REASONS = {
    deeds.DeedStatus.WITHDRAWN: LedgerReason.WITHDRAWN,
    deeds.DeedStatus.REJECTED: LedgerReason.REJECTED,
    deeds.DeedStatus.UNVERIFIED: LedgerReason.REJECTED,
    deeds.DeedStatus.REMOVED: LedgerReason.REMOVED,
}


@dataclass(frozen=True)
class Target:
    kind: LedgerKind  # award or decision
    points: int
    deed_units: int
    counts_for_prize_rank: bool
    breakdown: dict[str, Any]

    def same_as(self, entry: PointsLedger) -> bool:
        return (self.kind, self.points, self.deed_units, self.counts_for_prize_rank) == (
            entry.kind,
            entry.points,
            entry.deed_units,
            entry.counts_for_prize_rank,
        )


def round_half_up(points: int, percent: int) -> int:
    """``points * percent / 100`` rounded half up, at least 1 (D53)."""
    return max(1, (points * percent + 50) // 100)


def _decision(cap: int) -> Target:
    return Target(
        kind=LedgerKind.DECISION,
        points=0,
        deed_units=0,
        counts_for_prize_rank=False,
        breakdown={"cap": cap},
    )


@dataclass
class _RoleEntries:
    active: PointsLedger | None = None
    # The role has had an award or decision before (standing or since reversed).
    earlier: bool = False
    # The first award of the role: it carries the frozen pair ordinal and multiplier.
    award: PointsLedger | None = None


def _by_role(entries: list[PointsLedger]) -> dict[LedgerRole, _RoleEntries]:
    reversed_ids = {e.reverses_entry_id for e in entries if e.kind == LedgerKind.REVERSAL}
    out = {role: _RoleEntries() for role in LedgerRole}
    for entry in entries:
        if entry.kind == LedgerKind.REVERSAL:
            continue
        slot = out[entry.role]
        slot.earlier = True
        if entry.id not in reversed_ids:
            slot.active = entry
        if entry.kind == LedgerKind.AWARD and slot.award is None:
            slot.award = entry
    return out


async def day_order(
    session: AsyncSession, role: LedgerRole, user_id: uuid.UUID, day: date, zone: tzinfo
) -> list[uuid.UUID]:
    """The user's deeds holding daily-cap places on ``day`` (in ``zone``), in cap order."""
    start = datetime.combine(day, time(), tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time(), tzinfo=zone)
    return await deeds.day_order(session, role=role.value, user_id=user_id, start=start, end=end)


@dataclass(frozen=True)
class _Pair:
    ordinal: int
    percent: int


def _frozen_pair(entry: PointsLedger | None) -> _Pair | None:
    if entry is None or entry.kind != LedgerKind.AWARD:
        return None
    return _Pair(int(entry.breakdown["pair_ordinal"]), int(entry.breakdown["pair_percent"]))


class _Reconciler:
    def __init__(
        self,
        session: AsyncSession,
        inputs: deeds.ScoringInputs,
        config: ScoringConfig,
        config_version: int,
        source_event_id: uuid.UUID | None,
    ) -> None:
        assert inputs.doer_id is not None and inputs.verified_at is not None
        self.session = session
        self.inputs = inputs
        self.doer_id: uuid.UUID = inputs.doer_id
        self.verified_at = inputs.verified_at
        self.config = config
        self.version = config_version
        self.source_event_id = source_event_id
        self.day = inputs.verified_at.astimezone(config.zone).date()
        self.month = self.day.replace(day=1)
        self._ordinal: int | None = None

    async def _pair(self, role: PairRole, frozen: PointsLedger | None) -> _Pair:
        pair = _frozen_pair(frozen)
        if pair is not None:
            return pair
        if self._ordinal is None:
            self._ordinal = max(
                1,
                await deeds.pair_ordinal(
                    self.session, self.doer_id, self.inputs.reporter_id, self.verified_at
                ),
            )
        return _Pair(self._ordinal, self.config.pair_percent(role, self._ordinal))

    async def _capped(self, role: LedgerRole, user_id: uuid.UUID, cap: int) -> bool:
        """Whether the deed is past the first ``cap`` places of the user's day (strict order)."""
        order = await day_order(self.session, role, user_id, self.day, self.config.zone)
        place = order.index(self.inputs.id) if self.inputs.id in order else len(order)
        return place >= cap

    async def rules(self, window_end_hours: int) -> tuple[RuleResult, int, EvidenceLevel]:
        """Rule result, photo bonus and evidence level, from content and evidence as of the end
        of the withdrawal window."""
        # The window is half-open, like withdrawal (``now < verified_at + window``): an edit or
        # photo at exactly the window end is already outside it. Timestamps have microsecond
        # precision, so "as of one microsecond before the end" is exact.
        window_end = self.verified_at + timedelta(hours=window_end_hours)
        content = await deeds.content_as_of(
            self.session, self.inputs.id, window_end - timedelta(microseconds=1)
        )
        assert content is not None
        result = evaluate(content.category, content.description, self.config)
        photo_at = await first_photo_ready_at(self.session, self.inputs.id)
        has_photo = photo_at is not None and photo_at < window_end
        bonus = self.config.photo_bonus if has_photo else 0
        level = EvidenceLevel.PHOTO if has_photo else EvidenceLevel.TEXT_ONLY
        if self.inputs.same_act_group_id is not None:
            group = await deeds.same_act_group_view(self.session, self.inputs.same_act_group_id)
            if group is not None and group.distinct_reporters >= 2:
                level = EvidenceLevel.CORROBORATED
        return result, bonus, level

    async def doer_target(
        self, entries: _RoleEntries, rules: tuple[RuleResult, int, EvidenceLevel] | None
    ) -> Target | None:
        if rules is None:
            return None
        if await self._capped(LedgerRole.DOER, self.doer_id, self.config.doer_daily_cap):
            return _decision(self.config.doer_daily_cap)
        pair = await self._pair("doer", entries.award)
        result, bonus, _ = rules
        return Target(
            kind=LedgerKind.AWARD,
            points=round_half_up(result.base + bonus, pair.percent),
            deed_units=1,
            counts_for_prize_rank=self.inputs.prize_rank_eligible,
            breakdown={
                "base": result.base,
                "text_check": result.text_check.value,
                "photo_bonus": bonus,
                "pair_ordinal": pair.ordinal,
                "pair_percent": pair.percent,
                "cap": None,
            },
        )

    async def reporter_target(self, entries: _RoleEntries) -> Target | None:
        if self.inputs.status != deeds.DeedStatus.VERIFIED:
            return None
        cap = self.config.reporter_daily_cap
        if await self._capped(LedgerRole.REPORTER, self.inputs.reporter_id, cap):
            return _decision(cap)
        pair = await self._pair("reporter", entries.award)
        counted = self.inputs.counts_for_points
        base = self.config.reporter_points if counted else self.config.reporter_points_beyond_cap
        return Target(
            kind=LedgerKind.AWARD,
            points=round_half_up(base, pair.percent),
            deed_units=0,
            counts_for_prize_rank=False,
            breakdown={
                "base": base,
                "counted": int(counted),
                "pair_ordinal": pair.ordinal,
                "pair_percent": pair.percent,
                "cap": None,
            },
        )

    def _removal_reason(self, role: LedgerRole) -> LedgerReason:
        reason = _REMOVAL_REASONS.get(self.inputs.status)
        if reason is not None:
            return reason
        # Still verified, so only the doer can lose its entry: no longer counted.
        return LedgerReason.COUNTS_FOR_POINTS_LOST

    @staticmethod
    def _change_reason(
        role: LedgerRole,
        target: Target,
        entries: _RoleEntries,
        hint: LedgerReason | None,
    ) -> LedgerReason:
        if target.kind == LedgerKind.DECISION:
            return (
                LedgerReason.DOER_DAILY_CAP
                if role == LedgerRole.DOER
                else LedgerReason.REPORTER_DAILY_CAP
            )
        old = entries.active
        if old is None:
            # A first entry, or a re-award after the entry was lost (only a promotion does that).
            if entries.earlier or hint == LedgerReason.PROMOTED:
                return LedgerReason.PROMOTED
            return LedgerReason.VERIFIED
        if old.kind == LedgerKind.DECISION:
            return LedgerReason.PROMOTED  # back inside the daily cap
        if old.counts_for_prize_rank != target.counts_for_prize_rank:
            return LedgerReason.PRIZE_RANK_CHANGED
        if old.breakdown.get("counted") != target.breakdown.get("counted"):
            return LedgerReason.PROMOTED
        if old.breakdown.get("photo_bonus") != target.breakdown.get("photo_bonus"):
            return LedgerReason.PHOTO_EVIDENCE
        return LedgerReason.EDITED

    def _row(
        self,
        role: LedgerRole,
        *,
        kind: LedgerKind,
        points: int,
        deed_units: int,
        counts_for_prize_rank: bool,
        region_id: int,
        reason: LedgerReason,
        breakdown: dict[str, Any],
        reverses: uuid.UUID | None = None,
        evaluation_id: uuid.UUID | None = None,
    ) -> PointsLedger:
        doer_row = role == LedgerRole.DOER
        return PointsLedger(
            id=uuid.uuid4(),
            user_id=self.doer_id if doer_row else self.inputs.reporter_id,
            deed_id=self.inputs.id,
            role=role,
            kind=kind,
            points=points,
            deed_units=deed_units,
            counts_for_prize_rank=counts_for_prize_rank,
            region_id=region_id,
            reason=reason,
            breakdown=breakdown,
            counterpart_user_id=self.inputs.reporter_id if doer_row else self.doer_id,
            reverses_entry_id=reverses,
            verified_at=self.verified_at,
            earned_on=self.day,
            period_month=self.month,
            scoring_config_version=self.version,
            evaluation_id=evaluation_id,
            source_event_id=self.source_event_id,
        )

    async def apply(
        self,
        role: LedgerRole,
        entries: _RoleEntries,
        target: Target | None,
        region_id: int,
        hint: LedgerReason | None,
        evaluation: ImpactEvaluation | None = None,
    ) -> list[PointsLedger]:
        old = entries.active
        if old is None and target is None:
            return []
        if old is not None and target is not None and target.same_as(old):
            return []
        reason = (
            self._removal_reason(role)
            if target is None
            else self._change_reason(role, target, entries, hint)
        )
        written: list[PointsLedger] = []
        if old is not None:
            reversal = self._row(
                role,
                kind=LedgerKind.REVERSAL,
                points=-old.points,
                deed_units=-old.deed_units,
                counts_for_prize_rank=old.counts_for_prize_rank,
                region_id=old.region_id,
                reason=reason,
                breakdown=dict(old.breakdown),
                reverses=old.id,
            )
            # Same dates as the reversed row (the trigger checks them; D58).
            reversal.verified_at, reversal.earned_on = old.verified_at, old.earned_on
            reversal.period_month = old.period_month
            self.session.add(reversal)
            # The trigger refuses the new entry while the old one is still active.
            await self.session.flush()
            written.append(reversal)
        if target is not None:
            if evaluation is not None:
                self.session.add(evaluation)
                await self.session.flush()
            entry = self._row(
                role,
                kind=target.kind,
                points=target.points,
                deed_units=target.deed_units,
                counts_for_prize_rank=target.counts_for_prize_rank,
                region_id=region_id,
                reason=reason,
                breakdown=target.breakdown,
                evaluation_id=None if evaluation is None else evaluation.id,
            )
            self.session.add(entry)
            await self.session.flush()
            written.append(entry)
        delta = (0 if target is None else target.points) - (0 if old is None else old.points)
        emit(
            self.session,
            PointsChanged(
                user_id=written[0].user_id,
                deed_id=self.inputs.id,
                role=role.value,
                delta=delta,
                reason=reason.value,
            ),
        )
        return written


def _evaluation(
    deed_id: uuid.UUID,
    rules: tuple[RuleResult, int, EvidenceLevel],
    config_version: int,
) -> ImpactEvaluation:
    result, bonus, level = rules
    return ImpactEvaluation(
        id=uuid.uuid4(),
        deed_id=deed_id,
        method=EvaluationMethod.RULE_BASED,
        mode=EvaluationMode.LIVE,
        rule_points=result.base + bonus,
        text_check=result.text_check,
        evidence_level=level,
        evidence_cap_applied=False,
        human_review_required=result.needs_review,
        scoring_config_version=config_version,
    )


async def reconcile_deed(
    session: AsyncSession,
    deed_id: uuid.UUID,
    *,
    hint: LedgerReason | None = None,
    source_event_id: uuid.UUID | None = None,
) -> list[PointsLedger]:
    """Bring the deed's ledger entries in line with its current state, then settle the daily
    caps of its doer-day and reporter-day (PO QA3). Returns the rows written (none when nothing
    changed). ``hint`` only names the reason of a first award (``promoted`` for a report promoted
    after a withdrawal). The caller commits."""
    inputs = await deeds.scoring_inputs(session, deed_id)
    if inputs is None or inputs.doer_id is None or inputs.verified_at is None:
        return []  # never verified: nothing was ever scored
    version, config = await load_scoring(session)
    day = inputs.verified_at.astimezone(config.zone).date()
    # Lock keys use doer, reporter and verified_at, which never change once a deed is verified.
    await advisory_xact_lock(session, "points_doer_day", str(inputs.doer_id), day.isoformat())
    await advisory_xact_lock(
        session, "points_reporter_day", str(inputs.reporter_id), day.isoformat()
    )
    written = await _reconcile_locked(
        session, deed_id, set(LedgerRole), version, config, hint, source_event_id
    )
    # Holding both day locks: the doer-day lock covers every doer row of that day and the
    # reporter-day lock every reporter row of that day, so the settling needs no other lock.
    written += await _settle_day(
        session, LedgerRole.DOER, inputs.doer_id, day, deed_id, version, config, source_event_id
    )
    written += await _settle_day(
        session,
        LedgerRole.REPORTER,
        inputs.reporter_id,
        day,
        deed_id,
        version,
        config,
        source_event_id,
    )
    return written


async def _settle_day(
    session: AsyncSession,
    role: LedgerRole,
    user_id: uuid.UUID,
    day: date,
    skip: uuid.UUID,
    version: int,
    config: ScoringConfig,
    source_event_id: uuid.UUID | None,
) -> list[PointsLedger]:
    """Re-score, for ``role`` only, the user's other deeds of ``day`` whose active entry is on
    the wrong side of the cap: an award past the first ``cap`` places, or a decision inside them.
    Deeds without an active entry are left to their own handlers. Earlier places are settled
    first, so a reversal is written before the award that replaces it."""
    cap = config.doer_daily_cap if role == LedgerRole.DOER else config.reporter_daily_cap
    order = await day_order(session, role, user_id, day, config.zone)
    others = [deed for deed in order if deed != skip]
    if not others:
        return []
    reversal = aliased(PointsLedger)
    actives = await session.scalars(
        select(PointsLedger)
        .where(
            PointsLedger.deed_id.in_(others),
            PointsLedger.role == role,
            PointsLedger.kind.in_(_ACTIVE_KINDS),
            ~exists().where(reversal.reverses_entry_id == PointsLedger.id),
        )
        .execution_options(populate_existing=True)
    )
    active_kind = {entry.deed_id: entry.kind for entry in actives}
    wrong = [
        deed
        for place, deed in enumerate(order)
        if deed in active_kind and (active_kind[deed] == LedgerKind.AWARD) != (place < cap)
    ]
    # Bumps (awards past the cap) first, so the day never holds more than ``cap`` awards.
    wrong.sort(key=lambda deed: active_kind[deed] != LedgerKind.AWARD)
    written: list[PointsLedger] = []
    for deed in wrong:
        written += await _reconcile_locked(
            session, deed, {role}, version, config, None, source_event_id
        )
    return written


async def _reconcile_locked(
    session: AsyncSession,
    deed_id: uuid.UUID,
    roles: set[LedgerRole],
    version: int,
    config: ScoringConfig,
    hint: LedgerReason | None,
    source_event_id: uuid.UUID | None,
) -> list[PointsLedger]:
    """Reconcile ``roles`` of one deed. The caller holds the day locks of those roles. The deed
    is read after the locks, so a withdrawal committed while waiting is seen."""
    inputs = await deeds.scoring_inputs(session, deed_id)
    if inputs is None or inputs.doer_id is None or inputs.verified_at is None:
        return []
    r = _Reconciler(session, inputs, config, version, source_event_id)
    entries = list(
        await session.scalars(
            select(PointsLedger)
            .where(PointsLedger.deed_id == deed_id)
            .order_by(PointsLedger.created_at, PointsLedger.id)
            .execution_options(populate_existing=True)
        )
    )
    by_role = _by_role(entries)
    doer_counts = inputs.status == deeds.DeedStatus.VERIFIED and inputs.counts_for_points
    rules = None
    if LedgerRole.DOER in roles and doer_counts:
        rules = await r.rules(await deeds.withdraw_window_hours(session))
    doer_target = (
        await r.doer_target(by_role[LedgerRole.DOER], rules) if LedgerRole.DOER in roles else None
    )
    reporter_target = (
        await r.reporter_target(by_role[LedgerRole.REPORTER])
        if LedgerRole.REPORTER in roles
        else None
    )

    if entries:
        region_id = entries[0].region_id  # frozen at the first entry (D66)
    elif doer_target is None and reporter_target is None:
        return []
    else:
        region = await deeds.deed_region(session, deed_id)
        assert region is not None
        region_id = region.id

    written: list[PointsLedger] = []
    if LedgerRole.DOER in roles:
        # One rule-based evaluation per new doer entry (D54), linked from that entry.
        doer_active = by_role[LedgerRole.DOER].active
        evaluation = None
        if (
            rules is not None
            and doer_target is not None
            and (doer_active is None or not doer_target.same_as(doer_active))
        ):
            evaluation = _evaluation(deed_id, rules, version)
        written += await r.apply(
            LedgerRole.DOER, by_role[LedgerRole.DOER], doer_target, region_id, hint, evaluation
        )
    if LedgerRole.REPORTER in roles:
        written += await r.apply(
            LedgerRole.REPORTER, by_role[LedgerRole.REPORTER], reporter_target, region_id, hint
        )
    return written

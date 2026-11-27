"""The scoring read endpoints (docs/M4-DESIGN.md §3.3 #1 to #10, §3.4).

Points leave this file only in the owner's shapes (#2, #3, #5) and the admin-only shapes (#9,
#10). Public shapes (#4, #6, #8) carry tier, badge and title totals, ranks and deed counts only
(D69). Non-owners and pending deeds get 404 on #5; an admin who did or reported a deed, or who
has a verified deed with either person inside ``fraud.coi_lookback_days``, gets 404 on #9 (the
conflict rule, M5 D90).

Final boards (M5 Q72): a row whose person's title for that board and month was revoked shows as
"Unavailable account", like a suspended account (SEC3 L6); its place is unchanged and the title
is gone. The featured-city board of a final month does the same for a person with any title of
that month revoked.
"""

import re
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import ColumnElement, and_, exists, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.errors import AppError, NotFound
from app.core.runtime_config import load_version
from app.modules.deeds import service as deeds
from app.modules.fraud.service import is_held, override_rules
from app.modules.identity import service as identity
from app.modules.scoring import _badges, _coi, _cursor, _finalize, _labels, _leaderboard
from app.modules.scoring._limits import LeaderboardConfig, Tier, load_leaderboard, load_scoring
from app.modules.scoring.models import (
    HeroTitle,
    ImpactEvaluation,
    LeaderboardEntry,
    LeaderboardSnapshot,
    LedgerKind,
    LedgerReason,
    LedgerRole,
    MonthFinalization,
    PointsLedger,
    ScoreOverride,
    UserBadge,
)
from app.modules.scoring.schemas import (
    AdminDeedScoring,
    AdminEvaluation,
    AdminLedgerEntry,
    AdminMonth,
    BadgeOption,
    BadgeOut,
    BoardRegion,
    BoardTitle,
    DeedPoints,
    FeaturedCityBoard,
    FeaturedCityRef,
    FeaturedRow,
    Heroes,
    HeroMonth,
    Leaderboard,
    LeaderboardRow,
    LedgerEntryOut,
    LedgerPage,
    LevelOption,
    MyBoard,
    MyScoring,
    OverrideOut,
    PublicScoring,
    RegionOption,
    RegionRef,
    ReporterToday,
    ScoringOptions,
    TierOption,
    TierOut,
    TitleOut,
    UserRank,
    UserRanks,
    UserSummary,
)

_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
MAX_REGION_RESULTS = 50


# Months and years the API accepts (SEC3 L1): wide enough for any real board, and far from
# ``date``'s limit, so "the month after" never overflows into a server error.
MIN_YEAR, MAX_YEAR = 2000, 2100


def parse_month(value: str) -> date:
    match = _MONTH.match(value)
    if match is not None:
        year, month = int(match.group(1)), int(match.group(2))
        if MIN_YEAR <= year <= MAX_YEAR and 1 <= month <= 12:
            return date(year, month, 1)
    raise AppError("Please choose a month as YYYY-MM.", code="invalid_month")


def _deed_not_found() -> NotFound:
    return NotFound("We couldn't find that deed.", code="deed_not_found")


def _board_not_found() -> NotFound:
    return NotFound("We couldn't find that leaderboard.", code="board_not_found")


async def _now_month(session: AsyncSession, now: datetime) -> date:
    _, scoring = await load_scoring(session)
    return _leaderboard.month_of(now, scoring.zone)


async def _summaries(
    session: AsyncSession, user_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, UserSummary]:
    found = await identity.public_summaries(session, user_ids)
    return {
        uid: UserSummary(handle=s.handle, display_name=s.display_name) for uid, s in found.items()
    }


UNAVAILABLE_NAME = "Unavailable account"


async def _board_summaries(
    session: AsyncSession, user_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, UserSummary]:
    """Names for boards and title lists: active accounts by name, any other account (suspended
    or deleted after its month was finalized) as a placeholder, so final results stay as they
    were (PO QA2) without showing who it was (SEC3 L6)."""
    ids = set(user_ids)
    active = await identity.active_user_ids(session, ids)
    found = await _summaries(session, active)
    hidden = UserSummary(handle=None, display_name=UNAVAILABLE_NAME)
    return {uid: found.get(uid, hidden) for uid in ids}


async def _revoked_title_users(
    session: AsyncSession, period: date, region_id: int | None
) -> set[uuid.UUID]:
    """People with a revoked title in the month (for one region, or any), M5 Q72."""
    stmt = select(HeroTitle.user_id).where(
        HeroTitle.period_month == period, HeroTitle.revoked_at.is_not(None)
    )
    if region_id is not None:
        stmt = stmt.where(HeroTitle.region_id == region_id)
    return set(await session.scalars(stmt))


def _hide(people: dict[uuid.UUID, UserSummary], user_ids: set[uuid.UUID]) -> None:
    for uid in user_ids & people.keys():
        people[uid] = UserSummary(handle=None, display_name=UNAVAILABLE_NAME)


def _tier_out(tier: Tier) -> TierOut:
    return TierOut(slug=tier.slug, label=tier.label)


# --------------------------------------------------- #1 options


async def options(session: AsyncSession) -> ScoringOptions:
    _, scoring = await load_scoring(session)
    _, config = await load_leaderboard(session)
    return ScoringOptions(
        tiers=[
            TierOption(slug=t.slug, label=t.label, min_points=t.min_points) for t in config.tiers
        ],
        badges=[
            BadgeOption(slug=b.slug, label=b.label, description=_labels.badge_description(b))
            for b in config.badges
        ],
        levels=[
            LevelOption(
                kind=level.kind,
                enabled=level.enabled,
                hero_min_deeds=level.min_counted_deeds,
                hero_min_reporters=level.min_distinct_reporters,
                titled_places=sorted({p for t in level.titles for p in t.places}),
            )
            for level in config.levels
        ],
        withdraw_window_hours=await deeds.withdraw_window_hours(session),
        doer_daily_cap=scoring.doer_daily_cap,
        reporter_daily_cap=scoring.reporter_daily_cap,
        reason_labels=dict(_labels.REASON_LABELS),
    )


# --------------------------------------------------- titles, badges (#2, #4, #6, #8)


class _TitleLabels:
    """Title labels in the config version each title was awarded with."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._configs: dict[int, LeaderboardConfig] = {}

    async def label(self, title: HeroTitle, region_name: str) -> str:
        config = self._configs.get(title.config_version)
        if config is None:
            try:
                config = await load_version(self.session, LeaderboardConfig, title.config_version)
            except (LookupError, ValueError):
                _, config = await load_leaderboard(self.session)
            self._configs[title.config_version] = config
        return _labels.title_label(
            config, title.level_kind.value, title.title_key, region_name, title.period_month
        )


async def _user_titles(session: AsyncSession, user_id: uuid.UUID) -> list[TitleOut]:
    titles = list(
        await session.scalars(
            select(HeroTitle)
            .where(HeroTitle.user_id == user_id, HeroTitle.revoked_at.is_(None))
            .order_by(HeroTitle.period_month.desc(), HeroTitle.place, HeroTitle.region_id)
        )
    )
    regions = await deeds.regions_by_id(session, {t.region_id for t in titles})
    labels = _TitleLabels(session)
    out: list[TitleOut] = []
    for t in titles:
        name = regions[t.region_id].name if t.region_id in regions else ""
        out.append(
            TitleOut(
                label=await labels.label(t, name),
                period_month=_labels.month_param(t.period_month),
                region_name=name,
                place=t.place,
                level=t.level_kind.value,
            )
        )
    return out


async def _user_badges(session: AsyncSession, user_id: uuid.UUID) -> list[BadgeOut]:
    _, config = await load_leaderboard(session)
    labels = {b.slug: b.label for b in config.badges}
    slugs = await session.scalars(
        select(UserBadge.badge_slug)
        .where(UserBadge.user_id == user_id, UserBadge.revoked_at.is_(None))
        .order_by(UserBadge.awarded_at, UserBadge.badge_slug)
    )
    return [BadgeOut(slug=s, label=labels.get(s, s)) for s in slugs]


async def _public(session: AsyncSession, user_id: uuid.UUID, now: datetime) -> PublicScoring:
    tiers = await _badges.tiers_for(session, [user_id], now=now)
    badges = await _user_badges(session, user_id)
    titles = await _user_titles(session, user_id)
    return PublicScoring(
        tier=_tier_out(tiers[user_id]),
        badges_total=len(badges),
        titles_total=len(titles),
        badges=badges,
        titles=titles,
    )


# --------------------------------------------------- #4 public


async def public_scoring(
    session: AsyncSession, handle: str, *, now: datetime | None = None
) -> PublicScoring:
    user = await identity.get_user_by_handle(session, handle)
    if user is None or user.status != "active":
        raise NotFound("We couldn't find that person.", code="user_not_found")
    return await _public(session, user.id, now or datetime.now(UTC))


# --------------------------------------------------- #2 me


async def my_scoring(
    session: AsyncSession, user_id: uuid.UUID, *, now: datetime | None = None
) -> MyScoring:
    now = now or datetime.now(UTC)
    public = await _public(session, user_id, now)
    cutoff = await _badges.final_before(session, now)
    totals = await session.execute(
        select(
            PointsLedger.role,
            func.sum(PointsLedger.points),
            func.sum(PointsLedger.points).filter(PointsLedger.verified_at > cutoff),
        )
        .where(PointsLedger.user_id == user_id)
        .group_by(PointsLedger.role)
    )
    points = {LedgerRole.DOER: 0, LedgerRole.REPORTER: 0}
    provisional = 0
    for role, total, pending in totals:
        points[role] = int(total or 0)
        provisional += int(pending or 0)

    _, scoring = await load_scoring(session)
    current = _leaderboard.month_of(now, scoring.zone)
    entries = await _leaderboard.user_board_entries(
        session, user_id, [current, _leaderboard.previous_month(current)]
    )
    regions = await deeds.regions_by_id(session, {s.region_id for s, _ in entries})
    # Boards of a disabled level (e.g. City while only Area is open) are not shown: their reads
    # return 404, and old snapshots of them may still exist.
    _, board_config = await load_leaderboard(session)
    open_kinds = {level.kind for level in board_config.levels if level.enabled}
    boards = [
        MyBoard(
            region=RegionRef(
                id=r.id,
                code=r.code,
                name=r.name,
                kind=r.kind.value,
            ),
            period_month=_labels.month_param(snapshot.period_month),
            rank=entry.rank,
            deed_count=entry.deed_count,
            final=snapshot.final,
        )
        for snapshot, entry in entries
        if (r := regions.get(snapshot.region_id)) is not None and r.kind.value in open_kinds
    ]

    today = now.astimezone(scoring.zone).date()
    used = await session.scalar(
        select(func.count(func.distinct(PointsLedger.deed_id))).where(
            PointsLedger.user_id == user_id,
            PointsLedger.role == LedgerRole.REPORTER,
            PointsLedger.earned_on == today,
            PointsLedger.kind.in_((LedgerKind.AWARD, LedgerKind.DECISION)),
        )
    )
    cap = scoring.reporter_daily_cap
    return MyScoring(
        **public.model_dump(),
        doer_points=points[LedgerRole.DOER],
        reporter_points=points[LedgerRole.REPORTER],
        total_points=points[LedgerRole.DOER] + points[LedgerRole.REPORTER],
        provisional_points=provisional,
        boards=boards,
        reporter_today=ReporterToday(used=min(int(used or 0), cap), cap=cap),
    )


# --------------------------------------------------- #3 ledger

# M5 D95: a reversal + award pair whose only change is ``counts_for_prize_rank`` (reason
# ``prize_rank_changed``, equal points; what a prize hold or its release writes) is left out of
# the owner's history, so a hold is never visible there. ``reconcile_deed`` writes both rows of
# a pair in one transaction, so they share ``created_at`` (the transaction time).


def _eligibility_only_pair() -> ColumnElement[bool]:
    """True for a ledger row that is one half of such a pair (as a SQL condition)."""
    other = aliased(PointsLedger)
    return and_(
        PointsLedger.reason == LedgerReason.PRIZE_RANK_CHANGED,
        exists().where(
            other.deed_id == PointsLedger.deed_id,
            other.role == PointsLedger.role,
            other.user_id == PointsLedger.user_id,
            other.created_at == PointsLedger.created_at,
            other.reason == LedgerReason.PRIZE_RANK_CHANGED,
            other.kind != PointsLedger.kind,
            other.points == -PointsLedger.points,
        ),
    )


def _eligibility_only_ids(rows: Iterable[PointsLedger]) -> set[uuid.UUID]:
    """The same rule in Python, over one deed's rows: ids of the rows in such pairs."""
    groups: dict[tuple[object, ...], list[PointsLedger]] = defaultdict(list)
    for r in rows:
        if r.reason == LedgerReason.PRIZE_RANK_CHANGED:
            groups[(r.deed_id, r.role, r.user_id, r.created_at)].append(r)
    hidden: set[uuid.UUID] = set()
    for group in groups.values():
        awards = [r for r in group if r.kind != LedgerKind.REVERSAL]
        for reversal in (r for r in group if r.kind == LedgerKind.REVERSAL):
            match = next((a for a in awards if a.points == -reversal.points), None)
            if match is not None:
                awards.remove(match)
                hidden |= {reversal.id, match.id}
    return hidden


async def _entries_out(
    session: AsyncSession, rows: Sequence[PointsLedger], now: datetime
) -> list[tuple[PointsLedger, LedgerEntryOut]]:
    window = timedelta(hours=await deeds.withdraw_window_hours(session))
    views = await deeds.get_deeds(session, {r.deed_id for r in rows})
    categories = await deeds.category_labels(session)
    people = await _summaries(
        session,
        {r.user_id for r in rows} | {r.counterpart_user_id for r in rows if r.counterpart_user_id},
    )
    out: list[tuple[PointsLedger, LedgerEntryOut]] = []
    for r in rows:
        until = r.verified_at + window
        provisional = now < until
        view = views.get(r.deed_id)
        category = view.category if view is not None else ""
        doer_id, reporter_id = (
            (r.user_id, r.counterpart_user_id)
            if r.role == LedgerRole.DOER
            else (r.counterpart_user_id, r.user_id)
        )
        out.append(
            (
                r,
                LedgerEntryOut(
                    id=r.id,
                    created_at=r.created_at,
                    deed_id=r.deed_id,
                    role=r.role.value,
                    kind=r.kind.value,
                    points=r.points,
                    reason=r.reason.value,
                    reason_label=_labels.reason_label(r.reason.value),
                    provisional=provisional,
                    provisional_until=until if provisional else None,
                    category_label=categories.get(category, category),
                    doer=people.get(doer_id) if doer_id else None,
                    reporter=people.get(reporter_id) if reporter_id else None,
                ),
            )
        )
    return out


async def ledger_page(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    role: LedgerRole | None,
    cursor: str | None,
    limit: int | None = None,
    now: datetime | None = None,
) -> LedgerPage:
    now = now or datetime.now(UTC)
    _, config = await load_leaderboard(session)
    size = limit or config.page_size
    stmt = select(PointsLedger).where(PointsLedger.user_id == user_id, ~_eligibility_only_pair())
    if role is not None:
        stmt = stmt.where(PointsLedger.role == role)
    if cursor is not None:
        at, last_id = _cursor.decode_ledger(cursor)
        stmt = stmt.where(tuple_(PointsLedger.created_at, PointsLedger.id) < tuple_(at, last_id))
    rows = list(
        await session.scalars(
            stmt.order_by(PointsLedger.created_at.desc(), PointsLedger.id.desc()).limit(size + 1)
        )
    )
    more = len(rows) > size
    rows = rows[:size]
    items = [entry for _, entry in await _entries_out(session, rows, now)]
    next_cursor = _cursor.encode_ledger(rows[-1].created_at, rows[-1].id) if more else None
    return LedgerPage(items=items, next_cursor=next_cursor)


# --------------------------------------------------- #5 own points on a deed


async def deed_points(
    session: AsyncSession, user_id: uuid.UUID, deed_id: uuid.UUID, *, now: datetime | None = None
) -> DeedPoints:
    """The caller's own entry on a deed they did or reported. 404 for anyone else, for pending
    deeds, and while the deed has no entry for the caller yet."""
    now = now or datetime.now(UTC)
    inputs = await deeds.scoring_inputs(session, deed_id)
    if inputs is None or inputs.doer_id is None or inputs.verified_at is None:
        raise _deed_not_found()
    if user_id == inputs.doer_id:
        role = LedgerRole.DOER
    elif user_id == inputs.reporter_id:
        role = LedgerRole.REPORTER
    else:
        raise _deed_not_found()
    rows = list(
        await session.scalars(
            select(PointsLedger)
            .where(
                PointsLedger.deed_id == deed_id,
                PointsLedger.role == role,
                PointsLedger.user_id == user_id,
            )
            .order_by(PointsLedger.created_at, PointsLedger.id)
        )
    )
    if not rows:
        raise _deed_not_found()
    reversed_ids = {r.reverses_entry_id for r in rows if r.kind == LedgerKind.REVERSAL}
    active = next(
        (r for r in rows if r.kind != LedgerKind.REVERSAL and r.id not in reversed_ids), None
    )
    latest = rows[-1]
    until = rows[0].verified_at + timedelta(hours=await deeds.withdraw_window_hours(session))
    # A hold or its release only flips leaderboard eligibility; the reason shown is the one
    # from before it (M5 D95).
    hidden = _eligibility_only_ids(rows)
    shown = [r for r in rows if r.id not in hidden and r.kind != LedgerKind.REVERSAL]
    source = active if active is not None and active.id not in hidden else None
    reason = (source or (shown[-1] if shown else None) or active or latest).reason.value
    return DeedPoints(
        points=sum(r.points for r in rows),
        reason=reason,
        reason_label=_labels.reason_label(reason),
        capped=active is not None and active.kind == LedgerKind.DECISION,
        provisional_until=until if now < until else None,
    )


# --------------------------------------------------- #6 to #8 leaderboards


async def _board_region(
    session: AsyncSession, code: str | None, config: LeaderboardConfig
) -> deeds.RegionView:
    region = (
        await deeds.region_by_code(session, code) if code else await deeds.default_region(session)
    )
    if region is None:
        raise _board_not_found()
    if not any(level.kind == region.kind.value and level.enabled for level in config.levels):
        raise _board_not_found()
    return region


def _region_out(region: deeds.RegionView) -> BoardRegion:
    return BoardRegion(code=region.code, name=region.name, kind=region.kind.value)


async def _board_titles(
    session: AsyncSession, titles: Sequence[HeroTitle], region_name: str
) -> list[BoardTitle]:
    labels = _TitleLabels(session)
    people = await _board_summaries(session, {t.user_id for t in titles})
    grouped: dict[tuple[int, str], list[HeroTitle]] = defaultdict(list)
    for t in titles:
        grouped[(t.place, t.title_key)].append(t)
    out: list[BoardTitle] = []
    for (place, _key), items in sorted(grouped.items()):
        out.append(
            BoardTitle(
                place=place,
                label=await labels.label(items[0], region_name),
                users=[people[t.user_id] for t in items if t.user_id in people],
            )
        )
    return out


async def leaderboard(
    session: AsyncSession,
    viewer_id: uuid.UUID,
    *,
    region_code: str | None,
    month: str | None,
    cursor: str | None,
    limit: int | None = None,
    now: datetime | None = None,
) -> Leaderboard:
    now = now or datetime.now(UTC)
    _, config = await load_leaderboard(session)
    period = parse_month(month) if month else await _now_month(session, now)
    region = await _board_region(session, region_code, config)
    finalized = await _leaderboard.is_finalized(session, period)
    # A past month with no deeds at all is never finalized by the job; it reads as final.
    finalized = finalized or await _finalize.closed_without_deeds(session, period, now)
    snapshot = await _leaderboard.latest_snapshot(session, region.id, period)
    if finalized and snapshot is not None and not snapshot.final:
        snapshot = None  # finalized with no counted deed here: the final board is empty
    size = limit or config.page_size

    rows: list[tuple[LeaderboardEntry, int]] = []
    me: tuple[LeaderboardEntry, int] | None = None
    more = False
    if snapshot is not None:
        shared = (
            select(
                LeaderboardEntry,
                func.count().over(partition_by=LeaderboardEntry.rank).label("n"),
            )
            .where(LeaderboardEntry.snapshot_id == snapshot.id)
            .subquery()
        )
        entry = select(LeaderboardEntry, shared.c.n).join(
            shared,
            and_(
                shared.c.snapshot_id == LeaderboardEntry.snapshot_id,
                shared.c.user_id == LeaderboardEntry.user_id,
            ),
        )
        page = entry
        if cursor is not None:
            snapshot_id, rank, last_user = _cursor.decode_board(cursor)
            if snapshot_id != snapshot.id:
                raise _cursor.invalid_cursor()
            page = page.where(
                or_(
                    LeaderboardEntry.rank > rank,
                    and_(LeaderboardEntry.rank == rank, LeaderboardEntry.user_id > last_user),
                )
            )
        found = list(
            await session.execute(
                page.order_by(LeaderboardEntry.rank, LeaderboardEntry.user_id).limit(size + 1)
            )
        )
        more = len(found) > size
        rows = [(e, int(n)) for e, n in found[:size]]
        mine = (await session.execute(entry.where(LeaderboardEntry.user_id == viewer_id))).first()
        me = None if mine is None else (mine[0], int(mine[1]))
    elif cursor is not None:
        raise _cursor.invalid_cursor()

    users = {e.user_id for e, _ in rows} | ({me[0].user_id} if me else set())
    people = await _board_summaries(session, users)
    if finalized:
        _hide(people, await _revoked_title_users(session, period, region.id))
    tiers = await _badges.tiers_for(session, users, now=now)

    def _row(e: LeaderboardEntry, n: int) -> LeaderboardRow | None:
        person = people.get(e.user_id)
        if person is None:
            return None
        return LeaderboardRow(
            rank=e.rank,
            shared=n > 1,
            user=person,
            tier=_tier_out(tiers[e.user_id]),
            deed_count=e.deed_count,
        )

    items = [r for e, n in rows if (r := _row(e, n)) is not None]
    titles: list[BoardTitle] = []
    if finalized:
        held = list(
            await session.scalars(
                select(HeroTitle)
                .where(
                    HeroTitle.region_id == region.id,
                    HeroTitle.period_month == period,
                    HeroTitle.revoked_at.is_(None),
                )
                .order_by(HeroTitle.place, HeroTitle.user_id)
            )
        )
        titles = await _board_titles(session, held, region.name)
    last = rows[-1][0] if rows else None
    return Leaderboard(
        region=_region_out(region),
        month=_labels.month_param(period),
        computed_at=None if snapshot is None else snapshot.computed_at,
        final=finalized,
        finalizes_at=None if finalized else await _finalize.cutoff_for(session, period),
        items=items,
        me=None if me is None else _row(*me),
        titles=titles,
        next_cursor=(
            _cursor.encode_board(snapshot.id, last.rank, last.user_id)
            if more and snapshot is not None and last is not None
            else None
        ),
    )


async def user_ranks(
    session: AsyncSession, handle: str, *, now: datetime | None = None
) -> UserRanks:
    """#12 (D76): a person's rank this month at each level, read from the boards' current
    snapshots (final, else newest), so it always matches the leaderboard. A person can be on
    several boards of one level (deeds in several wards); the best one is shown: best rank, then
    more counted deeds, then region name. Disabled levels come back ``enabled: false``.

    Only the board's region is named, never the person's registered area or city."""
    user = await identity.get_user_by_handle(session, handle)
    if user is None or user.status != "active":
        raise NotFound("We couldn't find that person.", code="user_not_found")
    now = now or datetime.now(UTC)
    _, config = await load_leaderboard(session)
    period = await _now_month(session, now)
    finalized = await _leaderboard.is_finalized(session, period)
    finalized = finalized or await _finalize.closed_without_deeds(session, period, now)
    entries = [
        (snapshot, entry)
        for snapshot, entry in await _leaderboard.user_board_entries(session, user.id, [period])
        # As on the board read: once finalized, a region with no final snapshot is empty.
        if snapshot.final or not finalized
    ]
    regions = await deeds.regions_by_id(session, {s.region_id for s, _ in entries})
    best: dict[str, tuple[LeaderboardEntry, deeds.RegionView]] = {}
    for snapshot, entry in entries:
        region = regions.get(snapshot.region_id)
        if region is None:
            continue
        kind = region.kind.value
        current = best.get(kind)
        if current is None or (entry.rank, -entry.deed_count, region.name) < (
            current[0].rank,
            -current[0].deed_count,
            current[1].name,
        ):
            best[kind] = (entry, region)
    shared: dict[tuple[uuid.UUID, int], int] = {}
    if best:
        keys = [(e.snapshot_id, e.rank) for e, _ in best.values()]
        counts = await session.execute(
            select(LeaderboardEntry.snapshot_id, LeaderboardEntry.rank, func.count())
            .where(tuple_(LeaderboardEntry.snapshot_id, LeaderboardEntry.rank).in_(keys))
            .group_by(LeaderboardEntry.snapshot_id, LeaderboardEntry.rank)
        )
        shared = {(sid, rank): int(n) for sid, rank, n in counts}
    levels: list[UserRank] = []
    for level in config.levels:
        found = best.get(level.kind) if level.enabled else None
        levels.append(
            UserRank(
                level=level.kind,
                enabled=level.enabled,
                region=None if found is None else _region_out(found[1]),
                rank=None if found is None else found[0].rank,
                shared=found is not None
                and shared.get((found[0].snapshot_id, found[0].rank), 1) > 1,
            )
        )
    return UserRanks(month=_labels.month_param(period), final=finalized, levels=levels)


async def region_options(
    session: AsyncSession, kind: deeds.RegionKind, q: str | None
) -> list[RegionOption]:
    found = await deeds.regions(session, kind, q, limit=MAX_REGION_RESULTS)
    return [RegionOption(code=r.code, name=r.name) for r in found]


async def featured_city(
    session: AsyncSession,
    *,
    city_key: str | None,
    month: str | None,
    now: datetime | None = None,
) -> FeaturedCityBoard:
    """#11: the featured city's top ``top_n`` members of the month (D72), default the first
    enabled featured city. Unknown or disabled cities are 404."""
    now = now or datetime.now(UTC)
    _, config = await load_leaderboard(session)
    enabled = [c for c in config.featured_cities if c.enabled]
    city = next((c for c in enabled if c.key == city_key), None) if city_key else None
    if city_key is None and enabled:
        city = enabled[0]
    if city is None:
        raise _board_not_found()
    period = parse_month(month) if month else await _now_month(session, now)
    final = await _leaderboard.is_finalized(session, period)
    final = final or await _finalize.closed_without_deeds(session, period, now)
    ranked = await _leaderboard.featured_city_rows(session, period, city, config)
    per_rank: dict[int, int] = defaultdict(int)
    for row in ranked:
        per_rank[row.rank] += 1
    top = ranked[: city.top_n]
    users = {r.user_id for r in top}
    people = await _board_summaries(session, users)
    if final:
        _hide(people, await _revoked_title_users(session, period, None))
    tiers = await _badges.tiers_for(session, users, now=now)
    return FeaturedCityBoard(
        city=FeaturedCityRef(key=city.key, name=city.name),
        cities=[FeaturedCityRef(key=c.key, name=c.name) for c in enabled],
        month=_labels.month_param(period),
        final=final,
        items=[
            FeaturedRow(
                rank=r.rank,
                shared=per_rank[r.rank] > 1,
                user=people[r.user_id],
                tier=_tier_out(tiers[r.user_id]),
                deed_count=r.deed_count,
                points=r.doer_points if city.show_points else None,
            )
            for r in top
        ],
    )


async def heroes(
    session: AsyncSession,
    *,
    region_code: str | None,
    year: int | None,
    now: datetime | None = None,
) -> Heroes:
    """Final titles per finalized month of ``year`` (default: this IST year), newest first. A
    finalized month without a title (nobody qualified) is listed with no titles."""
    now = now or datetime.now(UTC)
    _, config = await load_leaderboard(session)
    region = await _board_region(session, region_code, config)
    year = year or (await _now_month(session, now)).year
    start, end = date(year, 1, 1), date(year + 1, 1, 1)
    months = list(
        await session.scalars(
            select(MonthFinalization.period_month)
            .where(MonthFinalization.period_month >= start, MonthFinalization.period_month < end)
            .order_by(MonthFinalization.period_month.desc())
        )
    )
    titles = list(
        await session.scalars(
            select(HeroTitle)
            .where(
                HeroTitle.region_id == region.id,
                HeroTitle.period_month >= start,
                HeroTitle.period_month < end,
                HeroTitle.revoked_at.is_(None),
            )
            .order_by(HeroTitle.place, HeroTitle.user_id)
        )
    )
    by_month: dict[date, list[HeroTitle]] = defaultdict(list)
    for t in titles:
        by_month[t.period_month].append(t)
    items = [
        HeroMonth(
            period_month=_labels.month_param(m),
            titles=await _board_titles(session, by_month.get(m, []), region.name),
        )
        for m in months
    ]
    return Heroes(region=_region_out(region), items=items)


# --------------------------------------------------- #9, #10 admin


async def admin_deed(
    session: AsyncSession, admin_id: uuid.UUID, deed_id: uuid.UUID, *, now: datetime | None = None
) -> AdminDeedScoring:
    """Ledger rows, evaluations, hold and overrides of one deed. 404 for an unknown deed, and
    for a conflicted admin: one who did or reported it, or has a verified deed with either
    person inside the lookback (D90; nobody reviews their own case)."""
    now = now or datetime.now(UTC)
    inputs = await deeds.scoring_inputs(session, deed_id)
    if inputs is None:
        raise _deed_not_found()
    rules = await override_rules(session)
    people = (inputs.reporter_id, inputs.doer_id)
    if await _coi.conflicted(session, admin_id, people, rules.coi_lookback_days, now):
        raise _deed_not_found()
    rows = list(
        await session.scalars(
            select(PointsLedger)
            .where(PointsLedger.deed_id == deed_id)
            .order_by(PointsLedger.created_at, PointsLedger.id)
        )
    )
    ledger = [
        AdminLedgerEntry(
            **entry.model_dump(),
            breakdown=dict(row.breakdown),
            config_version=row.scoring_config_version,
            reverses_entry_id=row.reverses_entry_id,
        )
        for row, entry in await _entries_out(session, rows, now)
    ]
    evaluations = [
        AdminEvaluation(
            id=e.id,
            method=e.method.value,
            mode=e.mode.value,
            status=e.status.value,
            created_at=e.created_at,
            config_version=e.scoring_config_version,
            rule_points=e.rule_points,
            text_check=None if e.text_check is None else e.text_check.value,
            impact_score=e.impact_score,
            raw_score=e.raw_score,
            sub_scores=e.sub_scores,
            evidence_level=e.evidence_level.value,
            evidence_cap_applied=e.evidence_cap_applied,
            impact_explanation=e.impact_explanation,
            estimated_beneficiaries=e.estimated_beneficiaries,
            evaluation_confidence=(
                None if e.evaluation_confidence is None else float(e.evaluation_confidence)
            ),
            human_review_required=e.human_review_required,
            model_version=e.model_version,
            prompt_version=e.prompt_version,
        )
        for e in await session.scalars(
            select(ImpactEvaluation)
            .where(ImpactEvaluation.deed_id == deed_id)
            .order_by(ImpactEvaluation.created_at, ImpactEvaluation.id)
        )
    ]
    # Every row of a deed carries the month it was earned in (a reversal copies it).
    period = rows[0].period_month if rows else None
    final = period is not None and await session.get(MonthFinalization, period) is not None
    return AdminDeedScoring(
        deed_id=deed_id,
        held=await is_held(session, deed_id),
        period_month=None if period is None else _labels.month_param(period),
        month_final=final,
        ledger=ledger,
        evaluations=evaluations,
        overrides=await overrides_out(session, deed_id),
    )


async def overrides_out(session: AsyncSession, deed_id: uuid.UUID) -> list[OverrideOut]:
    """The deed's override rows, oldest first, with the admin who wrote each."""
    rows = list(
        await session.scalars(
            select(ScoreOverride)
            .where(ScoreOverride.deed_id == deed_id)
            .order_by(ScoreOverride.created_at, ScoreOverride.id)
        )
    )
    actors = await _summaries(session, {r.actor_id for r in rows})
    return [
        OverrideOut(
            id=r.id,
            role=r.role.value,
            points=r.points,
            previous_points=r.previous_points,
            reason_code=r.reason_code,
            note=r.note,
            actor=actors.get(r.actor_id),
            case_id=r.case_id,
            request_id=r.request_id,
            created_at=r.created_at,
        )
        for r in rows
    ]


async def admin_month(session: AsyncSession, month: str) -> AdminMonth:
    period = parse_month(month)
    cutoff = await _finalize.cutoff_for(session, period)
    done = await session.get(MonthFinalization, period)
    finals = await session.scalar(
        select(func.count())
        .select_from(LeaderboardSnapshot)
        .where(LeaderboardSnapshot.period_month == period, LeaderboardSnapshot.final.is_(True))
    )
    waiting = await _finalize.barrier(session, period, cutoff)
    return AdminMonth(
        period_month=_labels.month_param(period),
        cutoff_at=cutoff,
        pending_scoring_events=waiting.scoring,
        pending_fraud_events=waiting.fraud,
        pending_hold_events=waiting.holds,
        finalized_at=None if done is None else done.finalized_at,
        final_snapshots=int(finals or 0),
    )

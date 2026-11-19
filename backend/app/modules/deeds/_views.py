"""Building ``DeedOut`` responses (§3.3). B8 extends this for the read endpoints.

Privacy: coordinates are never included (only ``has_location``), and the doer of a pending
deed is never revealed, not even to the reporter.
"""

import uuid
from collections.abc import Mapping

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.deeds._areas import area_names, display_name
from app.modules.deeds.models import Deed, DeedStatus, SameActGroup
from app.modules.deeds.schemas import DeedOut, InviteOut, SameActOut, UserRef
from app.modules.identity import service as identity

_CHANGEABLE = frozenset({DeedStatus.PENDING, DeedStatus.VERIFIED})


async def deed_out(
    session: AsyncSession,
    deed: Deed,
    viewer_id: uuid.UUID,
    *,
    group: SameActGroup | None = None,
    invite: InviteOut | None = None,
    summaries: Mapping[uuid.UUID, BaseModel] | None = None,
    areas: dict[int, str] | None = None,
) -> DeedOut:
    """``summaries`` and ``areas`` let a caller building a page of deeds prefetch every name in
    one query each; ``summaries`` must contain the reporter and (unless pending) the doer."""
    show_doer = deed.doer_user_id is not None and deed.status != DeedStatus.PENDING
    if summaries is None:
        ids = [deed.reporter_user_id]
        if show_doer and deed.doer_user_id is not None:
            ids.append(deed.doer_user_id)
        summaries = await identity.public_summaries(session, ids)

    def ref(user_id: uuid.UUID | None) -> UserRef | None:
        summary = summaries.get(user_id) if user_id is not None else None
        return None if summary is None else UserRef(**summary.model_dump())

    reporter = ref(deed.reporter_user_id)
    assert reporter is not None
    if group is None and deed.same_act_group_id is not None:
        group = await session.get(SameActGroup, deed.same_act_group_id)
    if areas is None:
        areas = await area_names(session, [deed.area_id])
    is_reporter = viewer_id == deed.reporter_user_id
    changeable = is_reporter and deed.status in _CHANGEABLE
    return DeedOut(
        id=deed.id,
        status=deed.status,
        verification_method=deed.verification_method,
        category=deed.category,
        description=deed.description,
        beneficiaries_count=deed.beneficiaries_count,
        occurred_at=deed.occurred_at,
        reported_at=deed.reported_at,
        verified_at=deed.verified_at,
        withdrawn_at=deed.withdrawn_at,
        last_edited_at=deed.last_edited_at,
        reporter=reporter,
        doer=ref(deed.doer_user_id) if show_doer else None,
        same_act=(
            None
            if group is None
            else SameActOut(
                report_count=group.verified_count, counts_for_points=deed.counts_for_points
            )
        ),
        prize_rank_eligible=deed.prize_rank_eligible,
        has_location=deed.location is not None,
        area_name=display_name(deed.area_id, areas),
        can_edit=changeable,
        can_withdraw=changeable,
        invite=invite if is_reporter else None,
    )

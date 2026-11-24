"""feed top comments

PO decision L1 (docs/M3-DESIGN.md §11, 2026-11-21): comments by a deed's doer and reporter still
count in ``comment_count`` but carry no Top weight. Adds ``feed_posts.top_comment_count`` (live
comments by anyone else) with its CHECK, and backfills it from existing non-deleted comments.
PO decision L3 follow-up (2026-11-21): adds ``feed_comments.author_deleted_at`` (the author's
delete request on a comment moderation removed) with its CHECK.
Top snapshots already stored keep their scores until the next refresh recomputes them.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-21 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def backfill_top_comment_count() -> None:
    """Live comments whose author is neither the doer nor the reporter. Safe to re-run: it sets
    the value rather than adding to it."""
    op.get_bind().execute(
        sa.text(
            "UPDATE feed_posts AS p SET top_comment_count = ("
            "SELECT count(*) FROM feed_comments AS c "
            "WHERE c.deed_id = p.deed_id AND c.deleted_at IS NULL "
            "AND c.author_id <> p.doer_id AND c.author_id <> p.reporter_id)"
        )
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "feed_posts",
        sa.Column("top_comment_count", sa.Integer(), server_default="0", nullable=False),
    )
    backfill_top_comment_count()
    op.create_check_constraint(
        op.f("ck_feed_posts_top_comment_count_valid"),
        "feed_posts",
        "top_comment_count >= 0 AND top_comment_count <= comment_count",
    )
    op.add_column(
        "feed_comments",
        sa.Column("author_deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_feed_comments_author_deleted_needs_deletion"),
        "feed_comments",
        "author_deleted_at IS NULL OR deleted_at IS NOT NULL",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        op.f("ck_feed_comments_author_deleted_needs_deletion"), "feed_comments", type_="check"
    )
    op.drop_column("feed_comments", "author_deleted_at")
    op.drop_constraint(op.f("ck_feed_posts_top_comment_count_valid"), "feed_posts", type_="check")
    op.drop_column("feed_posts", "top_comment_count")

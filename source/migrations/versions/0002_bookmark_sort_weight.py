"""bookmarks.sort_weight for custom ordering

Revision ID: 0002_bookmark_sort_weight
Revises: 0001_initial
Create Date: 2026-09-09
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002_bookmark_sort_weight"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bookmarks",
        sa.Column("sort_weight", sa.Integer(), nullable=False, server_default="0"),
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id, category_id, created_at FROM bookmarks ORDER BY category_id, created_at, id"
        )
    ).fetchall()
    counters: dict = {}
    for row in rows:
        bookmark_id, category_id, _created_at = row
        idx = counters.get(category_id, 0)  # category_id None -> 未分类组
        counters[category_id] = idx + 1
        connection.execute(
            sa.text("UPDATE bookmarks SET sort_weight = :w WHERE id = :id"),
            {"w": idx, "id": bookmark_id},
        )
    op.create_index(
        "ix_bookmarks_sort", "bookmarks", ["deleted_at", "category_id", "sort_weight"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_bookmarks_sort", table_name="bookmarks")
    op.drop_column("bookmarks", "sort_weight")

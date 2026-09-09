"""initial schema: users, app_meta, categories, tags, bookmarks, bookmark_tags, import_jobs

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-08
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def _utc_now_sql() -> sa.TextClause:
    """SQLite CURRENT_TIMESTAMP 即 UTC；与 ORM 的 naive UTC default 属同一比较体系。"""
    return sa.text("(CURRENT_TIMESTAMP)")


def upgrade() -> None:
    # ---- users（单管理员） ----
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("normalized_username", sa.String(length=100), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "session_version", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column(
            "failed_login_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=_utc_now_sql()
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=_utc_now_sql()
        ),
        sa.UniqueConstraint("normalized_username", name="uq_users_normalized_username"),
    )

    # ---- app_meta（全局单行，必须初始化 id=1） ----
    op.create_table(
        "app_meta",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "category_tree_revision", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=_utc_now_sql()
        ),
        sa.CheckConstraint("id = 1", name="ck_app_meta_single_row"),
    )
    op.execute(
        sa.text(
            "INSERT INTO app_meta (id, category_tree_revision, updated_at)"
            " VALUES (1, 1, CURRENT_TIMESTAMP)"
        )
    )

    # ---- categories（自关联树） ----
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("normalized_name", sa.String(length=100), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=_utc_now_sql()
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=_utc_now_sql()
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["categories.id"], ondelete="RESTRICT", name="fk_categories_parent"
        ),
    )
    op.create_index(
        "ix_categories_parent_id", "categories", ["parent_id"], unique=False
    )
    op.create_index(
        "ix_categories_parent_sort",
        "categories",
        ["parent_id", "sort_order", "id"],
        unique=False,
    )
    # 同级唯一：根层与子层分别约束（SQLite 部分唯一索引）
    op.create_index(
        "uq_categories_root_name",
        "categories",
        ["normalized_name"],
        unique=True,
        sqlite_where=sa.text("parent_id IS NULL"),
    )
    op.create_index(
        "uq_categories_child_name",
        "categories",
        ["parent_id", "normalized_name"],
        unique=True,
        sqlite_where=sa.text("parent_id IS NOT NULL"),
    )

    # ---- tags（全局规范化唯一） ----
    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("normalized_name", sa.String(length=50), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=_utc_now_sql()
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=_utc_now_sql()
        ),
        sa.UniqueConstraint("normalized_name", name="uq_tags_normalized_name"),
    )

    # ---- bookmarks ----
    op.create_table(
        "bookmarks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("url", sa.String(length=4096), nullable=False),
        sa.Column("normalized_url", sa.String(length=8192), nullable=False),
        sa.Column(
            "description", sa.Text(), nullable=False, server_default=""
        ),
        sa.Column("favicon_url", sa.String(length=2048), nullable=True),
        sa.Column("category_id", sa.Integer(), nullable=True),
        sa.Column(
            "is_favorite", sa.Boolean(), nullable=False, server_default="0"
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=_utc_now_sql()
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=_utc_now_sql()
        ),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["category_id"], ["categories.id"], ondelete="SET NULL", name="fk_bookmarks_category"
        ),
    )
    op.create_index(
        "ix_bookmarks_normalized_url", "bookmarks", ["normalized_url"], unique=False
    )
    op.create_index(
        "ix_bookmarks_category_id", "bookmarks", ["category_id"], unique=False
    )
    op.create_index("ix_bookmarks_deleted_at", "bookmarks", ["deleted_at"], unique=False)
    op.create_index(
        "ix_bookmarks_is_favorite", "bookmarks", ["is_favorite"], unique=False
    )
    op.create_index("ix_bookmarks_created_at", "bookmarks", ["created_at"], unique=False)
    op.create_index("ix_bookmarks_updated_at", "bookmarks", ["updated_at"], unique=False)
    # 常用列表复合索引：(deleted_at, created_at, id)
    op.create_index(
        "ix_bookmarks_list", "bookmarks", ["deleted_at", "created_at", "id"], unique=False
    )

    # ---- bookmark_tags（联合主键 + tag_id 检索索引） ----
    op.create_table(
        "bookmark_tags",
        sa.Column("bookmark_id", sa.Integer(), primary_key=True),
        sa.Column("tag_id", sa.Integer(), primary_key=True),
        sa.ForeignKeyConstraint(
            ["bookmark_id"], ["bookmarks.id"], ondelete="CASCADE", name="fk_bt_bookmark"
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"], ["tags.id"], ondelete="CASCADE", name="fk_bt_tag"
        ),
    )
    op.create_index(
        "ix_bookmark_tags_tag_id", "bookmark_tags", ["tag_id"], unique=False
    )

    # ---- import_jobs（UUID 主键的一次性任务） ----
    op.create_table(
        "import_jobs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("source_type", sa.String(length=4), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column("temp_file_key", sa.String(length=128), nullable=False),
        sa.Column("session_nonce_hash", sa.String(length=64), nullable=False),
        sa.Column("options_json", sa.Text(), nullable=False),
        sa.Column("summary_json", sa.Text(), nullable=False),
        sa.Column("category_tree_revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=_utc_now_sql()
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("executed_at", sa.DateTime(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_import_jobs_expires_at", "import_jobs", ["expires_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_import_jobs_expires_at", table_name="import_jobs")
    op.drop_table("import_jobs")
    op.drop_index("ix_bookmark_tags_tag_id", table_name="bookmark_tags")
    op.drop_table("bookmark_tags")
    op.drop_index("ix_bookmarks_list", table_name="bookmarks")
    op.drop_index("ix_bookmarks_updated_at", table_name="bookmarks")
    op.drop_index("ix_bookmarks_created_at", table_name="bookmarks")
    op.drop_index("ix_bookmarks_is_favorite", table_name="bookmarks")
    op.drop_index("ix_bookmarks_deleted_at", table_name="bookmarks")
    op.drop_index("ix_bookmarks_category_id", table_name="bookmarks")
    op.drop_index("ix_bookmarks_normalized_url", table_name="bookmarks")
    op.drop_table("bookmarks")
    op.drop_table("tags")
    op.drop_index("uq_categories_child_name", table_name="categories")
    op.drop_index("uq_categories_root_name", table_name="categories")
    op.drop_index("ix_categories_parent_sort", table_name="categories")
    op.drop_index("ix_categories_parent_id", table_name="categories")
    op.drop_table("categories")
    op.drop_table("app_meta")
    op.drop_table("users")

"""Alembic 迁移测试（BM-V1-102）。

- 空数据库 upgrade head 成功；重复执行不重复建表；
- 模型元数据与迁移结构一致；
- 根分类与子分类的同级唯一索引分别有效；
- app_meta 单行（id=1）已初始化。
"""

from __future__ import annotations

import pytest
from app.database import Base
from app.main import create_app
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError as SAIntegrityError
from tests.conftest import _upgrade_to_head

MODEL_TABLES = {
    "users",
    "app_meta",
    "categories",
    "bookmarks",
    "tags",
    "bookmark_tags",
    "import_jobs",
}

# SQLite 方言返回大写类型名；String 编译为 VARCHAR
_TYPE_ALIASES = {"STRING": "VARCHAR"}


def _types_equivalent(model_type: str, db_type: str) -> bool:
    return _TYPE_ALIASES.get(model_type.upper(), model_type.upper()) == db_type.upper()


def test_upgrade_on_empty_db_succeeds(db):
    """db fixture 已从空库 upgrade head；这里再执行一次验证幂等。"""
    _upgrade_to_head(str(db.url))
    inspector = inspect(db)
    assert MODEL_TABLES <= set(inspector.get_table_names())


def test_app_meta_row_initialized(db):
    with db.connect() as conn:
        row = conn.execute(text("SELECT id, category_tree_revision FROM app_meta")).one()
    assert row.id == 1
    assert row.category_tree_revision == 1


def test_model_metadata_matches_migration(db):
    """模型定义与迁移结构一致：表、列、可空性与命名索引。"""
    inspector = inspect(db)
    assert set(inspector.get_table_names()) - {"alembic_version"} == set(
        Base.metadata.tables.keys()
    )

    for table_name in MODEL_TABLES:
        model_table = Base.metadata.tables[table_name]
        db_columns = {col["name"]: col for col in inspector.get_columns(table_name)}
        model_columns = {name: col for name, col in model_table.columns.items()}
        assert set(db_columns) == set(model_columns), f"{table_name} 列不一致"
        for name, column in model_columns.items():
            assert db_columns[name]["nullable"] == column.nullable, (
                f"{table_name}.{name} 可空性不一致"
            )
            model_type = column.type.__class__.__name__
            db_type = db_columns[name]["type"].__class__.__name__
            assert _types_equivalent(model_type, db_type), (
                f"{table_name}.{name} 类型不一致: {model_type} vs {db_type}"
            )

    # 命名索引齐备（含两个部分唯一索引与列表复合索引）
    db_indexes = {idx["name"] for idx in inspector.get_indexes("categories")}
    assert "uq_categories_root_name" in db_indexes
    assert "uq_categories_child_name" in db_indexes
    assert "ix_categories_parent_sort" in db_indexes
    bookmarks_indexes = {idx["name"] for idx in inspector.get_indexes("bookmarks")}
    for name in (
        "ix_bookmarks_normalized_url",
        "ix_bookmarks_deleted_at",
        "ix_bookmarks_list",
        "ix_bookmarks_category_id",
        "ix_bookmarks_is_favorite",
        "ix_bookmarks_created_at",
        "ix_bookmarks_updated_at",
    ):
        assert name in bookmarks_indexes


def _insert_category(
    conn, name: str, parent_id: int | None = None, normalized: str | None = None
) -> int:
    result = conn.execute(
        text(
            "INSERT INTO categories (name, normalized_name, parent_id, sort_order, version)"
            " VALUES (:name, :norm, :parent, 0, 1)"
        ),
        {"name": name, "norm": normalized or name.lower(), "parent": parent_id},
    )
    return result.lastrowid


def test_partial_unique_indexes_root_and_child(db):
    """SQLite 部分唯一索引：根层重名拒绝；子层同父重名拒绝；不同父允许同名。"""
    with db.begin() as conn:
        work_id = _insert_category(conn, "工作", normalized="工作")
        with pytest.raises(SAIntegrityError):
            _insert_category(conn, "工作2", normalized="工作")  # 根层规范化重名
        ai_id = _insert_category(conn, "AI", normalized="ai")
        child_id = _insert_category(conn, "AI 子分类", parent_id=ai_id, normalized="ai 子分类")
        with pytest.raises(SAIntegrityError):
            _insert_category(conn, "重复子类", parent_id=ai_id, normalized="ai 子分类")  # 同父重名
        # 不同父节点允许同名
        _insert_category(conn, "同名B", parent_id=work_id, normalized="同名b")
        _insert_category(conn, "同名A", parent_id=child_id, normalized="同名b")
        # 根层与子层互不影响
        _insert_category(conn, "同名C", parent_id=None, normalized="同名b")


def test_foreign_keys_enforced(db):
    """迁移库外键真实生效：不存在分类关联、不存在标签关联均被拒绝。"""
    with db.begin() as conn:
        with pytest.raises(SAIntegrityError):
            conn.execute(
                text(
                    "INSERT INTO bookmarks (title, url, normalized_url, category_id, version)"
                    " VALUES ('t', 'u', 'n', 999, 1)"
                )
            )
        with pytest.raises(SAIntegrityError):
            conn.execute(text("INSERT INTO bookmark_tags (bookmark_id, tag_id) VALUES (1, 999)"))


def test_create_app_does_not_create_database(tmp_path):
    """create_app 不连接数据库：不产生库文件（连接是惰性的）。"""
    from app.config import load_settings

    settings = load_settings(
        env={
            "APP_ENV": "testing",
            "DATABASE_URL": f"sqlite:///{tmp_path / 'should-not-exist.db'}",
            "SESSION_SECRET": "s" * 40 + "x",
        },
        use_dotenv=False,
    )
    app = create_app(settings)
    assert app.state.engine is not None
    assert not (tmp_path / "should-not-exist.db").exists()

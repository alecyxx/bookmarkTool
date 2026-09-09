"""乐观并发与事务工具（BM-V1-107）。

- 统一 {id, version} 条件更新（CAS），成功后 version+1；0 行命中时区分 404 与 409；
- category_tree_revision 的 CAS 递增（所有分类结构修改必须通过本函数）；
- 批量版本预检：任一过期即整体失败。
"""

from __future__ import annotations

from typing import Any, TypeVar

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.database import now_utc
from app.errors import conflict, not_found
from app.models.app_meta import AppMeta

ModelT = TypeVar("ModelT")

_PROTECTED_FIELDS = {"id", "version", "created_at", "updated_at"}


def get_tree_revision(session: Session) -> int:
    meta = session.get(AppMeta, 1)
    if meta is None:
        # 迁移未初始化 app_meta 行：这是部署错误，不做猜测性写入
        raise RuntimeError("app_meta row missing: run alembic migrations")
    return meta.category_tree_revision


def bump_tree_revision(session: Session, expected_revision: int) -> None:
    """CAS 递增分类树 revision。期望值过期抛 409（调用方应重载整棵树）。"""
    result = session.execute(
        update(AppMeta)
        .where(AppMeta.id == 1, AppMeta.category_tree_revision == expected_revision)
        .values(category_tree_revision=expected_revision + 1, updated_at=now_utc())
    )
    if result.rowcount != 1:
        raise conflict("分类结构已变化，请刷新后重试。")


def fetch_versioned(session: Session, model: type[ModelT], record_id: int) -> ModelT:
    """按 id 取记录；不存在统一 404（供更新/删除类操作复用）。"""
    record = session.get(model, record_id)
    if record is None:
        raise not_found(f"{model.__name__} 不存在。")
    return record


def apply_versioned_update(
    session: Session,
    model: type[ModelT],
    record_id: int,
    expected_version: int,
    values: dict[str, Any],
) -> None:
    """CAS 更新：WHERE id AND version；成功后 version+1。

    - 记录不存在 -> 404（与并发冲突区分）；
    - 版本过期 -> 409 并提示重新加载；
    - 不可通过 values 修改 id/version/created_at。
    """
    for key in _PROTECTED_FIELDS:
        values.pop(key, None)
    values["updated_at"] = now_utc()
    values["version"] = expected_version + 1
    result = session.execute(
        update(model)
        .where(model.id == record_id, model.version == expected_version)
        .values(**values)
    )
    if result.rowcount != 1:
        exists = session.get(model, record_id)
        if exists is None:
            raise not_found(f"{model.__name__} 不存在。")
        raise conflict("数据已被其它会话修改，请重新加载后再试。")
    session.expire_all()


def validate_versions(
    session: Session,
    model: type[ModelT],
    items: list[tuple[int, int]],
) -> None:
    """批量版本预检：任一记录不存在或版本过期即抛 409，供整体事务拒绝。"""
    if not items:
        return
    ids = [item_id for item_id, _ in items]
    rows = session.execute(select(model.id, model.version).where(model.id.in_(ids))).all()
    found = {row[0]: row[1] for row in rows}
    for record_id, version in items:
        current = found.get(record_id)
        if current is None:
            raise not_found(f"{model.__name__} {record_id} 不存在。")
        if current != version:
            raise conflict(f"{model.__name__} {record_id} 已被其它会话修改，请重新加载后再试。")

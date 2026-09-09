"""批量书签命令与回收站操作（BM-V1-502~507）。

规则：去重 {id, version}；单次 1～500 条；任一记录不存在、状态不符或版本过期
即整体失败（同一数据库事务内完成，无部分成功）。
- soft_delete：只设置 deleted_at（正常列表书签）；
- restore：只接受回收站记录，保留 ID/时间/标签/分类/收藏/备注；
- permanent_delete：只接受回收站记录，标签关联由外键级联删除；
- set_category：替换主分类（可置未分类）；add_tags：只添加不替换；
- empty_trash：明确确认后的例外操作，忽略逐条版本但服务端再次限定 deleted_at IS NOT NULL。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.database import now_utc
from app.errors import conflict, not_found, validation_error
from app.models.bookmark import Bookmark
from app.models.category import Category
from app.services.bookmark_service import _resolve_tags

MAX_BULK_ITEMS = 500


@dataclass
class _RowState:
    version: int
    deleted_at: object  # None 表示未删除


class BulkBookmarkService:
    def __init__(self, session: Session):
        self.session = session

    # ---------- 公共校验 ----------

    def _load_state(self, items: list[tuple[int, int]]) -> dict[int, _RowState]:
        if not 1 <= len(items) <= MAX_BULK_ITEMS:
            raise validation_error({"items": f"批量操作数量必须在 1～{MAX_BULK_ITEMS} 之间。"})
        ids = [item_id for item_id, _ in items]
        if len(ids) != len(set(ids)):
            raise validation_error({"items": "批量请求中存在重复书签。"})
        rows = self.session.execute(
            select(Bookmark.id, Bookmark.version, Bookmark.deleted_at).where(Bookmark.id.in_(ids))
        ).all()
        found = {row[0]: _RowState(version=row[1], deleted_at=row[2]) for row in rows}
        for item_id, version in items:
            state = found.get(item_id)
            if state is None:
                raise not_found(f"书签 {item_id} 不存在。")
            if state.version != version:
                raise conflict(f"书签 {item_id} 已被其它会话修改，请重新加载后再试。")
        return found

    def _require_not_trashed(self, item_id: int, state: _RowState) -> None:
        if state.deleted_at is not None:
            raise conflict(f"书签 {item_id} 在回收站中，请先恢复或改用回收站操作。")

    def _require_trashed(self, item_id: int, state: _RowState) -> None:
        if state.deleted_at is None:
            raise conflict(f"书签 {item_id} 不在回收站中。")

    def _bump(self, item_id: int, version: int, **values) -> None:
        """版本化 CAS 更新单行；失败抛 409（整体回滚由请求级事务负责）。"""
        self.session.execute(
            update(Bookmark)
            .where(Bookmark.id == item_id, Bookmark.version == version)
            .values(version=version + 1, **values)
        )

    # ---------- 批量命令 ----------

    def soft_delete(self, items: list[tuple[int, int]]) -> int:
        state = self._load_state(items)
        for item_id, _version in items:
            self._require_not_trashed(item_id, state[item_id])
        stamp = now_utc()
        for item_id, _version in items:
            self._bump(item_id, _version, deleted_at=stamp, updated_at=stamp)
        return len(items)

    def restore(self, items: list[tuple[int, int]]) -> int:
        state = self._load_state(items)
        for item_id, _version in items:
            self._require_trashed(item_id, state[item_id])
        for item_id, version in items:
            self._bump(item_id, version, deleted_at=None)
        return len(items)

    def permanent_delete(self, items: list[tuple[int, int]]) -> int:
        state = self._load_state(items)
        for item_id, _version in items:
            self._require_trashed(item_id, state[item_id])
        for item_id, _version in items:
            self.session.delete(self.session.get(Bookmark, item_id))
        return len(items)

    def set_category(self, items: list[tuple[int, int]], category_id: int | None) -> int:
        if category_id is not None and self.session.get(Category, category_id) is None:
            raise validation_error({"category_id": "目标分类不存在。"})
        state = self._load_state(items)
        for item_id, _version in items:
            self._require_not_trashed(item_id, state[item_id])
        for item_id, version in items:
            self._bump(item_id, version, category_id=category_id)
        return len(items)

    def add_tags(self, items: list[tuple[int, int]], raw_tags: list[str]) -> int:
        state = self._load_state(items)
        for item_id, _version in items:
            self._require_not_trashed(item_id, state[item_id])
        tags = _resolve_tags(self.session, raw_tags)
        if not tags:
            raise validation_error({"tags": "至少提供一个有效标签。"})
        for item_id, version in items:
            bookmark = self.session.get(Bookmark, item_id)
            existing = {tag.id for tag in bookmark.tags}
            for tag in tags:
                if tag.id not in existing:
                    bookmark.tags.append(tag)
            bookmark.version = version + 1
        return len(items)

    def empty_trash(self) -> int:
        """清空回收站：物理删除全部回收站记录（不可撤销；关联标签级联删除）。"""
        result = self.session.execute(delete(Bookmark).where(Bookmark.deleted_at.is_not(None)))
        return result.rowcount or 0

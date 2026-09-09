"""标签领域服务（BM-V1-407/408）。

- 新增/改名/删除；规范化名称全局唯一，改名冲突不隐式合并（409）；
- 删除标签只删除标签及关联关系，不删除书签；
- 使用次数只统计未进入回收站的书签。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import conflict, not_found, validation_error
from app.models.bookmark import Bookmark, bookmark_tags
from app.models.tag import Tag
from app.services.normalize import normalize_name, strip_display_name
from app.services.versioned import apply_versioned_update

TAG_NAME_MAX = 50


class TagService:
    def __init__(self, session: Session):
        self.session = session

    def list_with_counts(self) -> list[tuple[Tag, int]]:
        """标签 + 未删除书签使用次数，按使用次数倒序、名称排序。"""
        count_expr = (
            select(func.count(Bookmark.id))
            .select_from(bookmark_tags)
            .join(Bookmark, Bookmark.id == bookmark_tags.c.bookmark_id)
            .where(
                bookmark_tags.c.tag_id == Tag.id,
                Bookmark.deleted_at.is_(None),
            )
            .scalar_subquery()
        )
        rows = self.session.execute(
            select(Tag, count_expr.label("cnt")).order_by(
                count_expr.desc(), Tag.normalized_name.asc()
            )
        ).all()
        return [(tag, int(cnt)) for tag, cnt in rows]

    def _require(self, tag_id: int) -> Tag:
        tag = self.session.get(Tag, tag_id)
        if tag is None:
            raise not_found("标签不存在。")
        return tag

    def create(self, name: str) -> Tag:
        display = strip_display_name(name)
        if not 1 <= len(display) <= TAG_NAME_MAX:
            raise validation_error({"name": f"标签名称长度必须在 1～{TAG_NAME_MAX} 字符之间。"})
        normalized = normalize_name(display)
        existing = self.session.scalar(select(Tag).where(Tag.normalized_name == normalized))
        if existing is not None:
            raise conflict("已存在同名标签。")
        tag = Tag(name=display, normalized_name=normalized)
        self.session.add(tag)
        self.session.flush()
        return tag

    def rename(self, tag_id: int, new_name: str, version: int) -> Tag:
        display = strip_display_name(new_name)
        if not 1 <= len(display) <= TAG_NAME_MAX:
            raise validation_error({"name": f"标签名称长度必须在 1～{TAG_NAME_MAX} 字符之间。"})
        normalized = normalize_name(display)
        existing = self.session.scalar(
            select(Tag).where(Tag.normalized_name == normalized, Tag.id != tag_id)
        )
        if existing is not None:
            raise conflict("已存在同名标签，改名不会自动合并，请先批量添加目标标签。")
        self._require(tag_id)
        apply_versioned_update(
            self.session, Tag, tag_id, version, {"name": display, "normalized_name": normalized}
        )
        return self._require(tag_id)

    def delete(self, tag_id: int) -> int:
        """删除标签；返回受影响的未删除书签数量（关联由外键级联删除，书签保留）。"""
        tag = self._require(tag_id)
        affected = self.active_count(tag_id)
        self.session.delete(tag)
        self.session.flush()
        return affected

    def active_count(self, tag_id: int) -> int:
        return int(
            self.session.scalar(
                select(func.count(Bookmark.id))
                .select_from(bookmark_tags)
                .join(Bookmark, Bookmark.id == bookmark_tags.c.bookmark_id)
                .where(bookmark_tags.c.tag_id == tag_id, Bookmark.deleted_at.is_(None))
            )
            or 0
        )

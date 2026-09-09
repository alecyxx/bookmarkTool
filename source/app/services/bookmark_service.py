"""书签领域服务（BM-V1-301~305）。

- 创建/编辑：主体 + 标签关联在同一事务；URL 规范化（重复只提示不阻止）；
- 标签按规范化名称查找或创建（保存书签的低摩擦行为）；
- 软删除（幂等）；收藏；查看；
- 列表查询：标题/URL/备注/分类完整祖先路径/标签子串（AND 组合）、
  分类筛选含全部后代、LIKE 转义、稳定排序、批量加载无 N+1。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.errors import conflict, not_found, validation_error
from app.models.bookmark import Bookmark, bookmark_tags
from app.models.category import Category
from app.models.tag import Tag
from app.services.category_service import CategoryService
from app.services.normalize import normalize_name, strip_display_name
from app.services.url_service import InvalidUrlError, normalize_url
from app.services.versioned import apply_versioned_update

PAGE_SIZE_ALL = 1_000_000
PAGE_SIZES = (15, 30, 25, 50, 100, PAGE_SIZE_ALL)
SORT_OPTIONS = (
    "created_desc",
    "created_asc",
    "updated_desc",
    "title_asc",
    "title_desc",
    "favorite_first",
)
DEFAULT_SORT = "created_desc"
MAX_SEARCH_LENGTH = 200
_LIKE_ESCAPE = re.compile(r"([%_\\])")


def escape_like(term: str) -> str:
    """转义 SQL LIKE 特殊字符（% _ \\），配合 ESCAPE '\\' 使用。"""
    return _LIKE_ESCAPE.sub(r"\\\1", term)


@dataclass
class BookmarkPage:
    items: list[Bookmark]
    total: int
    page: int
    page_size: int
    max_page: int


@dataclass
class DuplicateInfo:
    normalized_url: str
    active_count: int
    trashed_count: int


def _normalize_required_url(raw_url: str) -> str:
    try:
        return normalize_url(raw_url)
    except InvalidUrlError as exc:
        raise validation_error({"url": str(exc)}) from None


def _resolve_tags(db: Session, raw_tags: list[str]) -> list[Tag]:
    """按规范化名称查找或创建标签；同名不同写法合并为同一标签。"""
    result: list[Tag] = []
    seen: set[str] = set()
    for raw in raw_tags:
        display = strip_display_name(raw)
        if not display:
            continue
        normalized = normalize_name(display)
        if normalized in seen:
            continue
        seen.add(normalized)
        if len(display) > 50:
            raise validation_error({"tags": f"标签“{display[:20]}…”超过 50 字符上限。"})
        tag = db.scalar(select(Tag).where(Tag.normalized_name == normalized))
        if tag is None:
            tag = Tag(name=display, normalized_name=normalized)
            db.add(tag)
            db.flush()
        result.append(tag)
    return result


def duplicate_counts(db: Session, normalized: str) -> DuplicateInfo:
    active = db.scalar(
        select(func.count(Bookmark.id)).where(
            Bookmark.normalized_url == normalized, Bookmark.deleted_at.is_(None)
        )
    )
    trashed = db.scalar(
        select(func.count(Bookmark.id)).where(
            Bookmark.normalized_url == normalized, Bookmark.deleted_at.is_not(None)
        )
    )
    return DuplicateInfo(normalized, int(active or 0), int(trashed or 0))


def _validate_category(db: Session, category_id: int | None) -> None:
    if category_id is not None and db.get(Category, category_id) is None:
        raise validation_error({"category_id": "所选分类不存在，请刷新后重试。"})


class BookmarkService:
    def __init__(self, session: Session):
        self.session = session
        self._tree_cache: object | None = None

    # ---------- 单条维护 ----------

    def create(self, payload) -> Bookmark:
        normalized = _normalize_required_url(payload.url)
        _validate_category(self.session, payload.category_id)
        bookmark = Bookmark(
            title=strip_display_name(payload.title),
            url=payload.url.strip(),
            normalized_url=normalized,
            description=payload.description,
            favicon_url=payload.favicon_url,
            category_id=payload.category_id,
            is_favorite=payload.is_favorite,
        )
        self.session.add(bookmark)
        self.session.flush()
        tags = _resolve_tags(self.session, payload.tags)
        bookmark.tags.extend(tags)
        self.session.flush()
        return bookmark

    def get(self, bookmark_id: int) -> Bookmark:
        bookmark = self.session.scalar(
            select(Bookmark).where(Bookmark.id == bookmark_id).options(selectinload(Bookmark.tags))
        )
        if bookmark is None:
            raise not_found("书签不存在。")
        return bookmark

    def update(self, bookmark_id: int, version: int, payload) -> Bookmark:
        bookmark = self.get(bookmark_id)
        normalized = _normalize_required_url(payload.url)
        _validate_category(self.session, payload.category_id)
        apply_versioned_update(
            self.session,
            Bookmark,
            bookmark_id,
            version,
            {
                "title": strip_display_name(payload.title),
                "url": payload.url.strip(),
                "normalized_url": normalized,
                "description": payload.description,
                "favicon_url": payload.favicon_url,
                "category_id": payload.category_id,
                "is_favorite": payload.is_favorite,
            },
        )
        # 标签集合按提交值整体替换
        self.session.flush()
        bookmark = self.get(bookmark_id)
        bookmark.tags.clear()
        bookmark.tags.extend(_resolve_tags(self.session, payload.tags))
        self.session.flush()
        return bookmark

    def set_favorite(self, bookmark_id: int, version: int, is_favorite: bool) -> None:
        apply_versioned_update(
            self.session,
            Bookmark,
            bookmark_id,
            version,
            {"is_favorite": is_favorite},
        )

    def soft_delete(self, bookmark_id: int, version: int | None = None) -> None:
        """只设置 deleted_at。首次删除校验版本；记录已删除时重复调用幂等成功。"""
        from app.database import now_utc

        bookmark = self.session.get(Bookmark, bookmark_id)
        if bookmark is None:
            raise not_found("书签不存在。")
        if bookmark.deleted_at is not None:
            return  # 幂等
        if version is not None and bookmark.version != version:
            raise conflict("数据已被其它会话修改，请重新加载后再试。")
        bookmark.deleted_at = now_utc()
        bookmark.version += 1

    # ---------- 列表查询 ----------

    def _tree(self):
        if self._tree_cache is None:
            self._tree_cache = CategoryService(self.session).tree()
        return self._tree_cache

    def _base_filters(self, *, include_trash: bool = False):
        if include_trash:
            return [Bookmark.deleted_at.is_not(None)]
        return [Bookmark.deleted_at.is_(None)]

    def _apply_filters(
        self,
        stmt: Select,
        *,
        q: str | None,
        category_id: int | None,
        tag_id: int | None,
        favorite: bool | None,
        include_trash: bool = False,
    ) -> Select:
        filters = self._base_filters(include_trash=include_trash)
        if favorite is not None:
            filters.append(Bookmark.is_favorite.is_(favorite))
        if category_id is not None:
            tree = self._tree()
            descendant_ids = tree.descendant_ids(category_id, include_self=True)
            if not descendant_ids:
                # 非法/不存在分类 -> 空筛选（不报 500）
                filters.append(Bookmark.id == -1)
            else:
                filters.append(Bookmark.category_id.in_(descendant_ids))
        if tag_id is not None:
            exists_tag = (
                select(bookmark_tags.c.bookmark_id)
                .where(bookmark_tags.c.tag_id == tag_id)
                .scalar_subquery()
            )
            filters.append(Bookmark.id.in_(exists_tag))
        if q:
            term = q.strip()
            if len(term) > MAX_SEARCH_LENGTH:
                raise validation_error({"q": f"搜索词长度不能超过 {MAX_SEARCH_LENGTH} 字符。"})
            pattern = f"%{escape_like(term)}%"
            like_clauses = [
                Bookmark.title.like(pattern, escape="\\"),
                Bookmark.url.like(pattern, escape="\\"),
                Bookmark.description.like(pattern, escape="\\"),
            ]
            # 分类完整祖先路径匹配：命中路径文本的分类（含自身）作为候选集
            matched_category_ids = self._category_ids_matching(term)
            if matched_category_ids:
                like_clauses.append(Bookmark.category_id.in_(matched_category_ids))
            # 标签名匹配：以 EXISTS 子查询表达（天然去重）
            exists_tag_name = (
                select(bookmark_tags.c.bookmark_id)
                .join(Tag, Tag.id == bookmark_tags.c.tag_id)
                .where(Tag.name.like(pattern, escape="\\"))
                .scalar_subquery()
            )
            filters.append(or_(*like_clauses) | Bookmark.id.in_(exists_tag_name))
        return stmt.where(and_(*filters))

    def _category_ids_matching(self, term: str) -> list[int]:
        """返回自身名称或其祖先路径包含 term 的分类 id 集。"""
        tree = self._tree()
        lowered = term.casefold()
        matches = []
        for category_id, node in tree.nodes.items():
            path_text = "/".join(node.path).casefold()
            if lowered in path_text:
                matches.append(category_id)
        return matches

    def _sort_clauses(self, sort: str):
        if sort == "deleted_desc":
            return (Bookmark.deleted_at.desc(), Bookmark.id.desc())
        if sort == "created_asc":
            return (Bookmark.created_at.asc(), Bookmark.id.asc())
        if sort == "updated_desc":
            return (Bookmark.updated_at.desc(), Bookmark.id.desc())
        if sort == "title_asc":
            return (Bookmark.title.asc(), Bookmark.id.asc())
        if sort == "title_desc":
            return (Bookmark.title.desc(), Bookmark.id.desc())
        if sort == "favorite_first":
            return (Bookmark.is_favorite.desc(), Bookmark.created_at.desc(), Bookmark.id.desc())
        return (Bookmark.created_at.desc(), Bookmark.id.desc())  # created_desc 默认

    def list_page(
        self,
        *,
        q: str | None = None,
        category_id: int | None = None,
        tag_id: int | None = None,
        favorite: bool | None = None,
        sort: str = DEFAULT_SORT,
        page: int = 1,
        page_size: int = 50,
        include_trash: bool = False,
    ) -> BookmarkPage:
        if sort not in SORT_OPTIONS:
            sort = DEFAULT_SORT
        if page_size not in PAGE_SIZES:
            page_size = 50
        if page < 1:
            page = 1
        # 回收站默认按最近删除排序（created_desc 映射为 deleted_desc）
        if include_trash and sort == DEFAULT_SORT:
            sort = "deleted_desc"

        count_stmt = self._apply_filters(
            select(func.count(Bookmark.id)),
            q=q,
            category_id=category_id,
            tag_id=tag_id,
            favorite=favorite,
            include_trash=include_trash,
        )
        total = int(self.session.scalar(count_stmt) or 0)
        max_page = max(1, math.ceil(total / page_size))
        if page > max_page:
            page = max_page

        stmt = self._apply_filters(
            select(Bookmark),
            q=q,
            category_id=category_id,
            tag_id=tag_id,
            favorite=favorite,
            include_trash=include_trash,
        )
        stmt = stmt.options(selectinload(Bookmark.tags))
        for clause in self._sort_clauses(sort):
            stmt = stmt.order_by(clause)
        items = list(self.session.scalars(stmt.offset((page - 1) * page_size).limit(page_size)))
        return BookmarkPage(
            items=items, total=total, page=page, page_size=page_size, max_page=max_page
        )


def list_category_options(db: Session) -> list[tuple[int, str]]:
    """分类树拍平为 (id, 面包屑路径文本) 选项，供选择器/筛选使用。"""
    tree = CategoryService(db).tree()
    options: list[tuple[int, str]] = []
    for node in sorted(
        tree.nodes.values(), key=lambda n: (n.depth, n.category.sort_order, n.category.id)
    ):
        options.append((node.category.id, " / ".join(node.path)))
    return options

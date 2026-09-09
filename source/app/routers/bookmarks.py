"""书签页面与 API 路由（BM-V1-301~307）。

- GET /bookmarks：完整页面（URL 承载全部筛选/排序/分页状态）；
- GET /api/bookmarks：列表 HTML fragment（HTMX 局部刷新）；
- GET /api/bookmarks/{id}：编辑表单 fragment；
- POST /api/bookmarks：201 + JSON（重复提示）；HX-Request 时返回列表 fragment；
- PUT /api/bookmarks/{id}：编辑（version CAS）；DELETE：软删除（幂等）；
- PUT /api/bookmarks/{id}/favorite：收藏切换（轻量命令，需 version）。
"""

from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.dependencies import (
    get_current_user,
    get_db,
    require_api_guard,
    require_page_redirect,
)
from app.errors import validation_error
from app.models.tag import Tag
from app.models.user import User
from app.schemas.bookmark import BookmarkCreate, BookmarkUpdate
from app.services.bookmark_service import (
    PAGE_SIZE_ALL,
    BookmarkService,
    duplicate_counts,
    list_category_options,
)
from app.services.category_service import CategoryService
from app.services.tag_service import TagService
from app.template_utils import render

router = APIRouter()

_ALLOWED_SORTS = (
    "created_desc",
    "created_asc",
    "updated_desc",
    "title_asc",
    "title_desc",
    "favorite_first",
    "custom",
)

DEFAULT_PAGE_SIZE = 32


class VersionedAction(BaseModel):
    version: int = Field(ge=1)


class FavoriteAction(VersionedAction):
    is_favorite: bool


class BulkAction(BaseModel):
    items: list[tuple[int, int]] = Field(min_length=1, max_length=500)


class BulkCategoryAction(BulkAction):
    category_id: int | None = None


class BulkTagsAction(BulkAction):
    tags: list[str] = Field(default_factory=list, max_length=30)


class TrashEmptyAction(BaseModel):
    confirm: str = Field(min_length=1)


class BookmarkMove(BaseModel):
    id: int
    version: int = Field(ge=1)
    direction: Literal["up", "down"]


def _parse_int_param(value: str | None, default: int) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _build_state(request: Request) -> dict:
    """从 query 解析筛选状态（非法参数安全回退，不 500）。"""
    params = request.query_params
    q = (params.get("q") or "").strip() or None
    category = _parse_int_param(params.get("category"), 0)
    tag = _parse_int_param(params.get("tag"), 0)
    favorite_raw = params.get("favorite")
    favorite: bool | None = None
    if favorite_raw == "1":
        favorite = True
    sort = params.get("sort") if params.get("sort") in _ALLOWED_SORTS else "created_desc"
    page = _parse_int_param(params.get("page"), 1) or 1
    page_size_raw = params.get("page_size")
    if page_size_raw == "all":
        page_size = PAGE_SIZE_ALL
    elif page_size_raw in ("32", "64"):
        page_size = int(page_size_raw)
    else:
        page_size = DEFAULT_PAGE_SIZE
    if page < 1:
        page = 1
    return {
        "q": q,
        "category": category,
        "tag": tag,
        "favorite": favorite,
        "sort": sort,
        "page": page,
        "page_size": page_size,
    }


def _tag_options(db: Session) -> list[dict]:
    """弹窗标签选择器所需的现有标签库（名称 + 使用次数）。"""
    return [{"name": tag.name, "count": cnt} for tag, cnt in TagService(db).list_with_counts()]


def _page_size_token(page_size: int | str) -> str:
    """将 page_size 值（int 或字符串）序列化为 URL 参数 token（all 表示全部）。"""
    if page_size == "all" or (isinstance(page_size, int) and page_size >= PAGE_SIZE_ALL):
        return "all"
    return str(page_size)


def _canonical_url(base: str, state: dict, *, page: int | None = None) -> str:
    parts: list[tuple[str, str]] = []
    if state["q"]:
        parts.append(("q", state["q"]))
    if state["category"]:
        parts.append(("category", str(state["category"])))
    if state["tag"]:
        parts.append(("tag", str(state["tag"])))
    if state["favorite"]:
        parts.append(("favorite", "1"))
    if state["sort"] != "created_desc":
        parts.append(("sort", state["sort"]))
    current = page if page is not None else state["page"]
    if current != 1:
        parts.append(("page", str(current)))
    if state["page_size"] != DEFAULT_PAGE_SIZE:
        parts.append(("page_size", _page_size_token(state["page_size"])))
    query = urlencode(parts)
    return base + (f"?{query}" if query else "")


def _page_window(current: int, max_page: int, around: int = 1) -> list[int | None]:
    """页码窗口：当前页 ± around + 首尾页，缺口用 None（省略号）表示。"""
    candidates = {1, max_page} | {
        p for p in range(max(1, current - around), min(max_page, current + around) + 1)
    }
    pages: list[int | None] = []
    previous: int | None = None
    for p in sorted(candidates):
        if previous is not None and p - previous > 1:
            pages.append(None)
        pages.append(p)
        previous = p
    return pages


def _load_page_context(request: Request, db: Session, state: dict) -> dict:
    svc = BookmarkService(db)
    result = svc.list_page(
        q=state["q"],
        category_id=state["category"],
        tag_id=state["tag"],
        favorite=state["favorite"],
        sort=state["sort"],
        page=state["page"],
        page_size=state["page_size"],
    )
    category_svc = CategoryService(db)
    tree = category_svc.tree()
    counts = category_svc.counts()[0]

    tag_name = ""
    if state["tag"]:
        tag = db.get(Tag, state["tag"])
        tag_name = tag.name if tag else ""

    def page_url(page: int) -> str:
        return _canonical_url("/bookmarks", state, page=page)

    def filter_url(**overrides) -> str:
        """基于当前状态生成新筛选 URL；值为 None 的参数被移除；新筛选重置页码。"""
        merged = {
            "q": state["q"],
            "category": state["category"],
            "tag": state["tag"],
            "favorite": state["favorite"],
            "sort": state["sort"],
            "page_size": state["page_size"],
        }
        for key, value in overrides.items():
            merged[key] = value
        return _canonical_url("/bookmarks", merged, page=1)

    return {
        "request": request,
        "result": result,
        "state": state,
        "category_options": list_category_options(db),
        "sort_options": _ALLOWED_SORTS,
        "current_category_path": _category_path(tree, state["category"]),
        "pagination_pages": _page_window(result.page, result.max_page),
        "page_url": page_url,
        "filter_url": filter_url,
        "tree_roots": tree.roots,
        "counts": counts,
        "tag_name": tag_name,
        "page_size_all": PAGE_SIZE_ALL,
        "active_nav": "bookmarks",
        "page_title": "书签",
    }


def _category_path(tree, category_id: int | None) -> str:
    if category_id is None:
        return ""
    node = tree.get(category_id)
    return " / ".join(node.path) if node else ""


def _json_or_fragment(request: Request, fragment: str, status_code: int = 200) -> Response:
    response = HTMLResponse(fragment, status_code=status_code)
    return response


# ---------- 页面 ----------


@router.get("/bookmarks", response_class=HTMLResponse, response_model=None, include_in_schema=False)
async def bookmarks_page(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User | None, Depends(get_current_user)],
) -> Response:
    if user is None:
        return require_page_redirect(request)
    state = _build_state(request)
    context = _load_page_context(request, db, state)
    # 页码越界 -> 重定向到最后一个有效页（空结果保持第 1 页）
    result = context["result"]
    if result.total > 0 and result.page != state["page"]:
        return RedirectResponse(
            _canonical_url("/bookmarks", state, page=result.page), status_code=303
        )
    return render(request, "bookmarks.html", context)


# ---------- 列表 fragment ----------


@router.get("/api/bookmarks", response_class=HTMLResponse, include_in_schema=False)
async def bookmark_list_fragment(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> Response:
    if isinstance(user, RedirectResponse):
        return user
    state = _build_state(request)
    context = _load_page_context(request, db, state)
    result = context["result"]
    if result.total > 0 and result.page != state["page"]:
        # HTMX 场景：服务端直接渲染目标页并告知浏览器更新地址
        target_url = _canonical_url("/bookmarks", state, page=result.page)
        response = render(request, "partials/bookmark_list.html", context)
        response.headers["HX-Redirect"] = target_url
        return response
    return render(request, "partials/bookmark_list.html", context)


# ---------- 排序（自定义权重上移/下移） ----------


@router.post("/api/bookmarks/move", response_model=None, include_in_schema=False)
async def move_bookmark(
    payload: BookmarkMove,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> JSONResponse:
    if isinstance(user, RedirectResponse):
        raise validation_error({"detail": "请先登录。"})
    svc = BookmarkService(db)
    svc.move(payload.id, payload.version, payload.direction)
    db.commit()
    return JSONResponse({"ok": True})


# ---------- 单条 ----------


@router.get("/api/bookmarks/new-modal", response_class=HTMLResponse, include_in_schema=False)
async def bookmark_new_form(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> Response:
    if isinstance(user, RedirectResponse):
        return user
    context = {
        "request": request,
        "bookmark": None,
        "category_options": list_category_options(db),
        "all_tags": _tag_options(db),
        "duplicate": None,
        "modal_mode": "new",
    }
    return render(request, "partials/bookmark_modal.html", context)


@router.get("/api/bookmarks/{bookmark_id}", response_class=HTMLResponse, include_in_schema=False)
async def bookmark_edit_form(
    bookmark_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> Response:
    if isinstance(user, RedirectResponse):
        return user
    svc = BookmarkService(db)
    bookmark = svc.get(bookmark_id)
    context = {
        "request": request,
        "bookmark": bookmark,
        "category_options": list_category_options(db),
        "all_tags": _tag_options(db),
        "duplicate": duplicate_counts(db, bookmark.normalized_url),
        "modal_mode": "edit",
    }
    return render(request, "partials/bookmark_modal.html", context)


@router.post("/api/bookmarks")
async def create_bookmark(
    payload: BookmarkCreate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> Response:
    if isinstance(user, RedirectResponse):
        return user
    svc = BookmarkService(db)
    bookmark = svc.create(payload)
    db.commit()
    duplicate = duplicate_counts(db, bookmark.normalized_url)
    body = {
        "ok": True,
        "id": bookmark.id,
        "duplicate_active": duplicate.active_count - 1,
        "duplicate_trashed": duplicate.trashed_count,
    }
    if request.headers.get("HX-Request") == "true":
        state = _build_state(request)
        context = _load_page_context(request, db, state)
        return render(request, "partials/bookmark_list.html", context)
    return JSONResponse(body, status_code=201)


@router.put("/api/bookmarks/{bookmark_id}")
async def update_bookmark(
    bookmark_id: int,
    payload: BookmarkUpdate,
    request: Request,
    version: Annotated[int, Query(ge=1)],
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> Response:
    if isinstance(user, RedirectResponse):
        return user
    svc = BookmarkService(db)
    svc.update(bookmark_id, version, payload)
    db.commit()
    bookmark = svc.get(bookmark_id)
    context = {
        "request": request,
        "bookmark": bookmark,
        "duplicate": duplicate_counts(db, bookmark.normalized_url),
        "modal_mode": "edit",
    }
    if request.headers.get("HX-Request") == "true":
        return render(request, "partials/bookmark_item.html", context)
    return JSONResponse({"ok": True, "id": bookmark.id})


@router.delete("/api/bookmarks/{bookmark_id}")
async def delete_bookmark(
    bookmark_id: int,
    request: Request,
    version: Annotated[int, Query(ge=1)],
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> Response:
    if isinstance(user, RedirectResponse):
        return user
    svc = BookmarkService(db)
    svc.soft_delete(bookmark_id, version=version)
    db.commit()
    if request.headers.get("HX-Request") == "true":
        state = _build_state(request)
        context = _load_page_context(request, db, state)
        return render(request, "partials/bookmark_list.html", context)
    return JSONResponse({"ok": True})


@router.put("/api/bookmarks/{bookmark_id}/favorite")
async def toggle_favorite(
    bookmark_id: int,
    payload: FavoriteAction,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> Response:
    if isinstance(user, RedirectResponse):
        return user
    svc = BookmarkService(db)
    svc.set_favorite(bookmark_id, payload.version, payload.is_favorite)
    db.commit()
    bookmark = svc.get(bookmark_id)
    context = {
        "request": request,
        "bookmark": bookmark,
        "duplicate": duplicate_counts(db, bookmark.normalized_url),
        "modal_mode": "edit",
    }
    if request.headers.get("HX-Request") == "true":
        return render(request, "partials/bookmark_item.html", context)
    return JSONResponse(
        {"ok": True, "is_favorite": bookmark.is_favorite, "version": bookmark.version}
    )


# ---------- 批量 Modal fragment（BM-V1-508） ----------


@router.get("/api/bulk/category-modal", response_class=HTMLResponse, include_in_schema=False)
async def bulk_category_modal(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> Response:
    if isinstance(user, RedirectResponse):
        return user
    context = {
        "request": request,
        "options": [(None, "（未分类）")]
        + [(cid, path) for cid, path in list_category_options(db)],
    }
    return render(request, "partials/bulk_category_modal.html", context)


@router.get("/api/bulk/tags-modal", response_class=HTMLResponse, include_in_schema=False)
async def bulk_tags_modal(
    request: Request,
    user: Annotated[User, Depends(require_api_guard)],
) -> Response:
    if isinstance(user, RedirectResponse):
        return user
    return render(request, "partials/bulk_tags_modal.html", {"request": request})


@router.get("/api/bulk/confirm-modal", response_class=HTMLResponse, include_in_schema=False)
async def bulk_confirm_modal(
    request: Request,
    user: Annotated[User, Depends(require_api_guard)],
    action: str = "delete",
    count: int = 0,
) -> Response:
    if isinstance(user, RedirectResponse):
        return user
    words = {
        "delete": None,
        "restore": None,
        "permanent": "永久删除",
        "empty": "清空回收站",
    }
    if action not in words:
        from app.errors import not_found as nf

        raise nf("未知操作。")
    messages = {
        "delete": f"确定把选中的 {count} 个书签移入回收站吗？之后仍然可以恢复。",
        "restore": f"确定恢复选中的 {count} 个书签吗？",
        "permanent": f"确定永久删除选中的 {count} 个书签吗？此操作不可撤销。",
        "empty": "确定清空整个回收站吗？回收站中的全部书签将被永久删除，此操作不可撤销。",
    }
    context = {
        "request": request,
        "action": action,
        "count": count,
        "message": messages[action],
        "confirm_word": words[action],
    }
    return render(request, "partials/bulk_confirm_modal.html", context)


def _bulk_items(payload: BulkAction) -> list[tuple[int, int]]:
    return [(item[0], item[1]) for item in payload.items]


@router.post("/api/bookmarks/bulk-delete")
async def bulk_soft_delete(
    payload: BulkAction,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> JSONResponse:
    if isinstance(user, RedirectResponse):
        return user  # type: ignore[return-value]
    from app.services.bulk_service import BulkBookmarkService

    count = BulkBookmarkService(db).soft_delete(_bulk_items(payload))
    db.commit()
    return JSONResponse({"ok": True, "count": count})


@router.post("/api/bookmarks/bulk-restore")
async def bulk_restore(
    payload: BulkAction,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> JSONResponse:
    if isinstance(user, RedirectResponse):
        return user  # type: ignore[return-value]
    from app.services.bulk_service import BulkBookmarkService

    count = BulkBookmarkService(db).restore(_bulk_items(payload))
    db.commit()
    return JSONResponse({"ok": True, "count": count})


@router.post("/api/bookmarks/bulk-permanent-delete")
async def bulk_permanent_delete(
    payload: BulkAction,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> JSONResponse:
    if isinstance(user, RedirectResponse):
        return user  # type: ignore[return-value]
    from app.services.bulk_service import BulkBookmarkService

    count = BulkBookmarkService(db).permanent_delete(_bulk_items(payload))
    db.commit()
    return JSONResponse({"ok": True, "count": count})


@router.post("/api/bookmarks/bulk-category")
async def bulk_set_category(
    payload: BulkCategoryAction,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> JSONResponse:
    if isinstance(user, RedirectResponse):
        return user  # type: ignore[return-value]
    from app.services.bulk_service import BulkBookmarkService

    count = BulkBookmarkService(db).set_category(_bulk_items(payload), payload.category_id)
    db.commit()
    return JSONResponse({"ok": True, "count": count})


@router.post("/api/bookmarks/bulk-tags")
async def bulk_add_tags(
    payload: BulkTagsAction,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> JSONResponse:
    if isinstance(user, RedirectResponse):
        return user  # type: ignore[return-value]
    from app.services.bulk_service import BulkBookmarkService

    count = BulkBookmarkService(db).add_tags(_bulk_items(payload), payload.tags)
    db.commit()
    return JSONResponse({"ok": True, "count": count})


@router.post("/api/bookmarks/{bookmark_id}/restore")
async def restore_bookmark(
    bookmark_id: int,
    payload: VersionedAction,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> JSONResponse:
    if isinstance(user, RedirectResponse):
        return user  # type: ignore[return-value]
    from app.services.bulk_service import BulkBookmarkService

    BulkBookmarkService(db).restore([(bookmark_id, payload.version)])
    db.commit()
    return JSONResponse({"ok": True, "id": bookmark_id})


@router.delete("/api/bookmarks/{bookmark_id}/permanent")
async def permanent_delete_bookmark(
    bookmark_id: int,
    payload: VersionedAction,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> JSONResponse:
    if isinstance(user, RedirectResponse):
        return user  # type: ignore[return-value]
    from app.services.bulk_service import BulkBookmarkService

    BulkBookmarkService(db).permanent_delete([(bookmark_id, payload.version)])
    db.commit()
    return JSONResponse({"ok": True, "id": bookmark_id})


@router.post("/api/trash/empty")
async def empty_trash(
    payload: TrashEmptyAction,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> JSONResponse:
    if isinstance(user, RedirectResponse):
        return user  # type: ignore[return-value]
    if payload.confirm.strip() != "清空回收站":
        from app.errors import validation_error as ve

        raise ve({"confirm": "请输入「清空回收站」以确认。"})
    from app.services.bulk_service import BulkBookmarkService

    count = BulkBookmarkService(db).empty_trash()
    db.commit()
    return JSONResponse({"ok": True, "count": count})

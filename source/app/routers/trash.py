"""回收站页面（BM-V1-505）。

- GET /trash：回收站列表页（独立搜索/分页/稳定排序：默认最近删除在前）；
- GET /api/trash/list：列表 fragment；
- 单条恢复/永久删除与批量/清空命令位于书签路由（设计 §38/39 接口归属书签）；
- 行内重复提示：与正常列表存在相同 normalized_url 时显示提示（恢复不阻止）。
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.dependencies import get_current_user, get_db, require_api_guard, require_page_redirect
from app.models.bookmark import Bookmark
from app.models.user import User
from app.services.bookmark_service import BookmarkService
from app.template_utils import render

router = APIRouter()

_PAGE_SIZES = ("25", "50", "100")


def _page_window(current: int, max_page: int, around: int = 1) -> list[int | None]:
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


def _load_context(request: Request, db: Session) -> dict:
    params = request.query_params
    q = (params.get("q") or "").strip() or None
    page = 1
    try:
        page = int(params.get("page") or "1")
    except ValueError:
        page = 1
    if page < 1:
        page = 1
    page_size_raw = params.get("page_size")
    page_size = int(page_size_raw) if page_size_raw in _PAGE_SIZES else 50

    svc = BookmarkService(db)
    result = svc.list_page(q=q, page=page, page_size=page_size, include_trash=True)
    if result.total > 0 and result.page != page:
        params_dict = {k: v for k, v in params.items() if k != "page"}
        params_dict["page"] = str(result.page)
        return {"redirect": "/trash?" + urlencode(params_dict)}

    # 页内重复提示：与正常列表同键统计（含已删除在本页记录自身的说明留给模板判断）
    ids = [item.id for item in result.items]
    duplicates: dict[int, int] = {}
    if ids:
        rows = db.execute(
            select(Bookmark.normalized_url, func.count(Bookmark.id))
            .where(Bookmark.normalized_url.in_([b.normalized_url for b in result.items]))
            .group_by(Bookmark.normalized_url)
        ).all()
        active_by_url = {url: cnt for url, cnt in rows}
        for item in result.items:
            duplicates[item.id] = active_by_url.get(item.normalized_url, 0) - 1  # 减自身

    def page_url(p: int) -> str:
        params_dict = {k: v for k, v in params.items() if k != "page"}
        if p != 1:
            params_dict["page"] = str(p)
        query = urlencode(params_dict)
        return "/trash" + (f"?{query}" if query else "")

    return {
        "request": request,
        "result": result,
        "q": q,
        "page_url": page_url,
        "pagination_pages": _page_window(result.page, result.max_page),
        "duplicates": duplicates,
        "active_nav": "trash",
        "page_title": "回收站",
        "redirect": None,
    }


def _respond(request: Request, db: Session, template: str) -> Response:
    context = _load_context(request, db)
    if context.get("redirect"):
        return RedirectResponse(context["redirect"], status_code=303)
    return render(request, template, context)


@router.get("/trash", response_class=HTMLResponse, response_model=None, include_in_schema=False)
async def trash_page(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User | None, Depends(get_current_user)],
) -> Response:
    if user is None:
        return require_page_redirect(request)
    return _respond(request, db, "trash.html")


@router.get("/api/trash/list", response_class=HTMLResponse, include_in_schema=False)
async def trash_list_fragment(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> Response:
    if isinstance(user, RedirectResponse):
        return user
    return _respond(request, db, "partials/trash_list.html")

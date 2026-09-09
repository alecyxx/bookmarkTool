"""分类页面与 API 路由（BM-V1-401~406）。

- GET /categories：树形管理页；
- GET /api/categories/tree：树 fragment（含面包屑与计数）；
- GET /api/categories/{id}/delete-info：删除影响确认 fragment；
- GET /api/categories/new-modal / /{id}/edit-modal：新增/编辑表单；
- POST /api/categories：创建（同级规范化唯一校验）；
- PUT /api/categories/{id}：整量更新（名称与父节点；移动整个子树）；
- DELETE /api/categories/{id}：删除当前节点（书签迁移 + 子分类提升）；
- POST /api/categories/reorder：同级排序（上移/下移通过提交整层顺序实现）。

所有写接口必须提交 category_tree_revision；更新/删除已有节点还需节点 version。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
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
from app.models.user import User
from app.services.category_service import CategoryService
from app.services.versioned import get_tree_revision
from app.template_utils import render

router = APIRouter()


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    parent_id: int | None = None
    category_tree_revision: int = Field(ge=1)


class CategoryUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    parent_id: int | None = None
    version: int = Field(ge=1)
    category_tree_revision: int = Field(ge=1)


class CategoryDelete(BaseModel):
    version: int = Field(ge=1)
    category_tree_revision: int = Field(ge=1)
    move_bookmarks_to_category_id: int | None = None


class ReorderItem(BaseModel):
    id: int
    version: int = Field(ge=1)


class CategoryReorder(BaseModel):
    parent_id: int | None = None
    ordered: list[ReorderItem] = Field(min_length=1, max_length=500)
    category_tree_revision: int = Field(ge=1)


def _user_or_redirect(user) -> User | RedirectResponse:
    if isinstance(user, RedirectResponse):
        return user
    return user


def _load_tree_context(request: Request, db: Session) -> dict:
    svc = CategoryService(db)
    tree = svc.tree()
    counts = svc.counts()[0]
    return {
        "request": request,
        "tree": tree,
        "counts": counts,
        "tree_revision": get_tree_revision(db),
        "active_nav": "categories",
        "page_title": "分类",
    }


@router.get(
    "/categories", response_class=HTMLResponse, response_model=None, include_in_schema=False
)
async def categories_page(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User | None, Depends(get_current_user)],
) -> Response:
    if user is None:
        return require_page_redirect(request)
    return render(request, "categories.html", _load_tree_context(request, db))


@router.get("/api/categories/tree", response_class=HTMLResponse, include_in_schema=False)
async def category_tree_fragment(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> Response:
    if isinstance(user, RedirectResponse):
        return user
    return render(request, "partials/category_tree.html", _load_tree_context(request, db))


@router.get("/api/categories/new-modal", response_class=HTMLResponse, include_in_schema=False)
async def category_new_form(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
    parent: int | None = None,
) -> Response:
    if isinstance(user, RedirectResponse):
        return user
    context = {
        "request": request,
        "category": None,
        "parent_id_default": parent,
        "options": _category_options(db),
        "tree_revision": get_tree_revision(db),
    }
    return render(request, "partials/category_modal.html", context)


@router.get(
    "/api/categories/{category_id}/edit-modal", response_class=HTMLResponse, include_in_schema=False
)
async def category_edit_form(
    category_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> Response:
    if isinstance(user, RedirectResponse):
        return user
    svc = CategoryService(db)
    category = svc.tree().require(category_id).category
    context = {
        "request": request,
        "category": category,
        "parent_id_default": category.parent_id,
        "options": _category_options(db),
        "tree_revision": get_tree_revision(db),
    }
    return render(request, "partials/category_modal.html", context)


@router.get(
    "/api/categories/{category_id}/delete-info",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def category_delete_info(
    category_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> Response:
    if isinstance(user, RedirectResponse):
        return user
    svc = CategoryService(db)
    tree = svc.tree()
    node = tree.require(category_id)
    counts, _uncategorized = svc.counts()
    direct_bookmarks = counts.get(category_id, 0)
    # 直接书签数（不含后代）单独计算
    from sqlalchemy import func
    from sqlalchemy import select as sa_select

    from app.models.bookmark import Bookmark

    direct_count = int(
        db.scalar(
            sa_select(func.count(Bookmark.id)).where(
                Bookmark.category_id == category_id, Bookmark.deleted_at.is_(None)
            )
        )
        or 0
    )
    context = {
        "request": request,
        "category": node.category,
        "direct_children": len(node.children),
        "direct_bookmarks": direct_count,
        "total_bookmarks": direct_bookmarks,
        "options": _category_options(db),
        "tree_revision": get_tree_revision(db),
    }
    return render(request, "partials/category_delete_confirm.html", context)


def _category_options(db: Session) -> list[tuple[int, str]]:
    svc = CategoryService(db)
    tree = svc.tree()
    options: list[tuple[int, str]] = []
    for node in sorted(
        tree.nodes.values(), key=lambda n: (n.depth, n.category.sort_order, n.category.id)
    ):
        options.append((node.category.id, " / ".join(node.path)))
    return options


# ---------- 写操作 ----------


@router.post("/api/categories")
async def create_category(
    payload: CategoryCreate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> JSONResponse:
    if isinstance(user, RedirectResponse):
        raise validation_error({"detail": "请先登录。"})  # pragma: no cover - 守卫短路
    svc = CategoryService(db)
    category = svc.create(payload.name, payload.parent_id, payload.category_tree_revision)
    db.commit()
    return JSONResponse({"ok": True, "id": category.id})


@router.put("/api/categories/{category_id}")
async def update_category(
    category_id: int,
    payload: CategoryUpdate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> JSONResponse:
    if isinstance(user, RedirectResponse):
        raise validation_error({"detail": "请先登录。"})  # pragma: no cover
    svc = CategoryService(db)
    svc.update(
        category_id,
        payload.name,
        payload.parent_id,
        payload.version,
        payload.category_tree_revision,
    )
    db.commit()
    return JSONResponse({"ok": True, "id": category_id})


@router.delete("/api/categories/{category_id}")
async def delete_category(
    category_id: int,
    payload: CategoryDelete,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> JSONResponse:
    if isinstance(user, RedirectResponse):
        raise validation_error({"detail": "请先登录。"})  # pragma: no cover
    svc = CategoryService(db)
    svc.delete(
        category_id,
        payload.move_bookmarks_to_category_id,
        payload.version,
        payload.category_tree_revision,
    )
    db.commit()
    return JSONResponse({"ok": True, "id": category_id})


@router.post("/api/categories/reorder")
async def reorder_categories(
    payload: CategoryReorder,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> JSONResponse:
    if isinstance(user, RedirectResponse):
        raise validation_error({"detail": "请先登录。"})  # pragma: no cover
    svc = CategoryService(db)
    ordered = [(item.id, item.version) for item in payload.ordered]
    svc.reorder(payload.parent_id, ordered, payload.category_tree_revision)
    db.commit()
    return JSONResponse({"ok": True})

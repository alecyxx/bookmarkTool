"""标签页面与 API 路由（BM-V1-407/408）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.dependencies import get_current_user, get_db, require_api_guard, require_page_redirect
from app.models.user import User
from app.services.tag_service import TagService
from app.template_utils import render

router = APIRouter()


class TagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50)


class TagUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    version: int = Field(ge=1)


class TagDelete(BaseModel):
    version: int = Field(ge=1)


def _load_tag_context(request: Request, db: Session) -> dict:
    svc = TagService(db)
    tags = svc.list_with_counts()
    return {
        "request": request,
        "tags": tags,
        "active_nav": "tags",
        "page_title": "标签",
    }


@router.get("/tags", response_class=HTMLResponse, response_model=None, include_in_schema=False)
async def tags_page(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User | None, Depends(get_current_user)],
) -> Response:
    if user is None:
        return require_page_redirect(request)
    return render(request, "tags.html", _load_tag_context(request, db))


@router.get("/api/tags/list", response_class=HTMLResponse, include_in_schema=False)
async def tag_list_fragment(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> Response:
    if isinstance(user, RedirectResponse):
        return user
    return render(request, "partials/tag_list.html", _load_tag_context(request, db))


@router.get("/api/tags/new-modal", response_class=HTMLResponse, include_in_schema=False)
async def tag_new_form(
    request: Request,
    user: Annotated[User, Depends(require_api_guard)],
) -> Response:
    if isinstance(user, RedirectResponse):
        return user
    context = {"request": request, "tag": None}
    return render(request, "partials/tag_modal.html", context)


@router.get("/api/tags/{tag_id}/edit-modal", response_class=HTMLResponse, include_in_schema=False)
async def tag_edit_form(
    tag_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> Response:
    if isinstance(user, RedirectResponse):
        return user
    tag = TagService(db)._require(tag_id)
    context = {"request": request, "tag": tag}
    return render(request, "partials/tag_modal.html", context)


@router.get("/api/tags/{tag_id}/delete-info", response_class=HTMLResponse, include_in_schema=False)
async def tag_delete_info(
    tag_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> Response:
    if isinstance(user, RedirectResponse):
        return user
    svc = TagService(db)
    tag = svc._require(tag_id)
    affected = svc.active_count(tag_id)
    context = {"request": request, "tag": tag, "affected": affected, "version": tag.version}
    return render(request, "partials/tag_delete_confirm.html", context)


@router.post("/api/tags")
async def create_tag(
    payload: TagCreate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> JSONResponse:
    if isinstance(user, RedirectResponse):
        return user  # type: ignore[return-value]
    tag = TagService(db).create(payload.name)
    db.commit()
    return JSONResponse({"ok": True, "id": tag.id})


@router.put("/api/tags/{tag_id}")
async def update_tag(
    tag_id: int,
    payload: TagUpdate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> JSONResponse:
    if isinstance(user, RedirectResponse):
        return user  # type: ignore[return-value]
    tag = TagService(db).rename(tag_id, payload.name, payload.version)
    db.commit()
    return JSONResponse({"ok": True, "id": tag.id})


@router.delete("/api/tags/{tag_id}")
async def delete_tag(
    tag_id: int,
    payload: TagDelete,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> JSONResponse:
    if isinstance(user, RedirectResponse):
        return user  # type: ignore[return-value]
    from app.models.tag import Tag
    from app.services.versioned import validate_versions

    validate_versions(db, Tag, [(tag_id, payload.version)])
    affected = TagService(db).delete(tag_id)
    db.commit()
    return JSONResponse({"ok": True, "id": tag_id, "affected_bookmarks": affected})

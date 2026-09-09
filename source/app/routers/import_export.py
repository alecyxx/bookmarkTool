"""导入导出页面与接口（BM-V1-601~608）。"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.config import Settings
from app.dependencies import get_current_user, get_db, require_api_guard, require_page_redirect
from app.errors import AppError, validation_error
from app.models.user import User
from app.services import export_service, import_service
from app.template_utils import render

router = APIRouter()


def _app_settings(request: Request) -> Settings:
    return request.app.state.settings


def _session_cookie_value(request: Request) -> str | None:
    name = _app_settings(request).session_cookie_name
    return request.cookies.get(name)


def _detect_type(filename: str, content: bytes) -> str:
    lowered = filename.lower()
    if lowered.endswith((".html", ".htm")):
        return "HTML"
    if lowered.endswith(".csv"):
        return "CSV"
    head = content[:4096].decode("utf-8", errors="ignore").lower()
    if "netscape-bookmark-file" in head or "<dl" in head:
        return "HTML"
    if "title,url" in head:
        return "CSV"
    raise validation_error({"file": "无法识别的文件类型：请上传 Netscape HTML 书签或 CSV 文件。"})


def _job_card_context(request: Request, job, summary: dict) -> dict:
    options = json.loads(job.options_json)
    return {
        "request": request,
        "job": job,
        "summary": summary,
        "options": options,
    }


# ---------- 页面 ----------


@router.get(
    "/import-export", response_class=HTMLResponse, response_model=None, include_in_schema=False
)
async def import_export_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)],
) -> Response:
    if user is None:
        return require_page_redirect(request)
    context = {
        "request": request,
        "active_nav": "import_export",
        "page_title": "导入 / 导出",
    }
    return render(request, "import_export.html", context)


# ---------- 导入 ----------


@router.post("/api/imports/preview", response_class=HTMLResponse, include_in_schema=False)
async def preview_upload(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
    file: UploadFile,
    folder_policy: Annotated[str, Form()] = "category",
    csrf_token: Annotated[str, Form()] = "",
) -> Response:
    if isinstance(user, RedirectResponse):
        return user
    # multipart 表单 CSRF：与登录/登出一致的全量校验（cookie 一致 + 签名/版本/过期）
    from app.routers.auth import _form_csrf_valid

    if not _form_csrf_valid(request, csrf_token):
        raise AppError(403, "forbidden", "请求校验未通过，请刷新页面后重试（CSRF）。")
    content = await file.read()
    if not content:
        raise validation_error({"file": "文件为空。"})
    settings = _app_settings(request)
    if len(content) > settings.max_upload_bytes:
        raise validation_error(
            {"file": f"文件超过 {settings.max_upload_bytes // (1024 * 1024)} MB 限制。"}
        )
    if folder_policy not in ("category", "tags", "ignore"):
        raise validation_error({"folder_policy": "无效的目录处理策略。"})
    source_type = _detect_type(file.filename or "", content)
    job, parsed = import_service.create_previewed_job(
        db,
        settings,
        source_type=source_type,
        original_filename=file.filename or "bookmarks",
        content=content,
        session_cookie=_session_cookie_value(request),
        folder_policy=folder_policy,
    )
    db.commit()
    summary = json.loads(job.summary_json)
    context = _job_card_context(request, job, summary)
    context["parsed_items"] = parsed.items
    return render(request, "partials/import_preview.html", context)


@router.get("/api/imports/{job_id}/card", response_class=HTMLResponse, include_in_schema=False)
async def job_card(
    job_id: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> Response:
    if isinstance(user, RedirectResponse):
        return user
    job = import_service.get_job(db, job_id)
    return render(
        request,
        "partials/import_preview.html",
        _job_card_context(request, job, json.loads(job.summary_json)),
    )


@router.put("/api/imports/{job_id}/options", response_class=HTMLResponse, include_in_schema=False)
async def change_options(
    job_id: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> Response:
    if isinstance(user, RedirectResponse):
        return user
    payload = await request.json()
    job = import_service.get_job(db, job_id)
    summary = import_service.update_options(
        db,
        job,
        _app_settings(request),
        duplicate_policy=str(payload.get("duplicate_policy") or "skip"),
        folder_policy=str(payload.get("folder_policy") or "category"),
    )
    db.commit()
    return render(
        request,
        "partials/import_preview.html",
        _job_card_context(request, job, summary),
    )


@router.post("/api/imports/{job_id}/execute")
async def execute_job(
    job_id: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> JSONResponse:
    if isinstance(user, RedirectResponse):
        return user
    job = import_service.get_job(db, job_id)
    executor = import_service.ImportExecutor(
        db, _app_settings(request), _session_cookie_value(request)
    )
    try:
        counts = executor.execute(job)
    except AppError as exc:
        # executor 已持久化终态；依赖层回滚为空操作
        raise exc
    db.commit()
    return JSONResponse({"ok": True, "job_id": job_id, "counts": counts})


@router.delete("/api/imports/{job_id}")
async def cancel_job(
    job_id: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> JSONResponse:
    if isinstance(user, RedirectResponse):
        return user
    job = import_service.get_job(db, job_id)
    import_service.cancel_job(db, job, _app_settings(request))
    db.commit()
    return JSONResponse({"ok": True, "job_id": job_id, "status": "EXPIRED"})


# ---------- 导出 ----------


def _export_response(content: bytes, filename: str, media_type: str) -> Response:
    disposition = f"attachment; filename*=UTF-8''{filename}"
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": disposition,
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/api/export/html", include_in_schema=False)
async def export_html(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> Response:
    if isinstance(user, RedirectResponse):
        return user
    content = export_service.to_netscape_html(db).encode("utf-8")
    return _export_response(
        content,
        export_service.export_filename("bookmarks", "html"),
        "text/html; charset=utf-8",
    )


@router.get("/api/export/csv", include_in_schema=False)
async def export_csv(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_api_guard)],
) -> Response:
    if isinstance(user, RedirectResponse):
        return user
    return _export_response(
        export_service.to_csv_bytes(db),
        export_service.export_filename("bookmarks", "csv"),
        "text/csv; charset=utf-8",
    )

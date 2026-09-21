"""导入任务状态机与预览/执行服务（BM-V1-601/604/605）。

安全模型：
- 上传即解析并创建 PREVIEWED 任务；执行只接受任务 ID + CSRF；
- 执行接口不信任任何客户端回传的策略/计数：读取服务端保存的 options、
  重新解析临时文件、重算 SHA-256、校验会话 nonce/过期/tree revision，
  并在同一事务内按数据库最新状态执行；
- 状态流转 PREVIEWED -> RUNNING -> SUCCEEDED/FAILED；同一任务最多执行一次；
- 失败时先回滚业务写入，再持久化 FAILED 状态（不产生半批数据）；
- 临时文件位于持久数据目录独立子目录，键为服务端随机 UUID。
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import now_utc
from app.errors import AppError, conflict, not_found, validation_error
from app.models.bookmark import Bookmark
from app.models.category import Category
from app.models.import_job import ImportJob
from app.models.tag import Tag
from app.services.import_parser import ParseResult, parse_csv, parse_netscape_html
from app.services.normalize import normalize_name, strip_display_name
from app.services.versioned import bump_tree_revision, get_tree_revision

STATUS_PREVIEWED = "PREVIEWED"
STATUS_RUNNING = "RUNNING"
STATUS_SUCCEEDED = "SUCCEEDED"
STATUS_FAILED = "FAILED"
STATUS_EXPIRED = "EXPIRED"

DUPLICATE_SKIP = "skip"
DUPLICATE_OVERWRITE = "overwrite"
DUPLICATE_KEEP = "keep"
FOLDER_CATEGORY = "category"
FOLDER_TAGS = "tags"
FOLDER_IGNORE = "ignore"
EMPTY_TITLE_HOST = "host"
EMPTY_TITLE_SKIP = "skip"

MAX_FILENAME_LENGTH = 180
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff ]")


@dataclass
class TempFiles:
    root: Path
    key: str

    @property
    def source(self) -> Path:
        return self.root / f"{self.key}.src"


def sanitize_filename(raw: str) -> str:
    """安全化文件名（仅展示用；磁盘路径一律使用服务端随机键）。"""
    name = Path(raw or "import").name
    name = _SAFE_FILENAME.sub("_", name).strip(" .") or "import"
    return name[:MAX_FILENAME_LENGTH]


def resolve_temp_root(settings: Settings) -> Path:
    if settings.import_tmp_dir:
        root = Path(settings.import_tmp_dir)
    else:
        from app.config import APP_ROOT

        root = APP_ROOT / "dev_data" / "import_tmp"
    root.mkdir(parents=True, exist_ok=True)
    return root


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _session_nonce_hash(session_cookie_value: str | None) -> str:
    return sha256_bytes((session_cookie_value or "").encode("utf-8"))


def parse_content(source_type: str, content: bytes) -> ParseResult:
    if source_type == "HTML":
        return parse_netscape_html(content)
    if source_type == "CSV":
        return parse_csv(content)
    raise ValueError(f"unknown source_type {source_type}")  # pragma: no cover


def make_summary(parsed: ParseResult, analysis: dict) -> dict:
    return {
        "total_rows": parsed.total_rows,
        "valid_items": len(parsed.items),
        "empty_folders": len(parsed.empty_folders),
        "errors_total": len(parsed.errors),
        "warnings_total": len(parsed.warnings),
        "errors_preview": parsed.errors[:50],
        "warnings_preview": parsed.warnings[:50],
        "empty_folders_preview": [" / ".join(f.path) for f in parsed.empty_folders[:50]],
        "analysis": analysis,
    }


def analyze(db: Session, parsed: ParseResult) -> dict:
    """预览统计：库内重复/回收站冲突/文件内重复计数（按 normalized_url 批量比对）。"""
    seen_in_file: dict[str, int] = {}
    for item in parsed.items:
        seen_in_file[item.normalized_url] = seen_in_file.get(item.normalized_url, 0) + 1
    file_duplicates = sum(count - 1 for count in seen_in_file.values() if count > 1)
    keys = list(seen_in_file)
    library_keys = 0
    trash_conflicts = 0
    if keys:
        active: dict[str, int] = {}
        trashed: dict[str, int] = {}
        # IN 子句分批，避免 SQLite 变量上限（999）
        for start in range(0, len(keys), 400):
            chunk = keys[start : start + 400]
            rows = db.execute(
                select(Bookmark.normalized_url, Bookmark.deleted_at.is_(None)).where(
                    Bookmark.normalized_url.in_(chunk)
                )
            ).all()
            for normalized, is_active in rows:
                bucket = active if is_active else trashed
                bucket[normalized] = bucket.get(normalized, 0) + 1
        for normalized in keys:
            if active.get(normalized, 0):
                library_keys += 1
            elif trashed.get(normalized, 0):
                trash_conflicts += 1
    return {
        "library_duplicates": library_keys,
        "trash_conflicts": trash_conflicts,
        "file_duplicates": file_duplicates,
        "unique_items": len(seen_in_file),
    }


def create_previewed_job(
    db: Session,
    settings: Settings,
    *,
    source_type: str,
    original_filename: str,
    content: bytes,
    session_cookie: str | None,
    folder_policy: str,
) -> tuple[ImportJob, ParseResult]:
    """解析并创建 PREVIEWED 任务。解析失败同样创建任务（便于页面展示错误，执行会被拒绝）。"""
    parsed = parse_content(source_type, content)
    if parsed.total_rows > settings.max_import_rows:
        raise validation_error({"file": f"文件数据行超过上限（{settings.max_import_rows} 行）。"})
    if folder_policy not in (FOLDER_CATEGORY, FOLDER_TAGS, FOLDER_IGNORE):
        raise validation_error({"folder_policy": "无效的目录处理策略。"})
    job_id = str(uuid.uuid4())
    root = resolve_temp_root(settings)
    key = job_id.replace("-", "")
    root.joinpath(f"{key}.src").write_bytes(content)
    analysis = analyze(db, parsed)
    job = ImportJob(
        id=job_id,
        source_type=source_type,
        original_filename=sanitize_filename(original_filename),
        file_sha256=sha256_bytes(content),
        temp_file_key=key,
        session_nonce_hash=_session_nonce_hash(session_cookie),
        options_json=json.dumps(
            {"duplicate_policy": DUPLICATE_SKIP, "folder_policy": folder_policy}, ensure_ascii=False
        ),
        summary_json=json.dumps(make_summary(parsed, analysis), ensure_ascii=False),
        category_tree_revision=get_tree_revision(db),
        status=STATUS_PREVIEWED,
        expires_at=now_utc() + timedelta(seconds=settings.import_job_expire_seconds),
    )
    try:
        db.add(job)
        db.flush()
    except Exception:
        # 提交失败时清理已写入的临时文件，避免孤儿文件
        TempFiles(resolve_temp_root(settings), key).source.unlink(missing_ok=True)
        raise
    return job, parsed


def get_job(db: Session, job_id: str) -> ImportJob:
    job = db.get(ImportJob, job_id)
    if job is None:
        raise not_found("导入任务不存在。")
    return job


def update_options(
    db: Session,
    job: ImportJob,
    settings: Settings,
    *,
    duplicate_policy: str,
    folder_policy: str,
) -> dict:
    if job.status != STATUS_PREVIEWED:
        raise conflict("任务已不是预览状态，无法修改策略。")
    if duplicate_policy not in (DUPLICATE_SKIP, DUPLICATE_OVERWRITE, DUPLICATE_KEEP):
        raise validation_error({"duplicate_policy": "无效的重复策略。"})
    if folder_policy not in (FOLDER_CATEGORY, FOLDER_TAGS, FOLDER_IGNORE):
        raise validation_error({"folder_policy": "无效的目录处理策略。"})
    options = {"duplicate_policy": duplicate_policy, "folder_policy": folder_policy}
    parsed = parse_content(
        job.source_type,
        TempFiles(resolve_temp_root(settings), job.temp_file_key).source.read_bytes(),
    )
    analysis = analyze(db, parsed)
    job.options_json = json.dumps(options, ensure_ascii=False)
    job.summary_json = json.dumps(make_summary(parsed, analysis), ensure_ascii=False)
    db.flush()
    return json.loads(job.summary_json)


def mark_stale_running_as_failed(db: Session) -> int:
    """服务重启后 RUNNING 任务标记为 FAILED（一次性修复）。"""
    return (
        db.execute(
            update(ImportJob)
            .where(ImportJob.status == STATUS_RUNNING)
            .values(
                status=STATUS_FAILED,
                executed_at=now_utc(),
                error_summary="服务重启导致任务中断，请重新上传。",
            )
        ).rowcount
        or 0
    )


def expire_jobs(db: Session, settings: Settings) -> dict:
    """清理过期/终态任务：PREVIEWED 超时置 EXPIRED 并删文件；
    SUCCEEDED/FAILED 超过保留期（import_job_expire_seconds，默认 24h）删除行与文件。"""
    root = resolve_temp_root(settings)
    expired_count = 0
    for job in db.scalars(
        select(ImportJob).where(
            ImportJob.status == STATUS_PREVIEWED, ImportJob.expires_at < now_utc()
        )
    ).all():
        job.status = STATUS_EXPIRED
        root.joinpath(f"{job.temp_file_key}.src").unlink(missing_ok=True)
        expired_count += 1
    terminal = db.scalars(
        select(ImportJob).where(
            ImportJob.status.in_((STATUS_SUCCEEDED, STATUS_FAILED)),
            (
                ImportJob.executed_at.is_(None)
                | (
                    ImportJob.executed_at
                    < now_utc() - timedelta(seconds=settings.import_job_expire_seconds)
                )
            ),
        )
    ).all()
    removed = 0
    for job in terminal:
        root.joinpath(f"{job.temp_file_key}.src").unlink(missing_ok=True)
        db.delete(job)
        removed += 1
    return {"expired": expired_count, "terminal_removed": removed}


def cleanup_orphan_files(db: Session, settings: Settings, older_seconds: int = 3600) -> int:
    """清理无对应任务记录的临时文件（预览提交失败等路径产生的孤儿）。"""
    root = resolve_temp_root(settings)
    tracked = set(db.scalars(select(ImportJob.temp_file_key)).all())
    cutoff_ts = (now_utc() - timedelta(seconds=older_seconds)).timestamp()
    removed = 0
    for path in root.glob("*.src"):
        if path.name.removesuffix(".src") in tracked:
            continue
        try:
            too_old = path.stat().st_mtime < cutoff_ts
        except OSError:
            too_old = False
        if too_old:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def cancel_job(db: Session, job: ImportJob, settings: Settings) -> None:
    if job.status != STATUS_PREVIEWED:
        raise conflict("任务已不是预览状态，无法取消。")
    job.status = STATUS_EXPIRED
    resolve_temp_root(settings).joinpath(f"{job.temp_file_key}.src").unlink(missing_ok=True)
    db.flush()


# ---------- 执行 ----------


class ImportExecutor:
    """一次性执行器。失败时回滚业务写入后持久化 FAILED 状态（无半批数据）。"""

    def __init__(self, db: Session, settings: Settings, session_cookie: str | None):
        self.db = db
        self.settings = settings
        self.session_nonce = _session_nonce_hash(session_cookie)
        self._categories_by_key: dict[tuple[int | None, str], Category] = {}
        self._tags_by_norm: dict[str, Tag] = {}
        self._existing: dict[str, list[dict]] = {}
        self._new_category_ids: set[int] = set()
        self._new_tag_ids: set[int] = set()

    def _load_existing(self) -> None:
        rows = self.db.execute(
            select(Bookmark.id, Bookmark.normalized_url, Bookmark.deleted_at).order_by(Bookmark.id)
        ).all()
        for row in rows:
            self._existing.setdefault(row.normalized_url, []).append(
                {"id": row.id, "deleted_at": row.deleted_at}
            )

    def _load_categories(self) -> None:
        self._categories_by_key = {
            (c.parent_id, c.normalized_name): c
            for c in self.db.execute(select(Category)).scalars().all()
        }

    def _load_tags(self) -> None:
        self._tags_by_norm = {
            t.normalized_name: t for t in self.db.execute(select(Tag)).scalars().all()
        }

    def _ensure_category_path(self, path: tuple[str, ...]) -> int | None:
        parent_id: int | None = None
        for part in path:
            display = strip_display_name(part)
            if not display:
                continue
            normalized = normalize_name(display)
            category = self._categories_by_key.get((parent_id, normalized))
            if category is None:
                category = Category(name=display, normalized_name=normalized, parent_id=parent_id)
                self.db.add(category)
                self.db.flush()
                self._new_category_ids.add(category.id)
                self._categories_by_key[(parent_id, normalized)] = category
            parent_id = category.id
        return parent_id

    def _resolve_tag(self, name: str) -> Tag:
        display = strip_display_name(name)
        if not display:
            raise validation_error({"detail": "标签名不能为空。"})
        normalized = normalize_name(display)
        tag = self._tags_by_norm.get(normalized)
        if tag is None:
            tag = Tag(name=display, normalized_name=normalized)
            self.db.add(tag)
            self.db.flush()
            self._new_tag_ids.add(tag.id)
            self._tags_by_norm[normalized] = tag
        return tag

    def execute(self, job: ImportJob) -> dict:
        """执行入口：claim + 全量校验 + 按策略写库；终态落库才返回。"""
        if job.status != STATUS_PREVIEWED:
            raise AppError(409, "conflict", f"任务当前状态为 {job.status}，无法执行。")
        if job.session_nonce_hash != self.session_nonce:
            raise AppError(403, "forbidden", "任务不属于当前会话，请重新预览。")
        if job.expires_at < now_utc():
            self._finalize(job, STATUS_EXPIRED, None)
            raise AppError(410, "expired", "任务已过期，请重新上传。")
        try:
            # claim：原子 PREVIEWED -> RUNNING
            result = self.db.execute(
                update(ImportJob)
                .where(ImportJob.id == job.id, ImportJob.status == STATUS_PREVIEWED)
                .values(status=STATUS_RUNNING)
            )
            if result.rowcount != 1:
                raise conflict("任务已被执行或状态已变化。")
            self.db.flush()
            options = json.loads(job.options_json)
            content = TempFiles(
                resolve_temp_root(self.settings), job.temp_file_key
            ).source.read_bytes()
            if sha256_bytes(content) != job.file_sha256:
                raise AppError(409, "conflict", "上传文件已变化，请重新预览。")
            parsed = parse_content(job.source_type, content)
            if parsed.errors:
                raise AppError(
                    422, "validation_error", f"解析错误（{len(parsed.errors)} 条），不允许执行。"
                )
            if get_tree_revision(self.db) != job.category_tree_revision:
                raise AppError(409, "conflict", "分类结构已变化，请刷新预览后重试。")
            self._load_existing()
            self._load_categories()
            self._load_tags()
            counts = self._run_items(parsed, options)
            if self._new_category_ids:
                # 目录转分类只递增一次 tree revision（CAS 使用预览时的版本）
                bump_tree_revision(self.db, job.category_tree_revision)
            job.status = STATUS_SUCCEEDED
            job.executed_at = now_utc()
            summary = json.loads(job.summary_json)
            summary["execution"] = counts
            job.summary_json = json.dumps(summary, ensure_ascii=False)
            self.db.flush()
            return counts
        except AppError:
            self._finalize_failed(job)
            raise
        except Exception:
            self._finalize_failed(job)
            raise AppError(500, "internal_error", "导入执行失败，请检查文件后重试。") from None

    def _finalize_failed(self, job: ImportJob) -> None:
        """回滚业务写入后持久化 FAILED 状态（保证无半批数据且失败可见）。"""
        self.db.rollback()
        stale = self.db.get(ImportJob, job.id)
        if stale is None:
            return
        if stale.status in (STATUS_PREVIEWED, STATUS_RUNNING):
            stale.status = STATUS_FAILED
            stale.executed_at = now_utc()
            stale.error_summary = "执行失败：任务已终止。"
        self.db.commit()

    def _finalize(self, job: ImportJob, status: str, error_summary: str | None) -> None:
        self.db.rollback()
        stale = self.db.get(ImportJob, job.id)
        if stale is not None:
            stale.status = status
            stale.executed_at = now_utc()
            stale.error_summary = error_summary
            self.db.commit()

    # ---- 策略执行（同一事务） ----

    def _run_items(self, parsed: ParseResult, options: dict) -> dict:
        duplicate_policy = options["duplicate_policy"]
        folder_policy = options["folder_policy"]
        created_in_file: set[str] = set()
        counts = {"created": 0, "overwritten": 0, "skipped": 0}
        for item in parsed.items:
            title = item.title
            if not title:
                if options.get("empty_title_strategy") == EMPTY_TITLE_SKIP:
                    counts["skipped"] += 1
                    continue
                from urllib.parse import urlsplit

                title = urlsplit(item.url).hostname or item.url

            category_id: int | None = None
            row_tags: list[str] = []
            if folder_policy == FOLDER_CATEGORY and item.folder_path:
                category_id = self._ensure_category_path(item.folder_path)
            elif folder_policy == FOLDER_TAGS:
                row_tags = [part for part in item.folder_path if part.strip()]
            row_tags = list(dict.fromkeys(row_tags + item.tags))

            existing = self._existing.get(item.normalized_url) or []
            active = [b for b in existing if b["deleted_at"] is None]
            trashed_count = len(existing) - len(active)

            if duplicate_policy == DUPLICATE_SKIP:
                # 重复判断范围：正常列表 + 回收站 + 本次文件内（首条之外均跳过）
                if existing or item.normalized_url in created_in_file:
                    counts["skipped"] += 1
                    continue
            elif duplicate_policy == DUPLICATE_OVERWRITE:
                if item.normalized_url in created_in_file:
                    counts["skipped"] += 1  # 文件内重复以首次出现为代表
                    continue
                if len(active) > 1:
                    raise conflict(
                        "存在多条相同网址的书签，覆盖会产生歧义，请改用「跳过」或「保留」。"
                    )
                if len(active) == 1:
                    # 只更新未删除目标；回收站同键不自动复活
                    self._overwrite(active[0]["id"], item, title, category_id, row_tags)
                    counts["overwritten"] += 1
                    created_in_file.add(item.normalized_url)
                    continue
                if trashed_count:
                    raise conflict("只与回收站记录重复，不能自动复活；请先恢复或改用「保留」。")
            self._create(item, title, category_id, row_tags)
            counts["created"] += 1
            created_in_file.add(item.normalized_url)
        counts["categories_created"] = len(self._new_category_ids)
        counts["tags_created"] = len(self._new_tag_ids)
        return counts

    def _create(self, item, title: str, category_id: int | None, row_tags: list[str]) -> None:
        bookmark = Bookmark(
            title=title,
            url=item.url,
            normalized_url=item.normalized_url,
            description=item.description,
            category_id=category_id,
            is_favorite=item.favorite,
            favicon_url=item.favicon_url,
        )
        self.db.add(bookmark)
        self.db.flush()
        for tag_name in row_tags:
            bookmark.tags.append(self._resolve_tag(tag_name))
        self.db.flush()

    def _overwrite(
        self,
        bookmark_id: int,
        item,
        title: str,
        category_id: int | None,
        row_tags: list[str],
    ) -> None:
        bookmark = self.db.get(Bookmark, bookmark_id)
        bookmark.title = title
        bookmark.description = item.description
        bookmark.category_id = category_id
        bookmark.is_favorite = item.favorite
        bookmark.favicon_url = item.favicon_url
        bookmark.version += 1  # 覆盖也是写操作：推进版本以触发乐观锁（评审 H3/M3）
        bookmark.tags.clear()
        self.db.flush()
        for tag_name in row_tags:
            bookmark.tags.append(self._resolve_tag(tag_name))
        self.db.flush()

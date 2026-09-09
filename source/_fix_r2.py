"""CodeReview 修复第二批（L1/L2/M2）。"""
from pathlib import Path

ROOT = Path(".")


def patch(rel: str, pairs: list[tuple[str, str]]) -> None:
    p = ROOT / rel
    t = p.read_text(encoding="utf-8")
    for old, new in pairs:
        assert old in t, f"{rel}: NOT FOUND -> {old[:100]!r}"
        t = t.replace(old, new, 1)
    p.write_text(t, encoding="utf-8")
    print("patched", rel)


# ---------- L1: trash 查询串 URL 编码 ----------
patch("app/routers/trash.py", [
    (
        "from sqlalchemy.orm import Session",
        "from sqlalchemy.orm import Session\nfrom urllib.parse import urlencode",
    ),
    (
        '''        params_dict["page"] = str(result.page)
        return {"redirect": "/trash?" + "&".join(f"{k}={v}" for k, v in params_dict.items())}''',
        '''        params_dict["page"] = str(result.page)
        return {"redirect": "/trash?" + urlencode(params_dict)}''',
    ),
    (
        '''        if p != 1:
            params_dict["page"] = str(p)
        query = "&".join(f"{k}={v}" for k, v in params_dict.items())
        return "/trash" + (f"?{query}" if query else "")''',
        '''        if p != 1:
            params_dict["page"] = str(p)
        query = urlencode(params_dict)
        return "/trash" + (f"?{query}" if query else "")''',
    ),
])

# ---------- L2: config 死代码分支 ----------
patch("app/config.py", [
    (
        """        database_url = get("DATABASE_URL")
        if database_url is None:
            database_url = (
                f"sqlite:///{APP_ROOT / 'dev_data' / 'bookmarks.db'}"
                if app_env == "development"
                else f"sqlite:///{APP_ROOT / 'dev_data' / 'bookmarks.db'}"
            )""",
        """        database_url = get("DATABASE_URL")
        if database_url is None:
            # 默认开发库；生产部署必须显式配置 DATABASE_URL（部署模板已指向 /data）
            database_url = f"sqlite:///{APP_ROOT / 'dev_data' / 'bookmarks.db'}"''',
    ),
])

# ---------- M2: 导入临时文件生命周期 ----------
# 1) 预览创建失败时清理刚写入的文件
patch("app/services/import_service.py", [
    (
        """    job = ImportJob(
        id=job_id,
        source_type=source_type,
        original_filename=sanitize_filename(original_filename),
        file_sha256=sha256_bytes(content),
        temp_file_key=key,
        session_nonce_hash=_session_nonce_hash(session_cookie),
        options_json=json.dumps(
            {"duplicate_policy": DUPLICATE_SKIP, "folder_policy": folder_policy},
            ensure_ascii=False,
        ),
        summary_json=json.dumps(make_summary(parsed, analysis), ensure_ascii=False),
        category_tree_revision=get_tree_revision(db),
        status=STATUS_PREVIEWED,
        expires_at=now_utc() + timedelta(seconds=settings.import_job_expire_seconds),
    )
    db.add(job)
    db.flush()
    return job, parsed""",
        """    job = ImportJob(
        id=job_id,
        source_type=source_type,
        original_filename=sanitize_filename(original_filename),
        file_sha256=sha256_bytes(content),
        temp_file_key=key,
        session_nonce_hash=_session_nonce_hash(session_cookie),
        options_json=json.dumps(
            {"duplicate_policy": DUPLICATE_SKIP, "folder_policy": folder_policy},
            ensure_ascii=False,
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
    return job, parsed""",
    ),
    # 2) expire_jobs 扩展：过期 PREVIEWED 与超过保留期的终态任务一并清理
    (
        """def expire_jobs(db: Session, settings: Settings) -> int:
    jobs = db.scalars(
        select(ImportJob).where(
            ImportJob.status == STATUS_PREVIEWED, ImportJob.expires_at < now_utc()
        )
    ).all()
    root = resolve_temp_root(settings)
    for job in jobs:
        job.status = STATUS_EXPIRED
        root.joinpath(f"{job.temp_file_key}.src").unlink(missing_ok=True)
    return len(jobs)""",
        """def expire_jobs(db: Session, settings: Settings) -> dict:
    \"\"\"清理过期/终态任务：PREVIEWED 超时置 EXPIRED 并删文件；
    SUCCEEDED/FAILED 超过保留期（import_job_expire_seconds，默认 24h）删除行与文件。\"\"\"
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
            ImportJob.executed_at
            < now_utc() - timedelta(seconds=settings.import_job_expire_seconds),
        )
    ).all()
    removed = 0
    for job in terminal:
        root.joinpath(f"{job.temp_file_key}.src").unlink(missing_ok=True)
        db.delete(job)
        removed += 1
    return {"expired": expired_count, "terminal_removed": removed}""",
    ),
    # 3) 孤儿文件清扫
    (
        """def cancel_job(db: Session, job: ImportJob, settings: Settings) -> None:""",
        """def cleanup_orphan_files(db: Session, settings: Settings, older_seconds: int = 3600) -> int:
    \"\"\"清理无对应任务记录的临时文件（预览提交失败等路径产生的孤儿）。\"\"\"
    root = resolve_temp_root(settings)
    tracked = set(
        db.scalars(select(ImportJob.temp_file_key)).all()
    )
    cutoff = now_utc() - timedelta(seconds=older_seconds)
    removed = 0
    for path in root.glob("*.src"):
        if path.name.removesuffix(".src") in tracked:
            continue
        if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def cancel_job(db: Session, job: ImportJob, settings: Settings) -> None:""",
    ),
])

# cleanup CLI 适配
patch("scripts/cleanup_import_jobs.py", [
    (
        """    with session_factory() as session:
        expired = import_service.expire_jobs(session, settings)
        failed = import_service.mark_stale_running_as_failed(session)
        session.commit()
        logger.info("expired=%d stale_failed=%d", expired, failed)""",
        """    with session_factory() as session:
        cleanup = import_service.expire_jobs(session, settings)
        failed = import_service.mark_stale_running_as_failed(session)
        orphans = import_service.cleanup_orphan_files(session, settings)
        session.commit()
        logger.info(
            "expired=%d terminal_removed=%d stale_failed=%d orphans=%d",
            cleanup["expired"],
            cleanup["terminal_removed"],
            failed,
            orphans,
        )""",
    ),
])

# cron 示例补清理任务
p = ROOT / "../deploy/cron-backup.example"
t = p.read_text(encoding="utf-8")
anchor = "# 定期恢复演练（月度，独立临时目录，不触碰生产库）："
assert anchor in t
t = t.replace(
    anchor,
    "# 每日清理过期/终态导入任务与孤儿临时文件：\n"
    "0 3 * * * root cd /opt/bookmark && docker compose exec -T app python -m scripts.cleanup_import_jobs >> /var/log/bookmark-backup.log 2>&1\n\n"
    + anchor,
    1,
)
p.write_text(t, encoding="utf-8")
print("patched cron-backup.example")
print("ALL ROUND2 OK")

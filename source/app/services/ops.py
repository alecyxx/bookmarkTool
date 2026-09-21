"""运维能力（BM-V1-804~807）。

- check_ready：/health/ready 探针——数据库可达、可写、迁移 revision 与应用期望一致；
  失败时对外只暴露稳定 code（details 只进服务日志）；
- 备份：SQLite 在线 Backup API -> 临时文件 -> integrity_check -> SHA-256 清单 ->
  原子重命名；保留策略（日 7/周 4/月 12）为纯函数可单测；
- 恢复：独立临时目录 + 迁移到目标版本 + 统计核对（不覆盖生产库）。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import sqlite3
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

logger = logging.getLogger("ops")

# 与 migrations/versions 当前 head 保持一致（漂移由测试防护）
EXPECTED_DB_REVISION = "0002_bookmark_sort_weight"

# 备份文件名：{type}-{YYYYMMDD-HHMMSS}-{rand8}.db
BACKUP_NAME_RE = re.compile(r"^(daily|weekly|monthly)-(\d{8}-\d{6})-[0-9a-f]{8}\.db$")


# ---------- 数据库路径与 revision ----------


def database_file_path(database_url: str) -> Path:
    if not database_url.startswith("sqlite:///"):
        raise ValueError(f"unsupported database url: {database_url}")
    raw = database_url[len("sqlite:///") :]
    # 支持 sqlite:///相对路径（相对当前工作目录）与绝对路径
    path = Path(raw)
    if not path.is_absolute():
        from app.config import APP_ROOT

        path = APP_ROOT / path
    return path


def read_db_revision(db_file: Path) -> str | None:
    """返回 alembic_version 当前版本；库未初始化/不存在返回 None。"""
    if not db_file.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True, timeout=5)
        try:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def _probe_engine(database_url: str) -> Engine:
    # 与 read_db_revision 使用同一套 APP_ROOT/绝对路径解析，避免相对 URL
    # 出现“读的是一个库、写探针的是另一个库”。
    db_file = database_file_path(database_url)
    return create_engine(f"sqlite:///{db_file}", connect_args={"timeout": 5})


def is_db_writable(engine: Engine) -> bool:
    """事务内创建并清理探针表；能完成事务即视为可写。"""
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE IF NOT EXISTS _ready_probe (id INTEGER)"))
            conn.execute(text("DELETE FROM _ready_probe"))
        return True
    except Exception:  # noqa: BLE001 - 探针失败即视为不可写
        logger.exception("readiness write probe failed")
        return False


def check_ready(database_url: str) -> dict:
    """返回 {status: ok|not_ready, reason: code}；reason 仅内部消费。"""
    db_file = database_file_path(database_url)
    if not db_file.exists():
        return {"status": "not_ready", "reason": "db_missing"}
    revision = read_db_revision(db_file)
    if revision != EXPECTED_DB_REVISION:
        return {"status": "not_ready", "reason": "revision_mismatch"}
    engine = _probe_engine(database_url)
    try:
        if not is_db_writable(engine):
            return {"status": "not_ready", "reason": "db_not_writable"}
    finally:
        engine.dispose()
    return {"status": "ok", "reason": None}


# ---------- 备份 ----------


def backup_name(backup_type: str, when: datetime) -> str:
    return f"{backup_type}-{when.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}.db"


def _sqlite_backup(source: Path, target_tmp: Path) -> None:
    """SQLite Backup API：运行中一致备份（含 WAL 内容）。"""
    src = sqlite3.connect(str(source), timeout=30)
    dst = sqlite3.connect(str(target_tmp), timeout=30)
    try:
        with dst:
            src.backup(dst)
    finally:
        dst.close()
        src.close()


def integrity_ok(db_file: Path) -> bool:
    conn = sqlite3.connect(str(db_file), timeout=10)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return bool(row) and row[0] == "ok"
    finally:
        conn.close()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_sha256_manifest(db_file: Path) -> Path:
    manifest = db_file.with_suffix(db_file.suffix + ".sha256")
    manifest.write_text(f"{sha256_file(db_file)}  {db_file.name}\n", encoding="ascii")
    return manifest


def create_backup(
    database_url: str,
    backup_dir: Path,
    backup_type: str,
    when: datetime | None = None,
) -> Path:
    """执行一致性备份并返回备份文件路径（失败不产生半成品）。"""
    if backup_type not in ("daily", "weekly", "monthly"):
        raise ValueError(f"unknown backup type: {backup_type}")
    source = database_file_path(database_url)
    if not source.is_file():
        raise RuntimeError(f"database file does not exist: {source}")
    if read_db_revision(source) is None:
        raise RuntimeError("database schema is not initialized")
    backup_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = backup_dir / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    backup_time = when or datetime.now(UTC).replace(tzinfo=None)
    for _ in range(10):
        target_name = backup_name(backup_type, backup_time)
        target = backup_dir / target_name
        target_manifest = target.with_suffix(target.suffix + ".sha256")
        if not target.exists() and not target_manifest.exists():
            break
    else:  # pragma: no cover - UUID 连续碰撞仅作防御
        raise RuntimeError("could not allocate unique backup name")
    tmp_path = tmp_dir / target_name
    tmp_manifest = tmp_dir / f"{target_name}.sha256"
    try:
        _sqlite_backup(source, tmp_path)
        if not integrity_ok(tmp_path):
            raise RuntimeError("backup integrity check failed")
        # 在临时区先完成哈希清单；只有数据库与清单都就绪后才发布最终文件。
        tmp_manifest.write_text(
            f"{sha256_file(tmp_path)}  {target_name}\n",
            encoding="ascii",
        )
        os.replace(tmp_path, target)
        os.replace(tmp_manifest, target_manifest)
        logger.info("backup created file=%s bytes=%d", target.name, target.stat().st_size)
        return target
    except Exception:
        tmp_path.unlink(missing_ok=True)
        tmp_manifest.unlink(missing_ok=True)
        # 随机文件名保证本次 target 不会覆盖既有成功备份；发布任一步失败时
        # 清理本次可能已经移动出的半成品，避免后续保留/恢复逻辑误认。
        target.unlink(missing_ok=True)
        target_manifest.unlink(missing_ok=True)
        logger.exception("backup failed")
        raise
    finally:
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except OSError:
            pass


def _bucket_for(day: date) -> str:
    """按文件自身日期归属保留桶（d/w/m 由前缀标识）。"""
    return day.strftime("%Y-%m")


def classify_files(files: list[Path], now: datetime) -> dict[str, list[Path]]:
    """按前缀分类（未识别文件忽略）。"""
    buckets: dict[str, list[Path]] = {"daily": [], "weekly": [], "monthly": []}
    for path in files:
        match = BACKUP_NAME_RE.match(path.name)
        if match:
            buckets[match.group(1)].append(path)
    return buckets


def retention_plan(
    files: list[Path], now: datetime, daily: int = 7, weekly: int = 4, monthly: int = 12
) -> set[Path]:
    """纯函数保留计划：日取最近 daily 个文件；周取每周最早? 简化按规范实现：
    - daily: 保留最近 daily 个
    - weekly: 保留过去 weekly 周内每周一份（取该周最新一份；超过 weekly 周的删除）
    - monthly: 保留过去 monthly 个月内每月一份
    返回应保留的文件集合（未识别的文件不参与管理）。"""
    buckets = classify_files(files, now)
    keep: set[Path] = set()

    def parse_stamp(name: str) -> datetime:
        match = BACKUP_NAME_RE.match(name)
        return datetime.strptime(match.group(2), "%Y%m%d-%H%M%S") if match else datetime.min

    def sorted_by_newest(items: list[Path]) -> list[Path]:
        return sorted(items, key=lambda p: parse_stamp(p.name), reverse=True)

    # daily：最近 N 个
    keep.update(sorted_by_newest(buckets["daily"])[:daily])

    # weekly：按 ISO 周分组取最新，最多 weekly 周（当前周起向前）
    week_buckets: dict[str, Path] = {}
    for path in buckets["weekly"]:
        stamp = parse_stamp(path.name)
        iso = stamp.isocalendar()
        key = f"{iso.year}-W{iso.week:02d}"
        existing = week_buckets.get(key)
        if existing is None or stamp > parse_stamp(existing.name):
            week_buckets[key] = path
    # 用日期回退而不是手工递减周数，正确覆盖 ISO 年切换及第 53 周。
    # 分组键与允许键统一使用两位周数，避免 W1 与 W01 永远无法匹配。
    weeks: list[str] = []
    for offset in range(weekly):
        iso = (now - timedelta(weeks=offset)).isocalendar()
        weeks.append(f"{iso.year}-W{iso.week:02d}")
    for key, path in week_buckets.items():
        if key in weeks:
            keep.add(path)

    # monthly：按年月分组取最新，保留最近 monthly 个月
    month_buckets: dict[str, Path] = {}
    for path in buckets["monthly"]:
        stamp = parse_stamp(path.name)
        key = f"{stamp.year:04d}-{stamp.month:02d}"
        existing = month_buckets.get(key)
        if existing is None or stamp > parse_stamp(existing.name):
            month_buckets[key] = path
    allowed_months: set[str] = set()
    year, month = now.year, now.month
    for _ in range(monthly):
        allowed_months.add(f"{year:04d}-{month:02d}")
        month -= 1
        if month < 1:
            year -= 1
            month = 12
    for key, path in month_buckets.items():
        if key in allowed_months:
            keep.add(path)
    return keep


def apply_retention(
    backup_dir: Path,
    now: datetime | None = None,
    daily: int = 7,
    weekly: int = 4,
    monthly: int = 12,
) -> list[Path]:
    """清理过期备份（含 .sha256）；返回被删除的文件。"""
    now = now or datetime.now()
    files = [p for p in backup_dir.iterdir() if p.is_file() and p.suffix == ".db"]
    keep = retention_plan(files, now, daily=daily, weekly=weekly, monthly=monthly)
    removed: list[Path] = []
    for path in files:
        if path not in keep:
            path.unlink(missing_ok=True)
            path.with_suffix(path.suffix + ".sha256").unlink(missing_ok=True)
            removed.append(path)
    return removed


# ---------- 恢复验证 ----------


def verify_backup_file(backup_file: Path) -> str:
    """校验 SHA-256 清单与完整性；返回 sha256（异常抛 RuntimeError）。"""
    manifest = backup_file.with_suffix(backup_file.suffix + ".sha256")
    if not manifest.is_file():
        raise RuntimeError(f"SHA-256 manifest missing for {backup_file.name}")
    parts = manifest.read_text(encoding="ascii").strip().split()
    if (
        len(parts) != 2
        or parts[1] != backup_file.name
        or not re.fullmatch(r"[0-9a-f]{64}", parts[0])
    ):
        raise RuntimeError(f"invalid SHA-256 manifest for {backup_file.name}")
    expected = parts[0]
    actual = sha256_file(backup_file)
    if expected != actual:
        raise RuntimeError(f"SHA-256 mismatch for {backup_file.name}")
    try:
        ok = integrity_ok(backup_file)
    except sqlite3.Error as exc:
        raise RuntimeError(f"integrity_check failed for {backup_file.name}") from exc
    if not ok:
        raise RuntimeError(f"integrity_check failed for {backup_file.name}")
    return actual


def restore_verify(database_url: str, backup_file: Path, work_dir: Path) -> dict:
    """在独立临时目录恢复备份并核对（BM-V1-807）；不触碰生产库。

    返回统计摘要：书签/回收站/分类/标签/管理员/抽样关系。
    """
    if not backup_file.exists():
        raise FileNotFoundError(f"backup file not found: {backup_file}")
    verify_backup_file(backup_file)
    work_dir.mkdir(parents=True, exist_ok=True)
    restored = work_dir / "restored.db"
    if restored.exists():
        restored.unlink()
    shutil.copyfile(backup_file, restored)
    # 校验 + 迁移到目标版本（使用临时 alembic 配置指向 restored.db）
    _migrate_to_head(restored)
    return _collect_stats(restored)


def _migrate_to_head(db_file: Path) -> None:
    """对独立恢复库执行 alembic upgrade head。"""
    from alembic import command
    from alembic.config import Config

    source_root = Path(__file__).resolve().parents[2]
    config = Config(str(source_root / "alembic.ini"))
    config.set_main_option("script_location", str(source_root / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_file}")
    command.upgrade(config, "head")


def _collect_stats(db_file: Path) -> dict:
    conn = sqlite3.connect(str(db_file), timeout=10)
    try:
        counts = {
            "bookmarks": conn.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0],
            "trashed": conn.execute(
                "SELECT COUNT(*) FROM bookmarks WHERE deleted_at IS NOT NULL"
            ).fetchone()[0],
            "categories": conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0],
            "tags": conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0],
            "users": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        }
        # 抽样：任一书签应可解析其分类/标签关系存在
        sample = conn.execute(
            "SELECT id, category_id FROM bookmarks "
            "WHERE deleted_at IS NULL AND category_id IS NOT NULL ORDER BY id LIMIT 1"
        ).fetchone()
        if sample:
            exists = conn.execute(
                "SELECT COUNT(*) FROM categories WHERE id = ?", (sample[1],)
            ).fetchone()[0]
            counts["sample_category_linked"] = exists == 1
        else:
            counts["sample_category_linked"] = None
        counts["revision"] = read_db_revision(db_file)
        return counts
    finally:
        conn.close()

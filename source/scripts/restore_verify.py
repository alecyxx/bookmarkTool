"""恢复验证工具 CLI（BM-V1-807）。

用法：
    python -m scripts.restore_verify --backup <backup.db> [--work-dir DIR]

- 校验 SHA-256 清单与 PRAGMA integrity_check；
- 复制到独立临时目录（默认 <备份同目录>/restore-work/<随机>），迁移到目标版本；
- 输出核心计数与抽样关系（分类/标签/回收站/管理员），绝不触碰生产库。
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import load_settings
from app.services.ops import restore_verify

logger = logging.getLogger("restore_verify")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="从备份恢复并核对（独立临时目录）")
    parser.add_argument("--backup", required=True, help="备份 .db 文件路径")
    parser.add_argument(
        "--work-dir", default=None, help="恢复工作目录（默认备份同目录 restore-work）"
    )
    args = parser.parse_args()

    settings = load_settings()
    backup_file = Path(args.backup).expanduser().resolve()
    work_root = Path(args.work_dir) if args.work_dir else backup_file.parent / "restore-work"
    work_dir = work_root / uuid.uuid4().hex[:10]
    try:
        stats = restore_verify(settings.database_url, backup_file, work_dir)
        print("RESTORE_OK", backup_file)
        print("RESTORE_STATS", stats)
        logger.info("restore verified stats=%s", stats)
    except Exception as exc:  # noqa: BLE001 - CLI 出口
        print(f"RESTORE_FAILED {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()

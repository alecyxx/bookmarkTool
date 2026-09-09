"""一致性备份 CLI（BM-V1-805/806）。

用法：
    python -m scripts.backup [--type daily|weekly|monthly] [--dir DIR]
                             [--keep-daily 7] [--keep-weekly 4] [--keep-monthly 12]
                             [--offsite-dir DIR] [--offsite-encrypt-key KEY]

- SQLite Backup API -> 临时文件 -> integrity_check -> SHA-256 -> 原子重命名；
- 失败不覆盖最近成功备份（.tmp 清理）；
- 保留计划为纯函数（ops.retention_plan），每天/周/月调用一次即可；
- 异机副本：把最新备份复制到 offsite-dir（可为 rclone/对象存储挂载点）；
  设置 OFF_SITE_BACKUP_DIR 与 BACKUP_ENCRYPT_PASSPHRASE 时副本使用 openssl AES-256 加密
  （本地明文备份始终保留；上传失败可观测且不影响本地成功备份）。
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import load_settings
from app.services.ops import apply_retention, create_backup, sha256_file

logger = logging.getLogger("backup")


def _log_configured() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _offsite_copy(backup_file: Path, offsite_dir: Path, passphrase: str | None) -> Path | None:
    """异机/对象存储副本；提供口令时加密（副本 .db.enc + .sha256.enc）。"""
    offsite_dir.mkdir(parents=True, exist_ok=True)
    if passphrase:
        enc_target = offsite_dir / (backup_file.name + ".enc")
        subprocess.run(
            [
                "openssl",
                "enc",
                "-aes-256-cbc",
                "-pbkdf2",
                "-iter",
                "200000",
                "-salt",
                "-in",
                str(backup_file),
                "-out",
                str(enc_target),
                "-pass",
                "env:BACKUP_ENCRYPT_PASSPHRASE",
            ],
            check=True,
            env={**os.environ, "BACKUP_ENCRYPT_PASSPHRASE": passphrase},
        )
        digest_target = offsite_dir / (backup_file.name + ".sha256.enc")
        (offsite_dir / digest_target.name).write_text(
            f"{sha256_file(backup_file)}  {backup_file.name}\n", encoding="ascii"
        )
        return enc_target
    target = offsite_dir / backup_file.name
    shutil.copy2(backup_file, target)
    shutil.copy2(
        backup_file.with_suffix(backup_file.suffix + ".sha256"),
        offsite_dir / (backup_file.name + ".sha256"),
    )
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Bookmark Manager V1 一致性备份")
    parser.add_argument("--type", default="daily", choices=("daily", "weekly", "monthly"))
    parser.add_argument("--dir", default=None, help="备份目录（默认 <数据目录>/backups）")
    parser.add_argument("--keep-daily", type=int, default=7)
    parser.add_argument("--keep-weekly", type=int, default=4)
    parser.add_argument("--keep-monthly", type=int, default=12)
    parser.add_argument("--skip-retention", action="store_true")
    args = parser.parse_args()
    _log_configured()

    settings = load_settings()
    from app.services.ops import database_file_path

    backup_dir = (
        Path(args.dir) if args.dir else database_file_path(settings.database_url).parent / "backups"
    )
    backup_dir.mkdir(parents=True, exist_ok=True)

    backup_file = create_backup(settings.database_url, backup_dir, args.type)
    print(f"BACKUP_OK {backup_file}")

    offsite = os.environ.get("OFF_SITE_BACKUP_DIR")
    if offsite:
        try:
            copied = _offsite_copy(
                backup_file, Path(offsite), os.environ.get("BACKUP_ENCRYPT_PASSPHRASE")
            )
            logger.info("offsite copy created path=%s", copied)
        except Exception:  # noqa: BLE001 - 上传失败可观测，本地备份不受影响
            logger.exception("offsite copy failed; local backup kept")
            print("OFFSITE_FAILED")

    if not args.skip_retention:
        removed = apply_retention(
            backup_dir,
            daily=args.keep_daily,
            weekly=args.keep_weekly,
            monthly=args.keep_monthly,
        )
        logger.info("retention removed=%d files", len(removed))


if __name__ == "__main__":
    main()

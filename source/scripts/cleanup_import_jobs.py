"""清理过期导入任务与临时文件（BM-V1-601；建议每日 cron 或 systemd timer 执行）。

用法：
    python -m scripts.cleanup_import_jobs
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import load_settings
from app.database import create_engine_from_settings, create_session_factory
from app.services import import_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("cleanup_import_jobs")


def main() -> None:
    settings = load_settings()
    engine = create_engine_from_settings(settings)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
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
        )


if __name__ == "__main__":
    main()

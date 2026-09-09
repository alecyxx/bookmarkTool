"""import_jobs：一次性导入任务（预览到执行的原子状态机）。

id 为服务端生成的 UUID 字符串，不接受客户端路径或文件名。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, now_utc


class ImportJob(Base):
    __tablename__ = "import_jobs"
    __table_args__ = (Index("ix_import_jobs_expires_at", "expires_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_type: Mapped[str] = mapped_column(String(4), nullable=False)  # HTML | CSV
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    temp_file_key: Mapped[str] = mapped_column(String(128), nullable=False)
    session_nonce_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    options_json: Mapped[str] = mapped_column(Text, nullable=False)
    summary_json: Mapped[str] = mapped_column(Text, nullable=False)
    category_tree_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # PREVIEWED/RUNNING/SUCCEEDED/FAILED/EXPIRED
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_utc)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

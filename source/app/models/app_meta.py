"""app_meta：全局单行元数据（分类树结构版本 CAS）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, now_utc


class AppMeta(Base):
    __tablename__ = "app_meta"
    __table_args__ = (CheckConstraint("id = 1", name="ck_app_meta_single_row"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_tree_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=now_utc, onupdate=now_utc
    )

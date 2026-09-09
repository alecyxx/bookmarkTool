"""categories：自关联分类树（最多 8 层）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, now_utc


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (
        # 同级唯一（名称只要求同一父节点下唯一）
        Index(
            "uq_categories_root_name",
            "normalized_name",
            unique=True,
            sqlite_where="parent_id IS NULL",
        ),
        Index(
            "uq_categories_child_name",
            "parent_id",
            "normalized_name",
            unique=True,
            sqlite_where="parent_id IS NOT NULL",
        ),
        Index("ix_categories_parent_sort", "parent_id", "sort_order", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(100), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=now_utc, onupdate=now_utc
    )

    parent: Mapped[Category | None] = relationship(
        remote_side="Category.id", back_populates="children", viewonly=True
    )
    children: Mapped[list[Category]] = relationship(
        back_populates="parent", viewonly=True, order_by="Category.sort_order"
    )

"""bookmarks 与 bookmark_tags。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, now_utc

bookmark_tags = Table(
    "bookmark_tags",
    Base.metadata,
    Column("bookmark_id", ForeignKey("bookmarks.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
    Index("ix_bookmark_tags_tag_id", "tag_id"),
)


class Bookmark(Base):
    __tablename__ = "bookmarks"
    __table_args__ = (
        Index("ix_bookmarks_normalized_url", "normalized_url"),
        Index("ix_bookmarks_category_id", "category_id"),
        Index("ix_bookmarks_deleted_at", "deleted_at"),
        Index("ix_bookmarks_is_favorite", "is_favorite"),
        Index("ix_bookmarks_created_at", "created_at"),
        Index("ix_bookmarks_updated_at", "updated_at"),
        Index("ix_bookmarks_list", "deleted_at", "created_at", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    url: Mapped[str] = mapped_column(String(4096), nullable=False)
    normalized_url: Mapped[str] = mapped_column(String(8192), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    favicon_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    is_favorite: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=now_utc, onupdate=now_utc
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    tags: Mapped[list[Tag]] = relationship(  # noqa: F821 - 延迟导入避免环
        secondary=bookmark_tags,
        back_populates="bookmarks",
        order_by="Tag.name",
    )

"""书签输入模型（BM-V1-301/302）。校验阈值与数据模型/配置一致。"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class BookmarkPayload(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=1, max_length=4096)
    description: str = Field(default="", max_length=2000)
    category_id: int | None = None
    tags: list[str] = Field(default_factory=list, max_length=30)
    is_favorite: bool = False
    favicon_url: str | None = Field(default=None, max_length=2048)

    @field_validator("favicon_url")
    @classmethod
    def _validate_favicon(cls, value: str | None) -> str | None:
        """仅接受 http(s) 且非 SVG 的图标 URL（与导入解析器口径一致）。"""
        if value is None or not value.strip():
            return None
        low = value.strip().lower()
        if low.startswith(("data:", "file:", "javascript:")):
            raise ValueError("favicon_url 必须是 http(s) 地址")
        if ".svg" in low.split("?")[0].split("#")[0]:
            raise ValueError("favicon_url 不支持 SVG")
        parts = value.split(":", 1)
        if len(parts) != 2 or parts[0].lower() not in ("http", "https"):
            raise ValueError("favicon_url 必须是 http(s) 地址")
        return value.strip()


class BookmarkCreate(BookmarkPayload):
    pass


class BookmarkUpdate(BookmarkPayload):
    """编辑时提交 version；本 payload 不含 version（由路由层作为独立字段校验）。"""


class FavoriteToggle(BaseModel):
    is_favorite: bool

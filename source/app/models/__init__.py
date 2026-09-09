"""ORM 模型包。表结构只通过 Alembic 迁移创建。

导入本包即注册全部模型到 Base.metadata（供迁移与元数据比对使用）。
"""

from app.models.app_meta import AppMeta
from app.models.bookmark import Bookmark, bookmark_tags
from app.models.category import Category
from app.models.import_job import ImportJob
from app.models.tag import Tag
from app.models.user import User

__all__ = [
    "AppMeta",
    "Bookmark",
    "Category",
    "ImportJob",
    "Tag",
    "User",
    "bookmark_tags",
]

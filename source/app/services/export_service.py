"""导出服务（BM-V1-606/607）。

- HTML：Netscape Bookmark File；分类树映射为文件夹；空文件夹导出但保留层级；
  根级未分类书签放入「未分类」文件夹；标签写入 <A TAGS> 兼容属性供本系统往返；
  不导出回收站（浏览器 HTML 不能混入）；
- CSV：UTF-8 BOM；固定字段 title,url,category,tags,description,favorite,
  export_format_version；分类路径反斜杠转义；标签与描述由 csv 模块引号处理；
  以 = + - @ 开头的文本字段加单引号公式注入前缀（导入侧凭 marker 反向剥离）。
"""

from __future__ import annotations

import csv
import html
import io
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.bookmark import Bookmark
from app.models.category import Category
from app.services.import_parser import CSV_EXPORT_MARKER, format_category_path, format_tag_list

EXPORT_FORMAT_VERSION = 1


def _tree(db: Session) -> dict[int, list[Category]]:
    rows = (
        db.execute(select(Category).order_by(Category.parent_id, Category.sort_order, Category.id))
        .scalars()
        .all()
    )
    children: dict[int | None, list[Category]] = {}
    for category in rows:
        children.setdefault(category.parent_id, []).append(category)
    return children


def _root_folder_name() -> str:
    return "未分类"


def to_netscape_html(db: Session, include_favicon: bool = True) -> str:
    """Netscape Bookmark File 导出（默认不含回收站）。"""
    bookmarks = db.scalars(
        select(Bookmark).options(selectinload(Bookmark.tags)).where(Bookmark.deleted_at.is_(None))
    ).all()
    children = _tree(db)
    by_category: dict[int | None, list[Bookmark]] = {}
    for bookmark in bookmarks:
        by_category.setdefault(bookmark.category_id, []).append(bookmark)

    lines: list[str] = []
    lines.append("<!DOCTYPE NETSCAPE-Bookmark-file-1>")
    lines.append("<!-- Bookmark Manager V1 export -->")
    lines.append('<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">')
    lines.append("<TITLE>Bookmarks</TITLE>")
    lines.append("<H1>Bookmarks</H1>")
    lines.append("<DL><p>")

    def anchor(bookmark: Bookmark) -> str:
        attrs = [f'HREF="{html.escape(bookmark.url, quote=True)}"']
        if bookmark.created_at is not None:
            stamp = int(bookmark.created_at.replace(tzinfo=UTC).timestamp())
            attrs.append(f'ADD_DATE="{stamp}"')
        if bookmark.tags:
            attrs.append(
                f'TAGS="{html.escape(format_tag_list([tag.name for tag in bookmark.tags]), quote=True)}"'
            )
        if include_favicon and bookmark.favicon_url:
            attrs.append(f'ICON="{html.escape(bookmark.favicon_url, quote=True)}"')
        return "<DT><A " + " ".join(attrs) + ">" + html.escape(bookmark.title) + "</A>"

    def folder_block(category: Category) -> None:
        lines.append(f"    <DT><H3>{html.escape(category.name)}</H3>")
        lines.append("    <DL><p>")
        for bookmark in by_category.get(category.id, []):
            lines.append("    " + anchor(bookmark))
        for child in children.get(category.id, []):
            folder_block(child)
        lines.append("    </DL><p>")

    for category in children.get(None, []):
        folder_block(category)
    # 根级未分类书签
    root_items = by_category.get(None, [])
    if root_items:
        lines.append(f"    <DT><H3>{html.escape(_root_folder_name())}</H3>")
        lines.append("    <DL><p>")
        for bookmark in root_items:
            lines.append("    " + anchor(bookmark))
        lines.append("    </DL><p>")
    lines.append("</DL><p>")
    return "\n".join(lines) + "\n"


def _formula_safe(value: str) -> str:
    if value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def to_csv_bytes(db: Session, include_trash: bool = False) -> bytes:
    """CSV 导出（UTF-8 BOM）。"""
    stmt = select(Bookmark).options(selectinload(Bookmark.tags))
    if not include_trash:
        stmt = stmt.where(Bookmark.deleted_at.is_(None))
    bookmarks = db.scalars(stmt.order_by(Bookmark.id)).all()
    category_path: dict[int | None, str] = {}
    for category in db.execute(select(Category)).scalars():
        path: list[str] = []
        current: Category | None = category
        while current is not None:
            path.append(current.name)
            current = db.get(Category, current.parent_id) if current.parent_id else None
        category_path[category.id] = format_category_path(tuple(reversed(path)))
    category_path[None] = ""

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        ["title", "url", "category", "tags", "description", "favorite", "export_format_version"]
    )
    for bookmark in bookmarks:
        tags = format_tag_list([tag.name for tag in bookmark.tags])
        writer.writerow(
            [
                _formula_safe(bookmark.title),
                bookmark.url,
                category_path.get(bookmark.category_id, ""),
                _formula_safe(tags),
                _formula_safe(bookmark.description or ""),
                "1" if bookmark.is_favorite else "0",
                f"{CSV_EXPORT_MARKER}-{EXPORT_FORMAT_VERSION}",
            ]
        )
    return buffer.getvalue().encode("utf-8-sig")


def export_filename(prefix: str, extension: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{stamp}.{extension}"

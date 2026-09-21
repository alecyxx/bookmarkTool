"""导入解析器（BM-V1-602/603）。

- Netscape Bookmark File（HTML）：仅用标准库 html.parser，绝不执行脚本/样式/事件属性；
  读取文件夹层级、标题、URL、ADD_DATE/ICON 等可识别属性；未知属性收集为警告；
- CSV：UTF-8（允许 BOM）、首行字段名、标准引号/换行；支持反斜杠转义的分类路径与
  公式注入安全前缀的反转义（仅当 export_format_version=bookmark-manager-v1）。
- 解析结果包含行级错误（带位置），调用方决定是否允许执行。
"""

from __future__ import annotations

import csv
import html
import io
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlsplit

from app.services.url_service import InvalidUrlError, normalize_url

MAX_TREE_DEPTH = 8
MAX_FOLDER_LENGTH = 100
MAX_TITLE_LENGTH = 300
CSV_EXPORT_MARKER = "bookmark-manager-v1"
FORMULA_PREFIXES = ("=", "+", "-", "@")

KNOWN_A_ATTRIBUTES = {"href", "add_date", "last_modified", "icon", "shortcuturl", "tags", "charset"}
KNOWN_H3_ATTRIBUTES = {"add_date", "last_modified", "personal_toolbar_folder", "folded"}


@dataclass
class ParsedItem:
    title: str
    url: str
    normalized_url: str
    folder_path: tuple[str, ...] = ()
    description: str = ""
    favorite: bool = False
    favicon_url: str | None = None
    tags: list[str] = field(default_factory=list)
    source_index: int = 0  # 文件内序号（0 起），错误定位用
    line: int = 0  # 源文件行号（尽力而为）
    empty_title: bool = False  # CSV 标题为空（策略决定用 host 或跳过）


@dataclass
class EmptyFolder:
    path: tuple[str, ...]
    source_index: int


@dataclass
class ParseResult:
    items: list[ParsedItem] = field(default_factory=list)
    empty_folders: list[EmptyFolder] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # 未知属性等非致命提示（去重）
    errors: list[str] = field(default_factory=list)  # 行级致命错误（带位置）
    total_rows: int = 0  # 处理到的数据行（用于 50,000 上限检查）


# ---------- Netscape HTML ----------


class _NetscapeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.result = ParseResult()
        self.depth = 0  # <dl> 嵌套深度
        self.folder_stack: list[str] = []  # 当前打开的文件夹名
        self._pending_folder: str | None = None  # <h3> 刚结束、等待 <dl> 的文件夹
        self._in_anchor = False
        self._anchor_attrs: dict[str, str] = {}
        self._text_buffer: list[str] = []
        self._source_index = 0
        self._line = 0
        self._in_h3 = False
        self._unknown_warned: set[str] = set()
        self._ignored_tags: set[str] = set()
        self._folders_seen: set[tuple[str, ...]] = set()

    def error(self, message: str) -> None:  # 不抛错：畸形 HTML 容忍并继续
        self.result.errors.append(f"HTML 解析异常：{message}")

    # -- 辅助 --
    def _append(self, text: str) -> None:
        self._text_buffer.append(text)

    def _take_text(self) -> str:
        value = " ".join("".join(self._text_buffer).split())
        self._text_buffer = []
        return value

    def _warn_unknown(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        known = KNOWN_A_ATTRIBUTES if tag == "a" else KNOWN_H3_ATTRIBUTES
        for name, _value in attrs:
            if name not in known and name not in self._unknown_warned:
                self._unknown_warned.add(name)
                self.result.warnings.append(f"未知属性「{name}」（<{tag}> 标签）已忽略")

    # -- HTMLParser 钩子 --
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._line = self.getpos()[0]
        tag = tag.lower()
        if tag == "dl":
            self.depth += 1
            if self._pending_folder is not None:
                self.folder_stack.append(self._pending_folder)
                self._pending_folder = None
        elif tag == "dt":
            return
        elif tag in ("p", "h1", "h2", "meta", "title"):
            return
        elif tag == "h3":
            self._in_h3 = True
            self._warn_unknown("h3", attrs)
            self._text_buffer = []
        elif tag == "a":
            self._in_anchor = True
            self._warn_unknown("a", attrs)
            attrs_map = {name.lower(): html.unescape(value or "") for name, value in attrs}
            self._anchor_attrs = attrs_map
            self._text_buffer = []
        else:
            if tag not in self._ignored_tags:
                self._ignored_tags.add(tag)
                self.result.warnings.append(f"已忽略标签 <{tag}>（不影响书签解析）")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "h3" and self._in_h3:
            self._in_h3 = False
            name = self._take_text()
            if name:
                self._folders_seen.add(tuple(self.folder_stack) + (name,))
            self._pending_folder = name or None
            if not name:
                self.result.warnings.append(f"第 {self._line} 行：空文件夹名称已忽略")
        elif tag == "dl":
            self.depth = max(0, self.depth - 1)
            if self.folder_stack:
                self.folder_stack.pop()
        elif tag == "a" and self._in_anchor:
            self._in_anchor = False
            self._finish_anchor()

    def handle_data(self, data: str) -> None:
        if self._in_h3 or self._in_anchor:
            self._append(data)

    def _finish_anchor(self) -> None:
        title = self._take_text()
        href = (self._anchor_attrs.get("href") or "").strip()
        if not href:
            self.result.errors.append(f"第 {self._line} 行：书签缺少 URL，已跳过")
            self._source_index += 1
            return
        try:
            normalized = normalize_url(href)
        except InvalidUrlError as exc:
            self.result.errors.append(f"第 {self._line} 行：无效 URL「{href[:80]}」：{exc}")
            self._source_index += 1
            return
        if len(title) > MAX_TITLE_LENGTH:
            self.result.errors.append(
                f"第 {self._line} 行：标题超过 {MAX_TITLE_LENGTH} 字符，已跳过"
            )
            self._source_index += 1
            return
        favicon_url = None
        icon = (self._anchor_attrs.get("icon") or "").strip()
        if icon:
            favicon_url = _sanitize_favicon(icon)
        tags_attr = (self._anchor_attrs.get("tags") or "").strip()
        anchor_tags = list(parse_tag_list(tags_attr)) if tags_attr else []
        folder_path = tuple(self.folder_stack)
        if len(folder_path) > MAX_TREE_DEPTH:
            self.result.errors.append(
                f"第 {self._line} 行：目录层级超过 {MAX_TREE_DEPTH} 层（{' / '.join(folder_path[:MAX_TREE_DEPTH])}…），已跳过"
            )
            self._source_index += 1
            return
        for part in folder_path:
            if len(part) > MAX_FOLDER_LENGTH:
                self.result.errors.append(
                    f"第 {self._line} 行：目录名超过 {MAX_FOLDER_LENGTH} 字符，已跳过"
                )
                self._source_index += 1
                return
        self.result.items.append(
            ParsedItem(
                title=title or href,
                url=href,
                normalized_url=normalized,
                folder_path=folder_path,
                favicon_url=favicon_url,
                tags=anchor_tags,
                source_index=self._source_index,
                line=self._line,
            )
        )
        self._source_index += 1


def _sanitize_favicon(value: str) -> str | None:
    """ICON 属性仅接受 http(s) 且非 svg 的图标 URL；data:/file: 等一律拒绝。"""
    low = value.lower()
    if (
        low.startswith(("data:", "file:", "javascript:"))
        or ".svg" in low.split("?")[0].split("#")[0].lower()
    ):
        return None
    try:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or len(value) > 2048:
            return None
        return value
    except ValueError:
        return None


def _collect_empty_folders(parser: _NetscapeParser) -> list[EmptyFolder]:
    """没有直接或间接书签的目录（供预览提示；默认不创建）。"""
    items = parser.result.items
    empty: list[EmptyFolder] = []
    for path in sorted(parser._folders_seen):
        has_bookmark = any(
            item.folder_path == path or item.folder_path[: len(path)] == path for item in items
        )
        if not has_bookmark:
            empty.append(EmptyFolder(path=path, source_index=0))
    return empty


def parse_netscape_html(content: str | bytes) -> ParseResult:
    text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
    parser = _NetscapeParser()
    parser.feed(text)
    parser.close()
    parser.result.total_rows = parser._source_index
    parser.result.empty_folders = _collect_empty_folders(parser)
    return parser.result


# ---------- CSV ----------


def _unescape_formula(value: str) -> str:
    """移除本系统导出时添加的公式安全前缀（仅首字符）。"""
    if len(value) > 1 and value[0] == "'" and value[1] in FORMULA_PREFIXES:
        return value[1:]
    return value


def _escape_display(value: str) -> str:
    return value.strip()


def parse_category_path(raw: str) -> tuple[str, ...]:
    """分类路径：/ 分隔层级；\\ 转义分隔符与反斜杠本身。"""
    return _split_escaped(raw, "/")


def parse_tag_list(raw: str) -> list[str]:
    """标签列表：, 分隔层级；\\ 转义逗号与反斜杠本身。"""
    return [part.strip() for part in _split_escaped(raw, ",") if part.strip()]


def _split_escaped(raw: str, separator: str) -> tuple[str, ...]:
    if not raw.strip():
        return ()
    tokens: list[str] = []
    current: list[str] = []
    escaped = False
    for char in raw:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == separator:
            tokens.append("".join(current))
            current = []
        else:
            current.append(char)
    if escaped:  # 行尾反斜杠视为普通字符
        current.append("\\")
    tokens.append("".join(current))
    return tuple(token.strip() for token in tokens if token.strip())


def _read_field(row: list[str], col: dict[str, int], name: str, has_marker: bool) -> str:
    """读取文本字段；仅当带本系统导出标记时移除公式安全前缀。"""
    value = _escape_display((row[col[name]] or "") if name in col else "")
    if has_marker:
        value = _unescape_formula(value)
    return value


def parse_csv(content: bytes) -> ParseResult:
    """解析 CSV。任何解析/校验错误写入 errors（带行号），不抛出。"""
    text = content.decode("utf-8-sig", errors="replace")
    if "\x00" in text[:2048]:
        result = ParseResult()
        result.errors.append("CSV 不是有效的 UTF-8 文本（检测到 NUL 字节）")
        return result
    reader = csv.reader(io.StringIO(text))
    result = ParseResult()
    try:
        header = next(reader, None)
    except csv.Error as exc:
        result.errors.append(f"CSV 表头解析失败：{exc}")
        return result
    if header is None:
        result.errors.append("CSV 为空（缺少表头）")
        return result
    header = [name.strip().lower() for name in header]
    required = {"title", "url"}
    allowed = {
        "title",
        "url",
        "category",
        "tags",
        "description",
        "favorite",
        "export_format_version",
        "favicon_url",
    }
    unknown = [name for name in header if name and name not in allowed]
    if unknown:
        result.errors.append(f"未知列：{', '.join(unknown)}（第 1 行）")
        return result
    missing = [name for name in required if name not in header]
    if missing:
        result.errors.append(f"缺少必需列：{', '.join(missing)}（第 1 行）")
        return result
    col = {name: index for index, name in enumerate(header)}
    has_marker = col.get("export_format_version") is not None

    row_index = 0  # 数据行物理序号（表头后第一行为 1）
    for raw_row in reader:
        row_index += 1
        result.total_rows += 1
        line = row_index + 1  # 表头占第 1 行
        # 空行整行跳过
        if not any(cell.strip() for cell in raw_row):
            continue
        if len(raw_row) < len(header):
            result.errors.append(
                f"第 {line} 行：列数不足（期望 {len(header)}，实际 {len(raw_row)}）"
            )
            continue

        if has_marker:
            marker = _read_field(raw_row, col, "export_format_version", has_marker).strip()
            if marker and not marker.startswith(CSV_EXPORT_MARKER):
                result.errors.append(
                    f"第 {line} 行：export_format_version 值不受支持「{marker[:30]}」"
                )
                continue

        raw_title = _read_field(raw_row, col, "title", has_marker)
        raw_url = _read_field(raw_row, col, "url", has_marker)
        if not raw_url:
            result.errors.append(f"第 {line} 行：URL 为空")
            continue
        try:
            normalized = normalize_url(raw_url)
        except InvalidUrlError as exc:
            result.errors.append(f"第 {line} 行：无效 URL：{exc}")
            continue
        if not raw_title:
            # 标题为空：预览中默认确认使用 URL 主机名（不联网抓取），不视为格式错误
            raw_title = ""
            empty_title = True
        elif len(raw_title) > MAX_TITLE_LENGTH:
            result.errors.append(f"第 {line} 行：标题超过 {MAX_TITLE_LENGTH} 字符")
            continue
        else:
            empty_title = False

        favorite = False
        fav_raw = _read_field(raw_row, col, "favorite", has_marker)
        if fav_raw:
            if fav_raw in ("1", "true", "yes"):
                favorite = True
            elif fav_raw in ("0", "false", "no", ""):
                favorite = False
            else:
                result.errors.append(
                    f"第 {line} 行：favorite 值必须是 1/0（实际「{fav_raw[:20]}」）"
                )
                continue

        tags_raw = _read_field(raw_row, col, "tags", has_marker)
        tags = list(parse_tag_list(tags_raw)) if tags_raw else []

        favicon_url = None
        icon_raw = _read_field(raw_row, col, "favicon_url", has_marker)
        if icon_raw:
            favicon_url = _sanitize_favicon(icon_raw)
            if favicon_url is None:
                result.errors.append(f"第 {line} 行：favicon_url 必须为 http(s) 且非 SVG")
                continue

        path = parse_category_path(_read_field(raw_row, col, "category", has_marker))
        if len(path) > MAX_TREE_DEPTH:
            result.errors.append(f"第 {line} 行：分类路径超过 {MAX_TREE_DEPTH} 层")
            continue
        if any(len(part) > MAX_FOLDER_LENGTH for part in path):
            result.errors.append(f"第 {line} 行：分类名超过 {MAX_FOLDER_LENGTH} 字符")
            continue

        item = ParsedItem(
            title=raw_title,
            url=raw_url,
            normalized_url=normalized,
            folder_path=path,
            description=_read_field(raw_row, col, "description", has_marker),
            favorite=favorite,
            favicon_url=favicon_url,
            tags=tags,
            source_index=result.total_rows - 1,
            line=line,
            empty_title=empty_title,
        )
        result.items.append(item)
    return result


def format_category_path(path: tuple[str, ...]) -> str:
    """导出用：分类路径转义（/ 分隔；路径内的 \\ 与 / 用反斜杠转义）。"""
    escaped = []
    for part in path:
        escaped.append(part.replace("\\", "\\\\").replace("/", "\\/"))
    return "/".join(escaped)


def format_tag_list(tags: list[str]) -> str:
    """导出用：标签列表转义（, 分隔；名称内的 \\ 与 , 用反斜杠转义）。"""
    return ",".join(tag.replace("\\", "\\\\").replace(",", "\\,") for tag in tags)

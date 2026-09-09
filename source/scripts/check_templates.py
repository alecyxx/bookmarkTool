"""模板与前端资源检查（BM-V1-005 质量门禁一部分）。

检查项：
1. 所有 Jinja2 模板可编译（语法错误会被拒绝）；
2. 模板中引用的 /static/... 资源真实存在；
3. 基础 HTML 结构配对（div/section/main/ul/li 等）无明显不配对。

退出码非 0 表示检查失败。
"""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = APP_ROOT / "app" / "templates"
STATIC_DIR = APP_ROOT / "app" / "static"

_PAIR_TAGS = {
    "div",
    "section",
    "main",
    "ul",
    "ol",
    "li",
    "table",
    "tr",
    "td",
    "th",
    "nav",
    "header",
    "footer",
    "form",
    "span",
    "a",
    "button",
    "h1",
    "h2",
    "h3",
    "p",
    "label",
    "select",
    "textarea",
    "template",
}

_STATIC_REF = re.compile(r"""(?:src|href)=["'](/static/[^"'#?]+)["']""")


def _check_template_compile(template: Path) -> list[str]:
    errors: list[str] = []
    from jinja2 import Environment, StrictUndefined

    env = Environment(undefined=StrictUndefined)
    try:
        env.parse(template.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 需要捕获任意模板语法错误
        errors.append(f"{template.relative_to(APP_ROOT)}: 模板编译失败: {exc}")
    return errors


class _TagBalanceChecker(HTMLParser):
    """轻量标签配对检查：只关注显式声明的结构性标签。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.errors: list[str] = []
        self.void_seen: set[str] = set()
        # 以提取到的模板源码中第一个 HTML 片段为判断，注释与 {% %} 不参与

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _PAIR_TAGS:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.void_seen.add(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag not in _PAIR_TAGS:
            return
        if not self.stack:
            self.errors.append(f"多余的闭合标签 </{tag}>")
            return
        if self.stack[-1] == tag:
            self.stack.pop()
        else:
            self.errors.append(f"标签不配对: 期望 </{self.stack[-1]}> 实际 </{tag}>")


def _check_structure(template: Path, text: str) -> list[str]:
    errors: list[str] = []
    # 去除 Jinja 块与控制标签后再检查，避免 {%%} 干扰
    cleaned = re.sub(r"\{%.*?%\}", "", text, flags=re.S)
    cleaned = re.sub(r"\{\{.*?\}\}", "", cleaned, flags=re.S)
    parser = _TagBalanceChecker()
    try:
        parser.feed(cleaned)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{template.relative_to(APP_ROOT)}: HTML 解析失败: {exc}")
    errors.extend(f"{template.relative_to(APP_ROOT)}: {err}" for err in parser.errors)
    return errors


def _check_static_refs(template: Path, text: str) -> list[str]:
    errors: list[str] = []
    for match in _STATIC_REF.finditer(text):
        url = match.group(1)
        relative = url.removeprefix("/static/")
        if not (STATIC_DIR / relative).is_file():
            errors.append(f"{template.relative_to(APP_ROOT)}: 引用的静态资源不存在: {url}")
    return errors


def check_templates() -> list[str]:
    errors: list[str] = []
    if not TEMPLATES_DIR.is_dir():
        return [f"模板目录不存在: {TEMPLATES_DIR}"]
    for template in sorted(TEMPLATES_DIR.rglob("*.html")):
        text = template.read_text(encoding="utf-8")
        errors.extend(_check_template_compile(template))
        errors.extend(_check_static_refs(template, text))
        errors.extend(_check_structure(template, text))
    return errors


def main() -> int:
    errors = check_templates()
    for error in errors:
        print(f"[template] {error}")
    if errors:
        print(f"模板检查失败：{len(errors)} 个问题")
        return 1
    print(f"模板检查通过：{TEMPLATES_DIR}（共 {len(list(TEMPLATES_DIR.rglob('*.html')))} 个模板）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

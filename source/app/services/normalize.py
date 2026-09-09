"""名称规范化（BM-V1-104）。

- 用户名、分类、标签统一执行 NFKC + casefold；
- 展示名称仅去除首尾空白；规范化字段只用于比较/唯一；
- 分类只在同一父节点下唯一；标签与用户名全局唯一。
"""

from __future__ import annotations

import unicodedata

# 展示名去首尾空白用的字符集（含全角空格与 NBSP）
_TRIM_CHARS = " \t\n\r\f\v\u00a0\u3000"


def strip_display_name(value: str) -> str:
    """展示名：去除首尾空白（含全角空格），保留其它字符原样。"""
    return value.strip(_TRIM_CHARS)


def normalize_name(value: str) -> str:
    """NFKC + casefold；比较与唯一约束专用。"""
    return unicodedata.normalize("NFKC", value).casefold()


def normalize_username(value: str) -> str:
    """登录名规范化；V1 仅单管理员，规则与标签一致，保留给将来复用。"""
    return normalize_name(value)


def is_valid_length(value: str, maximum: int) -> bool:
    """长度按字符数（非字节）校验；首尾空白去除后再判断。"""
    return 1 <= len(strip_display_name(value)) <= maximum

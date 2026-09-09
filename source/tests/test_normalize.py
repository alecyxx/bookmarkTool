"""名称规范化测试（BM-V1-104）。"""

from __future__ import annotations

import pytest
from app.services.normalize import (
    is_valid_length,
    normalize_name,
    normalize_username,
    strip_display_name,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Python", "python"),
        ("PYTHON", "python"),
        ("ｐｙｔｈｏｎ", "python"),  # 全角 -> NFKC
        ("ＡＩ", "ai"),
        ("Docker", "docker"),
        ("开发", "开发"),
        ("ＡＩ　", "ai"),  # 全角空格被 casefold 前 NFKC 折叠? 先 strip 再 normalize
        ("  AI  ", "ai"),  # strip 由展示层负责；规范化只处理 NFKC+casefold
    ],
)
def test_normalize_name(raw, expected):
    assert normalize_name(strip_display_name(raw)) == expected


def test_normalize_casefold_german_eszett():
    # casefold 比 lower 更强：ß -> ss
    assert normalize_name("STRASSE") == normalize_name("straße")


def test_strip_display_name():
    assert strip_display_name("  工作  ") == "工作"
    assert strip_display_name("\u3000资料\u3000") == "资料"


def test_normalize_username_same_rule():
    # 登录名先去除首尾空白（认证层职责），再按同一规则规范化
    assert normalize_username(strip_display_name(" Admin ")) == normalize_username("ａｄｍｉｎ")


def test_is_valid_length():
    assert is_valid_length("a", 100) is True
    assert is_valid_length("", 100) is False
    assert is_valid_length("   ", 100) is False
    assert is_valid_length("a" * 100, 100) is True
    assert is_valid_length("a" * 101, 100) is False
    assert is_valid_length("中文标签", 50) is True

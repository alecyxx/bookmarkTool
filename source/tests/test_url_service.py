"""URL 校验与规范化服务测试（BM-V1-105，设计文档 §33）。"""

from __future__ import annotations

import pytest
from app.services.url_service import InvalidUrlError, normalize_url


class TestEquivalentKeys:
    def test_empty_path_equals_slash(self):
        assert normalize_url("https://example.com") == normalize_url("https://example.com/")

    def test_scheme_host_lowercase(self):
        assert normalize_url("HTTPS://EXAMPLE.COM/A") == normalize_url("https://example.com/A")

    def test_default_port_removed(self):
        assert normalize_url("https://example.com:443/a") == normalize_url("https://example.com/a")
        assert normalize_url("http://example.com:80/a") == normalize_url("http://example.com/a")

    def test_idna_host(self):
        assert normalize_url("https://例え.jp/") == normalize_url("https://xn--r8jz45g.jp/")

    def test_trailing_whitespace_stripped(self):
        assert normalize_url("  https://example.com  ") == "https://example.com/"


class TestDistinctKeys:
    """以下不得被 V1 自动认定为重复。"""

    def test_path_trailing_slash_distinct(self):
        assert normalize_url("https://example.com/a") != normalize_url("https://example.com/a/")

    def test_query_order_distinct(self):
        assert normalize_url("https://example.com/?a=1&b=2") != normalize_url(
            "https://example.com/?b=2&a=1"
        )

    def test_fragment_distinct(self):
        assert normalize_url("https://example.com/#one") != normalize_url(
            "https://example.com/#two"
        )

    def test_query_values_distinct(self):
        assert normalize_url("https://example.com/?id=1") != normalize_url(
            "https://example.com/?id=2"
        )

    def test_non_default_port_kept(self):
        assert normalize_url("https://example.com:8443/a") == "https://example.com:8443/a"


class TestRejected:
    @pytest.mark.parametrize(
        "raw",
        [
            "javascript:alert(1)",
            "data:text/html,<script>1</script>",
            "file:///etc/passwd",
            "ftp://example.com/a",
            "mailto:a@b.com",
            "https://user:pass@example.com/",
            "https://user@example.com/",
            "",
            "   ",
            "not-a-url",
            "https://",
            "https:///path",
            "https://example.com:99999/a",
            "https://exa mple.com/a",
        ],
    )
    def test_invalid_schemes_and_malformed(self, raw):
        with pytest.raises(InvalidUrlError):
            normalize_url(raw)

    def test_url_too_long(self):
        with pytest.raises(InvalidUrlError):
            normalize_url("https://example.com/" + "a" * 5000)


class TestFormalNormalization:
    def test_query_fragment_kept(self):
        assert (
            normalize_url("https://example.com/p?x=1&y=2#sec")
            == "https://example.com/p?x=1&y=2#sec"
        )

    def test_path_case_and_encoding_kept(self):
        assert normalize_url("https://example.com/A%2Fb/c") == "https://example.com/A%2Fb/c"

    def test_unicode_path_percent_encoded(self):
        normalized = normalize_url("https://example.com/路径")
        assert normalized == "https://example.com/%E8%B7%AF%E5%BE%84"

    def test_hex_escape_unified_uppercase(self):
        assert normalize_url("https://example.com/a%2fb") == "https://example.com/a%2Fb"

    def test_original_url_not_rewritten_by_service(self):
        # 规范化服务只输出 normalized_url；原 url 由调用方保存 strip 后的原始文本
        assert normalize_url("https://Example.com/Path/") == "https://example.com/Path/"

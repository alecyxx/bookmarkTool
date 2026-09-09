"""导入解析器测试（BM-V1-602/603）。"""

from __future__ import annotations

from pathlib import Path

from app.services.import_parser import (
    MAX_TREE_DEPTH,
    format_category_path,
    parse_category_path,
    parse_csv,
    parse_netscape_html,
    parse_tag_list,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class TestNetscapeHtml:
    def test_chrome_sample(self):
        result = parse_netscape_html(_load("chrome_bookmarks.html"))
        assert not result.errors, result.errors
        by_title = {item.title: item for item in result.items}
        assert by_title["GitHub"].folder_path == ("书签栏",)
        assert by_title["FastAPI"].folder_path == ("书签栏", "开发")
        assert by_title["Example Site"].normalized_url == "https://example.com/"
        # Path With Slash 保留尾斜杠不同键
        assert by_title["Path With Slash"].normalized_url == "https://example.com/a/"
        # data: ICON 被拒绝（不入 favicon_url），http(s) ICON 被接受
        assert by_title["GitHub"].favicon_url is None
        assert by_title["FastAPI"].favicon_url == "https://fastapi.tiangolo.com/img/favicon.png"
        # 空目录被收集
        assert any(f.path == ("书签栏", "空目录示例") for f in result.empty_folders)
        # 根级书签（无目录）
        assert by_title["Hacker News"].folder_path == ("其他书签",)

    def test_firefox_sample(self):
        result = parse_netscape_html(_load("firefox_bookmarks.html"))
        assert not result.errors
        items = {item.title: item for item in result.items}
        assert items["Get Help"].folder_path == ("Mozilla Firefox",)
        assert items["Wikipedia"].folder_path == ("工具",)
        # TAGS 属性（Firefox 导出）作为本系统往返标签导入
        assert items["Wikipedia"].tags == ["参考", "百科"]
        assert not any("未知属性" in w for w in result.warnings)

    def test_edge_sample_uppercase_host(self):
        result = parse_netscape_html(_load("edge_bookmarks.html"))
        assert not result.errors
        items = {item.title: item for item in result.items}
        # 根级书签（收藏夹栏 <dl> 外层之外）
        assert items["Google"].folder_path == ()
        # HTTPS 大写主机归一（与 example.com 同键）
        assert items["Example Uppercase"].normalized_url == "https://example.com/"
        # FOLDED 不产生未知属性警告
        assert not any("未知属性" in w for w in result.warnings)

    def test_safari_sample_no_p_tags(self):
        result = parse_netscape_html(_load("safari_bookmarks.html"))
        assert not result.errors, result.errors
        items = {item.title: item for item in result.items}
        assert items["Apple"].folder_path == ("Favorites",)
        assert items["Sub Page"].folder_path == ("Favorites", "Sub")
        assert items["DuckDuckGo"].folder_path == ()

    def test_malicious_html_not_executed(self):
        content = (
            "<HTML><HEAD><SCRIPT>alert(1)</SCRIPT></HEAD><BODY>"
            '<DL><DT><A HREF="javascript:alert(2)">bad</A>'
            '<DT><A HREF="https://safe.example/">ok</A></DL></BODY></HTML>'
        )
        result = parse_netscape_html(content)
        # 脚本标签被忽略不执行；javascript: URL 被拒绝；正文中的 "alert(1)" 文本不回显为书签
        assert all("safe" in item.url for item in result.items)
        assert not any("javascript" in item.url for item in result.items)

    def test_too_deep_tree_blocked(self):
        parts = ["<DL><p>"]
        for index in range(MAX_TREE_DEPTH + 3):
            parts.append(f"<DT><H3>L{index}</H3><DL><p>")
        parts.append('<DT><A HREF="https://example.com/x">deep</A>')
        for _ in range(MAX_TREE_DEPTH + 3):
            parts.append("</DL><p>")
        result = parse_netscape_html("".join(parts))
        assert result.errors  # 超 8 层阻止执行
        assert "超过" in result.errors[0]

    def test_unknown_attribute_warned_once(self):
        content = (
            '<DL><DT><H3 CUSTOM_FLAG="1">F</H3><DL>'
            '<DT><A HREF="https://example.com/" CUSTOM_ATTR="x">t</A></DL></DL>'
        )
        result = parse_netscape_html(content)
        # HTMLParser 会把属性名规范化为小写
        unknown = [w for w in result.warnings if "未知属性" in w]
        assert any("custom_flag" in w for w in unknown)
        assert any("custom_attr" in w for w in unknown)
        assert sum("custom_attr" in w for w in unknown) == 1


class TestCsv:
    def _csv(self, text: str) -> bytes:
        return text.encode("utf-8")

    def test_standard_rows(self):
        content = (
            "title,url,category,tags,description,favorite\n"
            'ChatGPT,https://chatgpt.com,AI,"AI,GPT","",1\n'
            'GitHub,https://github.com,开发,"Git,代码","多行\n备注",0\n'
        )
        result = parse_csv(self._csv(content))
        assert not result.errors, result.errors
        assert len(result.items) == 2
        first, second = result.items
        assert first.tags == ["AI", "GPT"]
        assert first.favorite is True
        assert first.folder_path == ("AI",)
        assert second.folder_path == ("开发",)
        assert second.description == "多行\n备注"  # 引号内真实换行

    def test_bom_and_utf8(self):
        content = b"\xef\xbb\xbftitle,url\n\xe6\xb5\x8b\xe8\xaf\x95,https://example.com/\n"
        result = parse_csv(content)
        assert not result.errors
        assert result.items[0].title == "测试"

    def test_empty_title_marked_not_error(self):
        content = "title,url\n,https://example.com/\n"
        result = parse_csv(self._csv(content))
        assert not result.errors
        assert result.items[0].empty_title is True

    def test_required_columns_missing(self):
        result = parse_csv(self._csv("title\nx\n"))
        assert any("缺少必需列" in e for e in result.errors)

    def test_unknown_column_rejected(self):
        result = parse_csv(self._csv("title,url,wat\nx,https://a.com,y\n"))
        assert any("未知列" in e for e in result.errors)

    def test_row_level_error_locations(self):
        content = "title,url,favorite\nok,https://ok.example,1\nbad,javascript:alert(1),0\nno,https://ok.example,x\n"
        result = parse_csv(self._csv(content))
        assert any("第 3 行" in e for e in result.errors)
        assert any("第 4 行" in e for e in result.errors)

    def test_category_path_escaping(self):
        content = "title,url,category\nx,https://x.example,资料/AI\\/ML/Deep\n"
        result = parse_csv(self._csv(content))
        assert not result.errors, result.errors
        assert result.items[0].folder_path == ("资料", "AI/ML", "Deep")

    def test_formula_prefix_stripped_only_with_marker(self):
        with_marker = (
            "title,url,description,export_format_version\n"
            "'=SUM(A1),https://a.example,'=1+1,bookmark-manager-v1\n"
        )
        result = parse_csv(self._csv(with_marker))
        assert not result.errors
        item = result.items[0]
        assert item.title == "=SUM(A1)"
        assert item.description == "=1+1"
        # 无标记的第三方 CSV 不去前缀
        no_marker = "title,url,description\n'=SUM(A1),https://a.example,'=1+1\n"
        result = parse_csv(self._csv(no_marker))
        assert result.items[0].title == "'=SUM(A1)"

    def test_tag_list_escaping(self):
        from app.services.import_parser import format_tag_list

        names = ["AI,GPT", "\\"]
        cell = format_tag_list(names)
        quoted = '"' + cell + '"'  # 与 csv.writer 引号规则一致
        result = parse_csv(self._csv("title,url,tags\nx,https://x.example," + quoted + "\n"))
        assert not result.errors, result.errors
        assert result.items[0].tags == names
        assert parse_tag_list(cell) == names

    def test_invalid_favorite_value(self):
        result = parse_csv(self._csv("title,url,favorite\nx,https://x.example,maybe\n"))
        assert any("favorite" in e for e in result.errors)


class TestCategoryPathFormat:
    def test_format_roundtrip(self):
        path = ("资料", "AI/ML", "Deep\\Model")
        formatted = format_category_path(path)
        assert formatted == "资料/AI\\/ML/Deep\\\\Model"
        assert parse_category_path(formatted) == path
        assert parse_category_path("") == ()
        assert format_category_path(()) == ""

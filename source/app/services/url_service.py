"""URL 校验与规范化服务（BM-V1-105，设计文档 §33）。

确定性步骤（保守策略）：
1. 去除首尾空白并解析 URL；
2. 仅允许 http/https，scheme 与 host 转小写，国际化域名转 IDNA；
3. 拒绝包含用户名或密码的 URL；
4. 去除默认端口（HTTP 80 / HTTPS 443）；
5. 空路径规范为 "/"，其余保留路径大小写和尾部 "/"；
6. 完整保留 query 参数、顺序与值；
7. 完整保留 fragment；
8. 对 path/query/fragment 中的非 ASCII 与非法字节做规范化百分号编码
   （合法 %XX 转写统一大写；空路径补 "/"）。

因此只有明确等价的表示才合并（如 https://example.com 与 https://example.com/）。
原始展示 URL（url 字段）除首尾空白外不做改写——规范化结果只写入 normalized_url。
"""

from __future__ import annotations

from urllib.parse import urlsplit

_HEX_DIGITS = set("0123456789abcdefABCDEF")
_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
# RFC 3986 pchar/sub-delims 中允许在对应段原样保留的结构字符
_PATH_SAFE = frozenset("/:@!$&'()*+,;=")
_QUERY_SAFE = frozenset("/?:@!$&'()*+,;=")
_FRAGMENT_SAFE = frozenset("/?:@!$&'()*+,;=")

MAX_URL_LENGTH = 4096
MAX_NORMALIZED_URL_LENGTH = 8192


class InvalidUrlError(ValueError):
    """URL 校验失败。message 面向用户可展示。"""


def _encode_part(part: str, safe: frozenset[str]) -> str:
    """规范化百分号编码：合法 %XX 大写保留；非法 % 与其它字节按 UTF-8 编码。

    safe 集合中的 ASCII 保留字符原样输出；字母数字与 -._~ 恒保留。
    """
    out: list[str] = []
    i = 0
    length = len(part)
    while i < length:
        ch = part[i]
        if (
            ch == "%"
            and i + 2 < length
            and part[i + 1] in _HEX_DIGITS
            and part[i + 2] in _HEX_DIGITS
        ):
            out.append("%" + part[i + 1 : i + 3].upper())
            i += 3
            continue
        if ch in _UNRESERVED or ch in safe:
            out.append(ch)
            i += 1
            continue
        if ord(ch) < 128:
            # 非法 ASCII（空白、控制、引号等）按字节编码
            out.append(f"%{ord(ch):02X}")
            i += 1
            continue
        for byte in ch.encode("utf-8"):
            out.append(f"%{byte:02X}")
        i += 1
    return "".join(out)


def _normalize_host(hostname: str) -> str:
    """host：IDNA 编码 + 小写。返回可放回 netloc 的主机部分（IPv6 带括号）。"""
    if not hostname:
        raise InvalidUrlError("URL 缺少主机名。")
    # 主机名不允许出现空白（nameprep 会把空格折叠成下划线，属于隐式改写，V1 拒绝）
    if any(ch in " \t\r\n" for ch in hostname):
        raise InvalidUrlError("URL 主机名包含非法字符。")
    try:
        ascii_host = hostname.encode("idna").decode("ascii")
    except (UnicodeError, IndexError):
        raise InvalidUrlError("URL 主机名包含非法字符。") from None
    if ":" in ascii_host and not ascii_host.startswith("["):
        return f"[{ascii_host}]"
    return ascii_host


def normalize_url(raw_url: str) -> str:
    """校验并返回规范化 URL；失败抛 InvalidUrlError。"""
    text = raw_url.strip()
    if not text:
        raise InvalidUrlError("URL 不能为空。")
    if len(text) > MAX_URL_LENGTH:
        raise InvalidUrlError(f"URL 长度超过 {MAX_URL_LENGTH} 字符上限。")
    try:
        parts = urlsplit(text)
    except ValueError:
        raise InvalidUrlError("URL 格式无法解析。") from None

    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise InvalidUrlError("仅支持 http:// 与 https:// 地址。")
    if parts.username is not None or parts.password is not None:
        raise InvalidUrlError("URL 不允许包含用户名或密码。")

    hostname = parts.hostname
    if hostname is None:
        raise InvalidUrlError("URL 缺少主机名。")

    # 显式端口解析（非法端口抛 ValueError -> 拒绝）
    try:
        port = parts.port
    except ValueError:
        raise InvalidUrlError("URL 端口不合法。") from None

    netloc = _normalize_host(hostname)
    default_port = 80 if scheme == "http" else 443
    if port is not None and port != default_port:
        netloc = f"{netloc}:{port}"

    path = parts.path if parts.path else "/"
    path = _encode_part(path, _PATH_SAFE)
    query = _encode_part(parts.query, _QUERY_SAFE) if parts.query else ""
    fragment = _encode_part(parts.fragment, _FRAGMENT_SAFE) if parts.fragment else ""

    normalized = f"{scheme}://{netloc}{path}"
    if query:
        normalized += f"?{query}"
    if fragment:
        normalized += f"#{fragment}"
    if len(normalized) > MAX_NORMALIZED_URL_LENGTH:
        raise InvalidUrlError("规范化后的 URL 超过长度上限。")
    return normalized

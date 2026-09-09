"""客户端来源 IP 解析（BM-V1-204）。

只信任显式配置的 TRUSTED_PROXIES 转发头；无代理或对端不在可信网段时
一律使用直连对端地址，不接受客户端伪造的转发 IP。
"""

from __future__ import annotations

import ipaddress

from fastapi import Request

from app.config import Settings


def client_ip(request: Request, settings: Settings | None = None) -> str:
    settings = settings or request.app.state.settings
    if request.client is None:
        return "-"
    peer = request.client.host
    if settings.trusted_proxies:
        try:
            peer_ip = ipaddress.ip_address(peer)
        except ValueError:
            return peer
        for cidr in settings.trusted_proxies:
            if peer_ip in ipaddress.ip_network(cidr, strict=False):
                forwarded = request.headers.get("X-Forwarded-For")
                if forwarded:
                    # 取最右一项：最接近本服务的可信转发地址
                    return forwarded.split(",")[-1].strip()
    return peer

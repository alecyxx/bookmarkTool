"""来源 IP 可信代理解析测试（BM-V1-204）。"""

from __future__ import annotations

from app.client_ip import client_ip


class _FakeClient:
    def __init__(self, host: str):
        self.host = host


class _FakeRequest:
    def __init__(self, peer: str, xff: str | None):
        self.client = _FakeClient(peer)
        self.headers = {"X-Forwarded-For": xff} if xff else {}


def _settings_with_proxies(make_settings, proxies: str):
    return make_settings({"TRUSTED_PROXIES": proxies})


def test_direct_connection_uses_peer(make_settings):
    settings = make_settings()
    assert client_ip(_FakeRequest("203.0.113.7", "1.2.3.4"), settings) == "203.0.113.7"


def test_untrusted_peer_ignores_xff(make_settings):
    settings = _settings_with_proxies(make_settings, "10.0.0.0/8")
    # 对端不在可信网段：伪造 XFF 无效
    assert client_ip(_FakeRequest("203.0.113.7", "1.2.3.4"), settings) == "203.0.113.7"


def test_trusted_peer_uses_xff(make_settings):
    settings = _settings_with_proxies(make_settings, "10.0.0.0/8")
    assert client_ip(_FakeRequest("10.0.0.5", "203.0.113.9"), settings) == "203.0.113.9"


def test_trusted_peer_uses_rightmost_xff(make_settings):
    settings = _settings_with_proxies(make_settings, "127.0.0.1")
    assert (
        client_ip(_FakeRequest("127.0.0.1", "203.0.113.9, 198.51.100.2"), settings)
        == "198.51.100.2"
    )


def test_no_client_returns_dash(make_settings):
    settings = make_settings()

    class _NoClient:
        client = None

    assert client_ip(_NoClient(), settings) == "-"

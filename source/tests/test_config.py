"""配置系统测试（BM-V1-003）。"""

from __future__ import annotations

import pytest
from app.config import ConfigError, load_settings


def test_defaults_in_testing(make_settings):
    settings = make_settings()
    assert settings.app_env == "testing"
    assert settings.default_web_search_engine == "google"
    assert settings.enable_remote_favicons is False
    assert settings.session_cookie_secure is False
    assert settings.session_cookie_http_only is True
    assert settings.session_cookie_same_site == "lax"
    assert settings.session_max_age_seconds == 12 * 3600
    assert settings.max_upload_bytes == 10 * 1024 * 1024
    assert settings.max_import_rows == 50_000
    assert settings.allow_docs is True  # testing 默认开放文档接口，production 强制关闭


def test_invalid_app_env(make_settings):
    with pytest.raises(ConfigError):
        make_settings({"APP_ENV": "staging"})


@pytest.mark.parametrize("value", ["google", "bing", "baidu"])
def test_search_engine_accepted(make_settings, value):
    assert make_settings({"DEFAULT_WEB_SEARCH_ENGINE": value}).default_web_search_engine == value


def test_search_engine_rejected(make_settings):
    with pytest.raises(ConfigError):
        make_settings({"DEFAULT_WEB_SEARCH_ENGINE": "yahoo"})


def test_invalid_boolean(make_settings):
    with pytest.raises(ConfigError):
        make_settings({"ENABLE_REMOTE_FAVICONS": "maybe"})


def test_boolean_parsing_variants(make_settings):
    assert make_settings({"SESSION_COOKIE_SECURE": "1"}).session_cookie_secure is True
    assert make_settings({"SESSION_COOKIE_SECURE": "TRUE"}).session_cookie_secure is True
    assert make_settings({"SESSION_COOKIE_SECURE": "off"}).session_cookie_secure is False


def test_production_requires_strong_secret(make_settings):
    with pytest.raises(ConfigError):
        make_settings({"APP_ENV": "production", "SESSION_SECRET": ""})
    with pytest.raises(ConfigError):
        make_settings({"APP_ENV": "production", "SESSION_SECRET": "short"})
    with pytest.raises(ConfigError):
        make_settings({"APP_ENV": "production", "SESSION_SECRET": "a" * 40})
    settings = make_settings(
        {"APP_ENV": "production", "SESSION_SECRET": "s" * 40 + "x", "SESSION_COOKIE_SECURE": "true"}
    )
    assert settings.app_env == "production"


def test_production_secure_cookie_required(make_settings):
    with pytest.raises(ConfigError):
        make_settings(
            {
                "APP_ENV": "production",
                "SESSION_SECRET": "s" * 40 + "x",
                "SESSION_COOKIE_SECURE": "false",
            }
        )
    settings = make_settings(
        {
            "APP_ENV": "production",
            "SESSION_SECRET": "s" * 40 + "x",
            "SESSION_COOKIE_SECURE": "true",
        }
    )
    assert settings.session_cookie_secure is True
    assert settings.allow_docs is False  # 生产强制关闭文档


def test_lan_http_requires_strong_secret_and_disables_docs(make_settings):
    with pytest.raises(ConfigError):
        make_settings({"APP_ENV": "lan", "SESSION_SECRET": ""})
    settings = make_settings(
        {
            "APP_ENV": "lan",
            "SESSION_SECRET": "s" * 40 + "x",
            "SESSION_COOKIE_SECURE": "false",
            "ALLOW_DOCS": "true",
        }
    )
    assert settings.app_env == "lan"
    assert settings.session_cookie_secure is False
    assert settings.allow_docs is False
    assert settings.log_format == "json"


def test_development_generates_ephemeral_secret(make_settings):
    settings = make_settings({"APP_ENV": "development", "SESSION_SECRET": ""})
    assert settings.session_secret is not None and len(settings.session_secret) >= 32


def test_secret_masked_in_repr(make_settings):
    settings = make_settings({"SESSION_SECRET": "super-secret-value-abcdefghijklmnop"})
    text = repr(settings)
    assert "super-secret-value-abcdefghijklmnop" not in text
    assert "***" in text


def test_invalid_limits(make_settings):
    with pytest.raises(ConfigError):
        make_settings({"MAX_UPLOAD_BYTES": "-1"})
    with pytest.raises(ConfigError):
        make_settings({"MAX_UPLOAD_BYTES": "abc"})


def test_invalid_timezone(make_settings):
    with pytest.raises(ConfigError):
        make_settings({"TIMEZONE": "Mars/Olympus"})


def test_invalid_cidr(make_settings):
    with pytest.raises(ConfigError):
        make_settings({"TRUSTED_PROXIES": "999.1.1.1/8"})
    settings = make_settings({"TRUSTED_PROXIES": "10.0.0.0/8, 192.168.1.1"})
    assert settings.trusted_proxies == ("10.0.0.0/8", "192.168.1.1")


def test_invalid_log_format(make_settings):
    with pytest.raises(ConfigError):
        make_settings({"LOG_FORMAT": "xml"})


def test_production_json_log_default(make_settings):
    settings = make_settings(
        {
            "APP_ENV": "production",
            "SESSION_SECRET": "s" * 40 + "x",
            "SESSION_COOKIE_SECURE": "true",
        }
    )
    assert settings.log_format == "json"


def test_load_settings_without_env_is_isolated():
    """不提供 env 时应从进程环境读取；本测试进程环境不含 APP_ENV 时回退 development。"""
    settings = load_settings(env={}, use_dotenv=False)
    assert settings.app_env == "development"

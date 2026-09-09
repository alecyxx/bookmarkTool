"""应用配置（BM-V1-003）。

分层规则：内置默认值 -> `.env` 文件（仅 development 便利）-> 环境变量 -> 注入覆盖（测试）。
- 生产环境缺少强随机 SESSION_SECRET 时拒绝启动；
- 非法布尔值、大小限制、URL 与密钥值给出启动错误；
- DEFAULT_WEB_SEARCH_ENGINE 只接受 google/bing/baidu；
- 敏感字段（session_secret）的字符串表示一律脱敏（repr=False + 日志从不输出）。

配置错误使用英文消息，避免 Windows 终端编码导致乱码误读。
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Windows 无系统 IANA tzdata；安装 tzdata 包后 zoneinfo 自动使用。
# 显式导入一次确保打包场景也携带。
try:  # pragma: no cover - 导入期保护
    import tzdata  # noqa: F401

except ImportError:  # pragma: no cover
    pass

APP_ROOT = Path(__file__).resolve().parent.parent

VALID_ENVS = ("development", "testing", "production")
VALID_SEARCH_ENGINES = ("google", "bing", "baidu")
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class ConfigError(ValueError):
    """启动期配置错误。消息不包含密钥值。"""


def _parse_bool(key: str, raw: str) -> bool:
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ConfigError(f"Invalid boolean for {key}: {raw!r}")


def _parse_int(key: str, raw: str) -> int:
    try:
        return int(raw.strip())
    except ValueError:
        raise ConfigError(f"Invalid integer for {key}: {raw!r}") from None


def _parse_cidrs(key: str, raw: str) -> tuple[str, ...]:
    """逗号分隔的 CIDR 列表，逐项校验合法性。"""
    items = [item.strip() for item in raw.split(",") if item.strip()]
    for item in items:
        try:
            import ipaddress

            ipaddress.ip_network(item, strict=False)
        except ValueError:
            raise ConfigError(f"Invalid CIDR for {key}: {item!r}") from None
    return tuple(items)


def _load_dotenv(path: Path) -> dict[str, str]:
    """极简 .env 解析：支持 KEY=VALUE、整行注释、整体引号包裹的值。不做变量展开。"""
    result: dict[str, str] = {}
    if not path.is_file():
        return result
    for lineno, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, raw = stripped.partition("=")
        key = key.strip()
        if not key:
            continue
        value = raw.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key in result:
            raise ConfigError(f"Duplicate key {key!r} in {path} at line {lineno}")
        result[key] = value
    return result


@dataclass(frozen=True)
class Settings:
    """只读配置快照。不要直接构造，使用 Settings.load()。"""

    app_env: str = "development"
    database_url: str = f"sqlite:///{APP_ROOT / 'dev_data' / 'bookmarks.db'}"
    # 安全与会话（session_secret 使用 repr=False 保证字符串表示脱敏）
    session_secret: str | None = field(default=None, repr=False)
    session_cookie_name: str = "bookmark_session"
    csrf_cookie_name: str = "bookmark_csrf"
    session_cookie_secure: bool = False
    session_cookie_http_only: bool = True
    session_cookie_same_site: str = "lax"
    session_max_age_seconds: int = 12 * 3600
    csrf_token_ttl_seconds: int = 12 * 3600
    login_max_attempts: int = 5
    login_lock_seconds: int = 15 * 60
    # 部署环境
    trusted_proxies: tuple[str, ...] = ()
    site_domain: str = ""
    timezone: str = "UTC"
    log_level: str = "INFO"
    log_format: str = "text"  # text | json；production 默认 json
    # 网络搜索首页（只影响首次高亮引擎）
    default_web_search_engine: str = "google"
    # favicon：V1 默认不显示第三方图标；显式开启才允许展示已导入的 HTTP(S) favicon URL
    enable_remote_favicons: bool = False
    # 上传与解析限制（第 07 阶段复用）
    max_upload_bytes: int = 10 * 1024 * 1024
    max_import_rows: int = 50_000
    max_parse_seconds: int = 60
    import_tmp_dir: str = ""  # 为空时使用 APP_ROOT/dev_data/import_tmp（生产应显式指向持久卷）
    import_job_expire_seconds: int = 24 * 3600
    # 字段长度上限（与数据模型一致）
    max_url_length: int = 4096
    max_title_length: int = 300
    max_description_length: int = 2000
    max_tag_length: int = 50
    max_category_name_length: int = 100
    # 开放接口（仅非 production 时可能开启）
    allow_docs: bool = False

    @classmethod
    def load(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        use_dotenv: bool = True,
        require_session_secret: bool = True,
    ) -> Settings:
        """从环境（可选注入 Mapping）构建配置。注入的 env 优先级最高。

        require_session_secret=False 供 Alembic/运维脚本等不需要会话密钥的场景使用。
        """
        source: dict[str, str] = {}
        if use_dotenv and cls._environ_app_env(env) == "development":
            source.update(_load_dotenv(APP_ROOT / ".env"))
        if env is not None:
            source.update({k: v for k, v in env.items() if v is not None})
        else:
            source.update({k: v for k, v in os.environ.items() if v is not None})
        return cls._from_mapping(source, require_session_secret=require_session_secret)

    @staticmethod
    def _environ_app_env(env: Mapping[str, str] | None) -> str:
        if env is not None and "APP_ENV" in env:
            return env["APP_ENV"]
        return os.environ.get("APP_ENV", "development")

    @classmethod
    def _from_mapping(
        cls, raw: Mapping[str, str], *, require_session_secret: bool = True
    ) -> Settings:
        def get(key: str, default: str | None = None) -> str | None:
            value = raw.get(key)
            return default if value is None or value == "" else value

        app_env = get("APP_ENV", "development") or "development"
        if app_env not in VALID_ENVS:
            raise ConfigError(f"Invalid APP_ENV: {app_env!r}, must be one of {VALID_ENVS}")

        database_url = get("DATABASE_URL")
        if database_url is None:
            # 默认开发库；生产部署必须显式配置 DATABASE_URL（部署模板指向 /data）
            database_url = f"sqlite:///{APP_ROOT / 'dev_data' / 'bookmarks.db'}"
        if not database_url.startswith("sqlite:///"):
            raise ConfigError("DATABASE_URL must be a sqlite:/// absolute or relative path in V1")

        secret = get("SESSION_SECRET")
        if secret is not None and not cls._is_strong_secret(secret):
            raise ConfigError(
                "SESSION_SECRET must be at least 32 characters and not all the same character"
            )
        if (
            app_env == "production"
            and require_session_secret
            and (secret is None or not cls._is_strong_secret(secret))
        ):
            raise ConfigError("SESSION_SECRET is required in production (random, >= 32 chars)")
        if secret is None:
            secret = secrets.token_hex(32)  # development/testing 自动生成，每次启动变化
            if app_env == "development":
                import logging

                logging.getLogger("app.config").warning(
                    "SESSION_SECRET not set in development; a random ephemeral secret was generated. "
                    "Set it in source/.env for stable sessions."
                )

        secure_raw = get("SESSION_COOKIE_SECURE")
        session_cookie_secure = (
            app_env == "production"
            if secure_raw is None
            else _parse_bool("SESSION_COOKIE_SECURE", secure_raw)
        )
        if app_env == "production" and not session_cookie_secure:
            raise ConfigError("SESSION_COOKIE_SECURE must be true in production")

        default_engine = get("DEFAULT_WEB_SEARCH_ENGINE", "google") or "google"
        if default_engine not in VALID_SEARCH_ENGINES:
            raise ConfigError(f"Invalid DEFAULT_WEB_SEARCH_ENGINE: {default_engine!r}")

        timezone = get("TIMEZONE", "UTC") or "UTC"
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError:
            raise ConfigError(f"Invalid TIMEZONE: {timezone!r}") from None

        log_format = get("LOG_FORMAT") or ("json" if app_env == "production" else "text")
        if log_format not in ("text", "json"):
            raise ConfigError(f"Invalid LOG_FORMAT: {log_format!r}")

        max_upload_bytes = _parse_int(
            "MAX_UPLOAD_BYTES", get("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024))
        )
        max_import_rows = _parse_int("MAX_IMPORT_ROWS", get("MAX_IMPORT_ROWS", "50000"))
        max_parse_seconds = _parse_int("MAX_PARSE_SECONDS", get("MAX_PARSE_SECONDS", "60"))
        session_max_age = _parse_int(
            "SESSION_MAX_AGE_SECONDS", get("SESSION_MAX_AGE_SECONDS", str(12 * 3600))
        )
        if (
            max_upload_bytes <= 0
            or max_import_rows <= 0
            or max_parse_seconds <= 0
            or session_max_age <= 0
        ):
            raise ConfigError("Upload/import/session limits must be positive integers")

        trusted = get("TRUSTED_PROXIES", "")
        allow_docs = app_env != "production" and _parse_bool(
            "ALLOW_DOCS", get("ALLOW_DOCS", "false" if app_env == "production" else "true")
        )
        if app_env == "production":
            allow_docs = False

        settings = cls(
            app_env=app_env,
            database_url=database_url,
            session_secret=secret,
            session_cookie_secure=session_cookie_secure,
            session_cookie_http_only=_parse_bool(
                "SESSION_COOKIE_HTTP_ONLY", get("SESSION_COOKIE_HTTP_ONLY", "true")
            ),
            session_cookie_same_site=get("SESSION_COOKIE_SAME_SITE", "lax") or "lax",
            session_max_age_seconds=session_max_age,
            csrf_token_ttl_seconds=_parse_int(
                "CSRF_TOKEN_TTL_SECONDS", get("CSRF_TOKEN_TTL_SECONDS", str(12 * 3600))
            ),
            login_max_attempts=_parse_int("LOGIN_MAX_ATTEMPTS", get("LOGIN_MAX_ATTEMPTS", "5")),
            login_lock_seconds=_parse_int(
                "LOGIN_LOCK_SECONDS", get("LOGIN_LOCK_SECONDS", str(15 * 60))
            ),
            trusted_proxies=_parse_cidrs("TRUSTED_PROXIES", trusted or ""),
            site_domain=get("SITE_DOMAIN", "") or "",
            timezone=timezone,
            log_level=(get("LOG_LEVEL", "INFO") or "INFO").upper(),
            log_format=log_format,
            default_web_search_engine=default_engine,
            enable_remote_favicons=_parse_bool(
                "ENABLE_REMOTE_FAVICONS", get("ENABLE_REMOTE_FAVICONS", "false")
            ),
            max_upload_bytes=max_upload_bytes,
            max_import_rows=max_import_rows,
            max_parse_seconds=max_parse_seconds,
            max_url_length=_parse_int("MAX_URL_LENGTH", get("MAX_URL_LENGTH", "4096")),
            max_title_length=_parse_int("MAX_TITLE_LENGTH", get("MAX_TITLE_LENGTH", "300")),
            max_description_length=_parse_int(
                "MAX_DESCRIPTION_LENGTH", get("MAX_DESCRIPTION_LENGTH", "2000")
            ),
            max_tag_length=_parse_int("MAX_TAG_LENGTH", get("MAX_TAG_LENGTH", "50")),
            max_category_name_length=_parse_int(
                "MAX_CATEGORY_NAME_LENGTH", get("MAX_CATEGORY_NAME_LENGTH", "100")
            ),
            import_tmp_dir=get("IMPORT_TMP_DIR", "") or "",
            import_job_expire_seconds=_parse_int(
                "IMPORT_JOB_EXPIRE_SECONDS", get("IMPORT_JOB_EXPIRE_SECONDS", str(24 * 3600))
            ),
            allow_docs=allow_docs,
        )
        if settings.session_cookie_same_site not in ("lax", "strict", "none"):
            raise ConfigError(
                f"Invalid SESSION_COOKIE_SAME_SITE: {settings.session_cookie_same_site!r}"
            )
        return settings

    @staticmethod
    def _is_strong_secret(value: str) -> bool:
        if len(value) < 32:
            return False
        return len(set(value)) > 1

    def __repr__(self) -> str:  # pragma: no cover - 防御性输出
        parts = []
        for f in self.__dataclass_fields__:
            value = getattr(self, f)
            if f == "session_secret":
                value = "***" if value else "<unset>"
            parts.append(f"{f}={value!r}")
        return f"Settings({', '.join(parts)})"


def load_settings(
    env: Mapping[str, str] | None = None,
    *,
    use_dotenv: bool = True,
    require_session_secret: bool = True,
) -> Settings:
    return Settings.load(
        env=env, use_dotenv=use_dotenv, require_session_secret=require_session_secret
    )

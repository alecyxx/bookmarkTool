"""会话与 CSRF 的签名/安全原语（BM-V1-202/203）。

- 会话 Cookie 值 = itsdangerous 签名载荷（SESSION_SECRET），服务端无状态验证；
- 载荷只包含管理员 ID、登录时间与 session_version（不含密码或敏感导入数据）；
- CSRF Token 为带过期时间与会话版本的签名值（double-submit cookie + 签名双重校验）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

_SESSION_SALT = "bookmark-session-v1"


def _secret_bytes(secret: str) -> bytes:
    return secret.encode("utf-8")


class SessionService:
    """登录 Session 的签发与验证。"""

    def __init__(self, secret: str, max_age_seconds: int):
        self._serializer = URLSafeTimedSerializer(_secret_bytes(secret), salt=_SESSION_SALT)
        self.max_age_seconds = max_age_seconds

    def issue(self, user_id: int, session_version: int) -> str:
        """签发新会话值（登录成功后调用；改密后旧会话由版本比对失效）。"""
        payload = {
            "uid": user_id,
            "sv": session_version,
            "iat": int(time.time()),
        }
        return self._serializer.dumps(payload)

    def load(self, raw_value: str | None) -> dict[str, Any] | None:
        """验证并返回载荷；过期/篡改/缺失一律返回 None（不产生 500）。"""
        if not raw_value:
            return None
        try:
            return self._serializer.loads(raw_value, max_age=self.max_age_seconds)
        except (BadSignature, SignatureExpired):
            return None


class CsrfService:
    """CSRF Token：值 = base64(expires.session_version.nonce.signature)。

    校验要求同时满足：签名有效、会话版本匹配、未过期、与请求 Cookie 中值一致。
    """

    def __init__(self, secret: str, ttl_seconds: int):
        self._secret = _secret_bytes(secret)
        self.ttl_seconds = ttl_seconds

    def issue(self, session_version: int) -> str:
        expires = int(time.time()) + self.ttl_seconds
        raw = f"{expires}.{session_version}.{secrets.token_urlsafe(24)}"
        signature = self._sign(raw)
        return base64.urlsafe_b64encode(f"{raw}.{signature}".encode()).decode("ascii")

    def verify(self, token: str | None, session_version: int) -> bool:
        if not token:
            return False
        try:
            decoded = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return False
        raw, _, signature = decoded.rpartition(".")
        if not hmac.compare_digest(signature, self._sign(raw)):
            return False
        try:
            expires_part, version_part, _nonce = raw.split(".", 2)
            if int(version_part) != session_version:
                return False  # 改密后旧 Token 立即失效
            if int(expires_part) < time.time():
                return False
        except ValueError:
            return False
        return True

    def _sign(self, raw: str) -> str:
        digest = hmac.new(self._secret, raw.encode("utf-8"), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii")

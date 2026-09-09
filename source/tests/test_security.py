"""密码哈希与会话/CSRF 原语测试（BM-V1-201/202/203）。"""

from __future__ import annotations

from app.services.security import hash_password, needs_rehash, verify_password
from app.services.session_service import CsrfService, SessionService

SECRET = "unit-test-secret-0123456789abcdef"


class TestPasswordHashing:
    def test_hash_and_verify(self):
        hashed = hash_password("correct horse battery staple")
        assert hashed != "correct horse battery staple"
        assert verify_password("correct horse battery staple", hashed) is True
        assert verify_password("wrong password", hashed) is False

    def test_hashes_are_salted_and_argon2id(self):
        first = hash_password("same-password-here")
        second = hash_password("same-password-here")
        assert first != second
        assert first.startswith("$argon2id$")

    def test_broken_hash_returns_false(self):
        assert verify_password("anything", "not-a-valid-hash") is False
        assert needs_rehash("not-a-valid-hash") is False

    def test_needs_rehash_same_parameters_false(self):
        hashed = hash_password("some-password-value")
        assert needs_rehash(hashed) is False


class TestSessionService:
    def test_roundtrip(self):
        service = SessionService(SECRET, max_age_seconds=3600)
        token = service.issue(1, 2)
        payload = service.load(token)
        assert payload is not None
        assert payload["uid"] == 1
        assert payload["sv"] == 2

    def test_tampered_rejected(self):
        service = SessionService(SECRET, max_age_seconds=3600)
        token = service.issue(1, 2)
        tampered = token[:-4] + ("abcd" if not token.endswith("abcd") else "dcba")
        assert service.load(tampered) is None

    def test_empty_and_none_rejected(self):
        service = SessionService(SECRET, max_age_seconds=3600)
        assert service.load(None) is None
        assert service.load("") is None

    def test_expired_rejected(self):
        service = SessionService(SECRET, max_age_seconds=-1)  # 立即过期
        token = service.issue(1, 2)
        assert service.load(token) is None


class TestCsrfService:
    def test_roundtrip_with_version(self):
        service = CsrfService(SECRET, ttl_seconds=3600)
        token = service.issue(3)
        assert service.verify(token, 3) is True
        assert service.verify(token, 4) is False  # 版本不匹配
        assert service.verify(None, 3) is False
        assert service.verify("", 3) is False

    def test_tampered_rejected(self):
        service = CsrfService(SECRET, ttl_seconds=3600)
        token = service.issue(1)
        assert service.verify(token + "x", 1) is False
        assert service.verify("garbage%%%", 1) is False

    def test_expired_rejected(self):
        # 攻击者无法对过期 Token 重新签名：跨密钥（不同 SESSION_SECRET）签发的 Token 必须被拒
        service = CsrfService(SECRET, ttl_seconds=3600)
        other = CsrfService(SECRET + "-different", ttl_seconds=3600)
        assert service.verify(other.issue(1), 1) is False

    def test_token_unique(self):
        service = CsrfService(SECRET, ttl_seconds=3600)
        assert service.issue(1) != service.issue(1)

"""认证与安全 HTTP 集成测试（BM-V1-202~207）。"""

from __future__ import annotations

from app.models.user import User

PASSWORD = "strong-password-1234"


def _csrf(client) -> str:
    return client.cookies.get("bookmark_csrf", "")


def _fresh_user(db_session) -> User:
    """绕过 identity map 读取数据库最新状态。"""
    db_session.expire_all()
    return db_session.query(User).first()


def get_login_page(client, next_target: str | None = None) -> object:
    url = "/login" + (f"?next={next_target}" if next_target else "")
    return client.get(url)


def do_login(
    client, username: str = "admin", password: str = PASSWORD, next_target: str = ""
) -> object:
    payload = {
        "username": username,
        "password": password,
        "csrf_token": _csrf(client),
    }
    if next_target:
        payload["next"] = next_target
    return client.post("/auth/login", data=payload)


def logged_in_client(client, make_admin):
    make_admin(password=PASSWORD)
    get_login_page(client)
    response = do_login(client)
    assert response.status_code == 303
    return client


class TestLoginFlow:
    def test_login_page_renders(self, client):
        response = get_login_page(client)
        assert response.status_code == 200
        assert "登录签栖" in response.text
        assert "个人搜索与书签首页" in response.text
        assert 'name="csrf_token"' in response.text

    def test_login_page_refreshes_invalid_csrf_cookie(self, client, make_admin):
        """旧服务实例签发的 CSRF Cookie 应在刷新登录页后自动恢复。"""
        make_admin(password=PASSWORD)
        client.cookies.set(
            "bookmark_csrf",
            "stale-token-from-old-instance",
            domain="testserver.local",
            path="/",
        )

        response = get_login_page(client)

        assert response.status_code == 200
        refreshed = _csrf(client)
        assert refreshed != "stale-token-from-old-instance"
        assert f'name="csrf_token" value="{refreshed}"' in response.text
        assert do_login(client).status_code == 303

    def test_unauthenticated_page_redirects_to_login(self, client):
        response = client.get("/")
        assert response.status_code == 303
        assert response.headers["location"].startswith("/login?next=%2F")

    def test_settings_page_protected(self, client):
        response = client.get("/settings")
        assert response.status_code == 303
        assert response.headers["location"].startswith("/login")

    def test_login_success_redirects_home_with_cookies(self, client, make_admin):
        make_admin(password=PASSWORD)
        get_login_page(client)
        response = do_login(client)
        assert response.status_code == 303
        assert response.headers["location"] == "/"
        set_cookie = response.headers.get("set-cookie", "")
        assert "bookmark_session=" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "SameSite=lax" in set_cookie
        # CSRF cookie 重签（登录前版本 0 -> 登录后绑定会话版本）
        assert "bookmark_csrf=" in set_cookie
        after = client.get("/")
        assert after.status_code == 200
        assert "聚合搜索" in after.text

    def test_login_respects_safe_next(self, client, make_admin):
        make_admin(password=PASSWORD)
        get_login_page(client, next_target="/bookmarks")
        response = do_login(client, next_target="/bookmarks")
        assert response.headers["location"] == "/bookmarks"

    def test_login_rejects_external_next(self, client, make_admin):
        """外部与协议相对 next 一律拒绝，回落到默认落点 /。"""
        make_admin(password=PASSWORD)
        for evil in ("https://evil.example.com", "//evil.example.com", "http://evil.example.com/x"):
            get_login_page(client, next_target=evil)
            response = do_login(client, next_target=evil)
            assert response.headers["location"] == "/", evil

    def test_login_default_landing_is_search_home(self, client, make_admin):
        logged_in_client(client, make_admin)
        response = client.get("/")
        assert response.status_code == 200
        assert "聚合搜索" in response.text
        assert 'class="nav-link active"' in response.text  # 搜索导航激活态


class TestLoginFailuresAndLockout:
    def test_wrong_password_unified_message(self, client, make_admin, db_session):
        make_admin(password=PASSWORD)
        get_login_page(client)
        response = do_login(client, password="wrong-password-value")
        assert response.status_code == 400
        assert "用户名或密码错误" in response.text
        user = _fresh_user(db_session)
        assert user.failed_login_count == 1

    def test_unknown_user_same_message(self, client, db_session):
        get_login_page(client)
        response = do_login(client, username="ghost")
        assert response.status_code == 400
        assert "用户名或密码错误" in response.text
        assert db_session.query(User).count() == 0

    def test_lockout_after_threshold(self, client, make_admin, db_session):
        make_admin(password=PASSWORD)
        get_login_page(client)
        for _ in range(5):
            response = do_login(client, password="wrong-password-value")
            assert response.status_code == 400
        user = _fresh_user(db_session)
        assert user.failed_login_count == 5
        assert user.locked_until is not None
        # 锁定期内即使密码正确也拒绝
        get_login_page(client)
        locked = do_login(client)
        assert locked.status_code == 403
        assert "临时锁定" in locked.text

    def test_unlock_admin_cli_restores_access(self, client, make_admin, db_session):
        make_admin(password=PASSWORD)
        from scripts.unlock_admin import unlock_admin

        for _ in range(5):
            get_login_page(client)
            do_login(client, password="wrong-password-value")
        assert unlock_admin(session=db_session) == 0
        get_login_page(client)
        assert do_login(client).status_code == 303

    def test_success_resets_failure_state(self, client, make_admin, db_session):
        make_admin(password=PASSWORD)
        for _ in range(2):
            get_login_page(client)
            do_login(client, password="wrong-password-value")
        get_login_page(client)
        assert do_login(client).status_code == 303
        user = _fresh_user(db_session)
        assert user.failed_login_count == 0
        assert user.locked_until is None
        assert user.last_login_at is not None

    def test_security_log_has_no_password(self, client, make_admin, caplog):
        import logging

        make_admin(password=PASSWORD)
        get_login_page(client)
        with caplog.at_level(logging.INFO):
            do_login(client, password="wrong-password-value")
        assert "wrong-password-value" not in caplog.text
        assert "login_failed" in caplog.text


class TestCsrf:
    def test_missing_token_rejected(self, client):
        response = client.post("/auth/login", data={"username": "a", "password": "b"})
        assert response.status_code == 403

    def test_wrong_token_rejected(self, client):
        get_login_page(client)
        response = client.post(
            "/auth/login",
            data={"username": "a", "password": "b", "csrf_token": "forged-token"},
        )
        assert response.status_code == 403

    def test_cookie_token_mismatch_rejected(self, client, make_admin):
        make_admin(password=PASSWORD)
        get_login_page(client)
        response = client.post(
            "/auth/login",
            data={"username": "admin", "password": PASSWORD, "csrf_token": "x" * 30},
        )
        assert response.status_code == 403

    def test_expired_login_form_refreshes_token_for_retry(self, client, make_admin):
        """登录表单令牌失效时，403 页面应换发令牌并允许直接重试。"""
        make_admin(password=PASSWORD)
        stale = "stale-token-from-old-instance"
        client.cookies.set("bookmark_csrf", stale, domain="testserver.local", path="/")

        response = client.post(
            "/auth/login",
            data={"username": "admin", "password": PASSWORD, "csrf_token": stale},
        )

        assert response.status_code == 403
        refreshed = _csrf(client)
        assert refreshed != stale
        assert f'name="csrf_token" value="{refreshed}"' in response.text
        assert do_login(client).status_code == 303

    def test_hx_request_cannot_bypass(self, client):
        get_login_page(client)
        response = client.post(
            "/auth/login",
            data={"username": "a", "password": "b"},
            headers={"HX-Request": "true"},
        )
        assert response.status_code == 403

    def test_get_is_not_mutating(self, client):
        # 安全 GET 不改变状态：登录页可无 CSRF 正常访问
        assert get_login_page(client).status_code == 200

    def test_old_csrf_rejected_after_password_change(self, client, make_admin):
        """改密后：旧 CSRF Token（绑定旧会话版本）不得再通过。"""
        logged_in_client(client, make_admin)
        old_csrf = _csrf(client)
        response = client.put(
            "/api/settings/password",
            json={"current_password": PASSWORD, "new_password": "another-strong-pass-99"},
            headers={"X-CSRF-Token": old_csrf},
        )
        assert response.status_code == 200
        assert client.cookies.get("bookmark_csrf") != old_csrf
        # 模拟旧标签页：仍持旧 CSRF Cookie，提交任何写请求都应被拒绝
        client.cookies.set("bookmark_csrf", old_csrf)
        rejected = client.put(
            "/api/settings/profile",
            json={"username": "hacker-name"},
            headers={"X-CSRF-Token": old_csrf},
        )
        assert rejected.status_code == 403


class TestSettings:
    def test_change_password_invalidates_old_sessions(self, client, make_admin):
        logged_in_client(client, make_admin)
        old_session = client.cookies.get("bookmark_session")
        response = client.put(
            "/api/settings/password",
            json={"current_password": PASSWORD, "new_password": "another-strong-pass-99"},
            headers={"X-CSRF-Token": _csrf(client)},
        )
        assert response.status_code == 200
        assert client.cookies.get("bookmark_session") != old_session
        # 旧会话 Cookie 直接失效（模拟另一标签页）
        client.cookies.set("bookmark_session", old_session)
        assert client.get("/settings").status_code == 303

    def test_change_password_wrong_current(self, client, make_admin):
        logged_in_client(client, make_admin)
        response = client.put(
            "/api/settings/password",
            json={"current_password": "wrong-current", "new_password": "another-strong-pass-99"},
            headers={"X-CSRF-Token": _csrf(client)},
        )
        assert response.status_code == 400
        assert "当前密码不正确" in response.text

    def test_change_password_weak_rejected(self, client, make_admin):
        logged_in_client(client, make_admin)
        response = client.put(
            "/api/settings/password",
            json={"current_password": PASSWORD, "new_password": "short"},
            headers={"X-CSRF-Token": _csrf(client)},
        )
        assert response.status_code == 422
        body = response.json()
        assert "new_password" in body["error"]["field_errors"]

    def test_update_profile_success_and_conflict(self, client, make_admin):
        logged_in_client(client, make_admin)
        response = client.put(
            "/api/settings/profile",
            json={"username": " 新用户名 "},
            headers={"X-CSRF-Token": _csrf(client)},
        )
        assert response.status_code == 200
        # 规范化冲突：与新用户名全角等价 -> 与自身不同但规范化同？与另一账号冲突测试
        make_admin(username="other", password=PASSWORD)
        response = client.put(
            "/api/settings/profile",
            json={"username": "OTHER"},
            headers={"X-CSRF-Token": _csrf(client)},
        )
        assert response.status_code == 422

    def test_profile_page_shows_username(self, client, make_admin):
        logged_in_client(client, make_admin)
        response = client.get("/settings")
        assert response.status_code == 200
        assert 'value="admin"' in response.text


class TestLogout:
    def test_logout_clears_cookies(self, client, make_admin):
        logged_in_client(client, make_admin)
        response = client.post("/auth/logout", data={"csrf_token": _csrf(client)})
        assert response.status_code == 303
        assert "bookmark_session" not in client.cookies
        assert client.get("/").status_code == 303


class TestSessionRobustness:
    def test_tampered_cookie_no_500(self, client, make_admin):
        logged_in_client(client, make_admin)
        client.cookies.set("bookmark_session", "tampered-value")
        response = client.get("/")
        assert response.status_code == 303  # 会话无效 -> 登录

    def test_expired_cookie_shape_no_500(self, client, make_admin):
        logged_in_client(client, make_admin)
        # 过期载荷：伪造一个 1 秒过期载荷需要可控时间源，此处验证格式损坏不 500
        client.cookies.set("bookmark_session", "abc.def.ghi")
        assert client.get("/").status_code == 303


class TestSecurityHeaders:
    def test_headers_present(self, client, make_admin):
        logged_in_client(client, make_admin)
        response = client.get("/")
        csp = response.headers.get("content-security-policy", "")
        assert "default-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp
        assert response.headers.get("x-content-type-options") == "nosniff"
        assert response.headers.get("referrer-policy") == "no-referrer"
        assert response.headers.get("x-frame-options") == "DENY"

    def test_csp_blocks_third_party_images_by_default(self, make_settings, db, make_admin):
        """远程 favicon 关闭时 CSP 不允许第三方图片。"""
        from app.main import create_app
        from fastapi.testclient import TestClient

        settings = make_settings({"DATABASE_URL": str(db.url), "ENABLE_REMOTE_FAVICONS": "false"})
        app = create_app(settings)
        with TestClient(app) as test_client:
            csp = test_client.get("/login").headers.get("content-security-policy", "")
            assert "img-src 'self' data:" in csp
            assert "img-src 'self' data: https:" not in csp

    def test_csp_allows_images_when_remote_favicons_enabled(self, make_settings, db):
        from app.main import create_app
        from fastapi.testclient import TestClient

        settings = make_settings({"DATABASE_URL": str(db.url), "ENABLE_REMOTE_FAVICONS": "true"})
        app = create_app(settings)
        with TestClient(app) as test_client:
            csp = test_client.get("/login").headers.get("content-security-policy", "")
            assert "img-src 'self' data: https:" in csp


class TestProductionSurface:
    def test_docs_disabled_in_production(self, make_settings, db):
        from app.main import create_app
        from fastapi.testclient import TestClient

        settings = make_settings(
            {
                "APP_ENV": "production",
                "SESSION_SECRET": "p" * 40 + "q",
                "SESSION_COOKIE_SECURE": "true",
                "DATABASE_URL": str(db.url),
            }
        )
        app = create_app(settings)
        with TestClient(app, raise_server_exceptions=False, follow_redirects=False) as test_client:
            assert test_client.get("/docs").status_code == 404
            assert test_client.get("/redoc").status_code == 404
            assert test_client.get("/openapi.json").status_code == 404
            assert test_client.get("/").status_code == 303  # 未登录跳转
            response = test_client.get("/login")
            assert response.headers.get("strict-transport-security", "") != ""

    def test_lan_http_disables_docs_without_hsts(self, make_settings, db):
        from app.main import create_app
        from fastapi.testclient import TestClient

        settings = make_settings(
            {
                "APP_ENV": "lan",
                "SESSION_SECRET": "l" * 40 + "n",
                "SESSION_COOKIE_SECURE": "false",
                "DATABASE_URL": str(db.url),
            }
        )
        app = create_app(settings)
        with TestClient(app, base_url="http://fnos.lan") as test_client:
            response = test_client.get("/login")
            assert response.status_code == 200
            assert response.headers.get("strict-transport-security") is None
            csrf_cookie = response.cookies.get(settings.csrf_cookie_name)
            assert csrf_cookie
            assert "Secure" not in response.headers.get("set-cookie", "")
            assert test_client.get("/docs").status_code == 404
            assert test_client.get("/redoc").status_code == 404
            assert test_client.get("/openapi.json").status_code == 404


class TestUploadLimit:
    def test_oversized_upload_rejected_413(self, make_settings, db):
        from app.main import create_app
        from fastapi.testclient import TestClient

        settings = make_settings({"DATABASE_URL": str(db.url), "MAX_UPLOAD_BYTES": "1024"})
        app = create_app(settings)
        with TestClient(app, raise_server_exceptions=False, follow_redirects=False) as test_client:
            response = test_client.post(
                "/auth/login",
                files={"file": ("big.bin", b"x" * 2048, "text/plain")},
            )
            assert response.status_code == 413
            request_id = response.headers.get("x-request-id")
            assert request_id
            assert response.headers.get("x-content-type-options") == "nosniff"
            assert response.json()["error"]["request_id"] == request_id

    def test_csrf_rejection_has_request_context(self, client, make_admin, caplog):
        logged_in_client(client, make_admin)
        with caplog.at_level("INFO", logger="app.access"):
            response = client.post(
                "/api/bookmarks",
                json={"title": "blocked", "url": "https://blocked.example"},
                headers={"X-CSRF-Token": "invalid-token"},
            )
        assert response.status_code == 403
        request_id = response.headers.get("x-request-id")
        assert request_id
        assert response.headers.get("content-security-policy")
        assert response.json()["error"]["request_id"] == request_id
        assert any("request_completed" in record.getMessage() and "status=403" in record.getMessage() for record in caplog.records)

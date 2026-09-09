"""管理员 CLI 测试（BM-V1-201/204/205）。"""

from __future__ import annotations

import pytest
from app.models.user import User
from app.services.security import verify_password
from scripts.create_admin import create_admin
from scripts.unlock_admin import unlock_admin


def test_create_admin_success(db_session):
    create_admin(" 管理员 ", "strong-password-1234", session=db_session)
    user = db_session.query(User).first()
    assert user is not None
    assert user.username == "管理员"
    assert verify_password("strong-password-1234", user.password_hash) is True
    assert user.password_hash != "strong-password-1234"


def test_create_admin_rejects_second(db_session):
    create_admin("admin", "strong-password-1234", session=db_session)
    with pytest.raises(SystemExit) as excinfo:
        create_admin("admin2", "strong-password-1234", session=db_session)
    assert "已存在" in str(excinfo.value)
    assert db_session.query(User).count() == 1


def test_create_admin_rejects_weak_password(db_session):
    with pytest.raises(SystemExit):
        create_admin("admin", "short", session=db_session)
    assert db_session.query(User).count() == 0


def test_create_admin_normalized_conflict(db_session):
    create_admin("admin", "strong-password-1234", session=db_session)
    with pytest.raises(SystemExit):
        create_admin("ＡＤＭＩＮ", "another-strong-pass-9", session=db_session)


def test_unlock_clears_lock(db_session):
    create_admin("admin", "strong-password-1234", session=db_session)
    user = db_session.query(User).first()
    from datetime import timedelta

    from app.database import now_utc

    user.locked_until = now_utc() + timedelta(seconds=900)
    user.failed_login_count = 5
    db_session.commit()
    assert unlock_admin(session=db_session) == 0
    db_session.refresh(user)
    assert user.locked_until is None
    assert user.failed_login_count == 0


def test_create_admin_no_default_account_in_fresh_db(db_session):
    assert db_session.query(User).count() == 0

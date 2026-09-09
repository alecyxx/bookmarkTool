"""pytest 内联诊断 v3：直接调用 auth.login。"""


def test_diag3(client, make_admin, db_session, db):
    from app.database import create_session_factory
    from app.services import auth_service as auth

    make_admin(password="strong-password-1234")
    settings = client.app.state.settings
    factory = create_session_factory(db)
    other = factory()
    try:
        user, error = auth.login(other, settings, "admin", "strong-password-1234")
        print("LOGIN RESULT:", user.id if user else None, error)
        if error:
            print("ERR CODE:", error.code)
    finally:
        other.close()

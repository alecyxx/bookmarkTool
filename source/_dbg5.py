"""诊断 GET /bookmarks 页面渲染。"""
import os
import traceback

from fastapi.testclient import TestClient

from app.config import load_settings
from app.database import create_session_factory, create_engine_from_settings
from app.main import create_app
from app.models.user import User
from app.services.normalize import normalize_username, strip_display_name
from app.services.security import hash_password
from tests.conftest import _upgrade_to_head

PASSWORD = "strong-password-1234"
url = "sqlite:///dev_data/_dbg5.db"
if os.path.exists("dev_data/_dbg5.db"):
    os.remove("dev_data/_dbg5.db")
_upgrade_to_head(url)
settings = load_settings(
    env={
        "APP_ENV": "testing",
        "SESSION_SECRET": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c",
        "DATABASE_URL": url,
    },
    use_dotenv=False,
)
engine = create_engine_from_settings(settings)
session = create_session_factory(engine)()
display = strip_display_name("admin")
session.add(
    User(username=display, normalized_username=normalize_username(display), password_hash=hash_password(PASSWORD))
)
session.commit()
session.close()
app = create_app(settings)
try:
    with TestClient(app, raise_server_exceptions=True, follow_redirects=False) as c:
        c.get("/login")
        csrf = c.cookies.get("bookmark_csrf")
        c.post("/auth/login", data={"username": "admin", "password": PASSWORD, "csrf_token": csrf})
        response = c.get("/bookmarks", headers={"Accept": "text/html"})
        print("page status:", response.status_code)
        print(response.text[:400])
except Exception:
    traceback.print_exc()

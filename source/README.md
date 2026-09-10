# Bookmark Manager V1 — 工程说明（阶段 01）

个人浏览器首页与书签管理系统 V1：单用户、单体、服务端渲染。
技术栈：**FastAPI + Jinja2 + HTMX + SQLAlchemy + SQLite（Alembic 迁移）**。

设计基线：[docs](../docs/个人书签管理系统%20V1%20开发设计文档.md) · 任务索引：[docs/tasks](../docs/tasks/README.md)

## Python 支持版本

开发与验证基于 **Python 3.14**；代码要求 `>=3.11`（`pyproject.toml` 已声明）。
安装确定性由 `requirements.txt`（运行依赖）与 `requirements-dev.txt`（开发依赖）的精确版本锁定保证。

## 目录结构（对应设计文档 §42）

```text
source/
├── app/
│   ├── main.py            # 应用工厂与 /health/live
│   ├── config.py          # 分层配置与启动校验
│   ├── errors.py          # 稳定错误结构 code/message/field_errors
│   ├── logging_setup.py   # 结构化日志（text/json）
│   ├── middleware.py      # request_id 与访问日志
│   ├── template_utils.py  # 页面渲染辅助与主导航定义
│   ├── routers/           # 页面路由（home 已建，其余随阶段加入）
│   ├── templates/         # Jinja2 模板
│   └── static/            # CSS / JS / vendor(Bootstrap5+htmx) / icons
├── migrations/            # Alembic 迁移（阶段 02 初始化）
├── scripts/               # quality.py 质量门禁等
└── tests/                 # pytest（隔离配置与数据库）
```

## 首次启动（开发）

```powershell
cd d:\bookmarkTool\source
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
Copy-Item .env.example .env        # 可选：修改为本机配置
uvicorn app.main:create_app --factory --reload --port 8000
```

浏览器访问 <http://127.0.0.1:8000/>；存活探针 <http://127.0.0.1:8000/health/live>。

注意：

- 应用启动与运行**不会**自动建库或执行迁移（阶段 02 后通过 `alembic upgrade head` 显式执行）；
- `APP_ENV=lan|production` 时缺少强随机 `SESSION_SECRET` 将拒绝启动；
- `lan` 仅用于可信内网 HTTP：关闭文档接口但允许非 Secure Cookie；
- 开发环境未设置 `SESSION_SECRET` 时自动生成临时密钥（每次启动变化）。

## 质量门禁（本地与 CI 共用一条命令）

```powershell
python -m scripts.quality
```

包含：`ruff check` → `ruff format --check` → 模板/静态资源检查 → `pytest`（含覆盖率）。
任一步失败即以非零状态退出。快速验证可加 `--no-cov`。

## 配置项（BM-V1-003）

复制 `.env.example` 为 `.env` 后修改。关键项：

| 变量 | 默认 | 说明 |
|---|---|---|
| `APP_ENV` | `development` | `development` / `testing` / `lan` / `production` |
| `DATABASE_URL` | `sqlite:///dev_data/bookmarks.db` | V1 只接受 SQLite |
| `SESSION_SECRET` | development 自动生成 | lan/production 必填且 ≥32 字符强随机 |
| `SESSION_COOKIE_SECURE` | 跟随 `APP_ENV` | lan 为 false；production 必须为 true |
| `DEFAULT_WEB_SEARCH_ENGINE` | `google` | 仅 `google` / `bing` / `baidu` |
| `ENABLE_REMOTE_FAVICONS` | `false` | 默认不显示第三方 favicon |
| `TRUSTED_PROXIES` | 空 | 逗号分隔 CIDR |
| `TIMEZONE` | `UTC` | IANA 时区，用于展示 |
| `MAX_UPLOAD_BYTES` | 10485760 | 10 MiB |
| `MAX_IMPORT_ROWS` | 50000 | 单次导入最大条数 |

## 安全约定

- 所有改变状态的请求必须携带 CSRF（阶段 03 统一接入）；
- Session Cookie：Secure/HttpOnly/SameSite=Lax、固定名、12 小时；
- 日志不记录密码、Cookie、CSRF Token、上传正文；访问日志不含 query；
- 生产环境关闭 `/docs`、`/redoc` 与 OpenAPI JSON。

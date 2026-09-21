# 签栖（Qianqi）

一个面向个人使用的书签与浏览器首页管理系统。项目采用 FastAPI 服务端渲染，提供分类导航、书签管理、导入导出、回收站、备份与恢复等功能。

当前版本定位为单用户、单实例应用，默认使用 SQLite，适合个人设备或可信内网部署。它不是多租户 SaaS，也不建议直接暴露到公网。

## 技术栈

- Python 3.11+
- FastAPI + Jinja2 + HTMX
- SQLAlchemy + Alembic
- SQLite
- Bootstrap 5（本地静态资源）

## 本地运行

```powershell
Set-Location .\source
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
Copy-Item .env.example .env
alembic upgrade head
uvicorn app.main:create_app --factory --reload --port 8000
```

打开 <http://127.0.0.1:8000/>。开发环境会生成临时 Session 密钥；部署到 `lan` 或 `production` 前，必须配置强随机的 `SESSION_SECRET`。

## 文档

- [架构与产品边界](docs/architecture.md)
- [运维手册](docs/ops/运维手册.md)
- [1 GB VPS 单文件部署](docs/ops/1GB-VPS部署指南.md)
- [fnOS 部署指南](docs/ops/fnOS部署指南.md)
- [发布流程](deploy/RELEASE.md)
- [贡献指南](CONTRIBUTING.md)
- [安全策略](SECURITY.md)

## 开发与验证

```powershell
Set-Location .\source
python -m scripts.quality
```

命令会执行静态检查、格式检查、模板/静态资源检查和测试。提交前请确认没有把 `.env`、数据库、备份、日志或个人数据加入 Git。

## 开源边界

仓库只保留可复用的源代码、测试、迁移、部署模板和公开运行文档。开发任务拆分、内部审查、发布评审、交付记录和环境审计属于维护者私有资料，不作为项目运行所需内容发布。

## 许可证

本项目采用 [MIT License](LICENSE)。

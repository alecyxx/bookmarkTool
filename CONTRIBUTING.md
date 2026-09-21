# 贡献指南

感谢参与签栖（Qianqi）开发。

## 开始开发

```powershell
Set-Location .\source
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
Copy-Item .env.example .env
alembic upgrade head
```

开发前请阅读 [架构与产品边界](docs/architecture.md)，并保持单用户、单实例和 SQLite 的 V1 边界，除非变更明确扩展了产品契约。

## 提交前检查

```powershell
python -m scripts.quality
```

新增数据库结构必须提供连续的 Alembic 迁移；不要修改已经发布的迁移。新增或修复功能应同时补充测试，并考虑 CSRF、权限边界、并发版本和导入数据异常路径。

## Pull Request 约定

- 说明问题、解决方案和验证命令。
- 保持一个 PR 聚焦一个主题，避免混入个人环境文件或无关格式化。
- 不提交真实凭据、个人书签、数据库、备份、日志或内部审查资料。
- UI 变更请附主要页面截图或复现步骤。
- 破坏性变更、数据迁移和部署行为变化必须明确写出升级与回滚影响。

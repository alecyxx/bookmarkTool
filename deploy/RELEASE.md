# 发布迁移流程（BM-V1-803）

本文件是通用发布流程入口；Docker 详细步骤见[运维手册](../docs/ops/运维手册.md)，VPS 单文件步骤见[1 GB VPS 部署指南](../docs/ops/1GB-VPS部署指南.md)。

## 固定顺序

```
备份 → 校验候选制品 → 停服务 → 迁移（失败即中止） → 切换制品 → ready 检查
```

## 发布清单

- [ ] 通过质量门禁：`python -m scripts.quality`（ruff + 模板检查 + 覆盖率测试）
- [ ] 全量测试绿（含阶段 10 验收回归用例）
- [ ] 执行 `scripts.backup --type weekly` 且备份文件 + SHA-256 清单存在
- [ ] Docker 镜像使用固定版本标签；VPS 可执行文件使用版本化文件名和 SHA-256 清单
- [ ] 迁移前 `docker compose stop app`；迁移失败不启动新镜像（回滚 = 旧镜像 + 原库）
- [ ] 启动后 `docker compose ps` healthy 且 `/health/ready` 返回 `{"status":"ok"}`
- [ ] revision 不匹配演练：`/health/ready` 503、日志含 `revision_mismatch`、不泄露详情
- [ ] VPS 制品执行 `qianqi self-check`，模板、静态资源和迁移资源齐全

## 发布候选（RC）验收

RC 镜像仅用于演练环境：全新部署、上一版本升级、迁移失败中止三种路径各执行一次并留档
（记录：时间、镜像标签、迁移 revision、ready 状态、备份文件）。

## 版本与标签约定

- 镜像标签或 `qianqi-<版本>` 文件名 = 发布版本；RC 用 `-rc1` 后缀，仅演练环境；
- 数据库迁移 revision 与 `app/services/ops.py::EXPECTED_DB_REVISION` 一致（漂移由测试防护）。

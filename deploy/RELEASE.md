# 发布迁移流程（BM-V1-803）

本文件是发布流程入口；**详细可执行步骤（含全新部署/升级/迁移失败/回滚演练）见 [运维手册](../docs/ops/运维手册.md) §2、§3、§9**。

## 固定顺序

```
备份 → 迁移（维护窗口，失败即中止） → 候选镜像 → ready 检查 → 放流量
```

## 发布清单

- [ ] 通过质量门禁：`python -m scripts.quality`（ruff + 模板检查 + 覆盖率测试）
- [ ] 全量测试绿（含阶段 10 验收回归用例）
- [ ] 执行 `scripts.backup --type weekly` 且备份文件 + SHA-256 清单存在
- [ ] 镜像使用固定版本标签（`IMAGE_TAG`），不使用浮动标签
- [ ] 迁移前 `docker compose stop app`；迁移失败不启动新镜像（回滚 = 旧镜像 + 原库）
- [ ] 启动后 `docker compose ps` healthy 且 `/health/ready` 返回 `{"status":"ok"}`
- [ ] revision 不匹配演练：`/health/ready` 503、日志含 `revision_mismatch`、不泄露详情

## 发布候选（RC）验收

RC 镜像仅用于演练环境：全新部署、上一版本升级、迁移失败中止三种路径各执行一次并留档
（记录：时间、镜像标签、迁移 revision、ready 状态、备份文件）。

## 版本与标签约定

- 镜像标签 = 发布版本（如 `1.0.0`、`1.1.0`）；RC 用 `-rc1` 后缀，仅演练环境；
- 数据库迁移 revision 与 `app/services/ops.py::EXPECTED_DB_REVISION` 一致（漂移由测试防护）。

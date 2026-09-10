# fnOS Docker 纯内网 HTTP 部署指南

适用场景：在可信局域网中运行一套独立签栖实例。它不与 VPS 同步，不使用 Caddy、HTTPS、DDNS、端口转发或其他反向代理。

访问方式：`http://<fnOS固定内网IP>:18000`。部署文件为 `deploy/docker-compose.fnos.yml` 和 `deploy/.env.fnos-lan.example`。

## 1. 安全边界

内网 HTTP 会以明文传输 Session Cookie。仅在以下条件全部满足时使用：

- fnOS 位于可信家庭或办公 LAN/VLAN；
- Wi-Fi 使用可靠加密，不存在访客或不可信设备混入同一网络；
- 路由器不把 18000 转发到公网，fnOS 防火墙不允许 WAN 访问；
- fnOS 使用固定内网 IPv4。

应用仍保留强随机 Session 密钥、HttpOnly、SameSite、CSRF、登录限流和关闭 API 文档；仅关闭 Cookie 的 `Secure` 标志和 HSTS。

## 2. 准备目录和文件

通过 fnOS 文件管理或 SMB，把仓库完整复制到存储池目录，保留：

```text
<FNOS_APP_DIR>/
├── source/
└── deploy/
    ├── docker-compose.fnos.yml
    └── .env.fnos-lan.example
```

在 fnOS SSH 终端设置实际路径；占位路径不能原样使用：

```bash
FNOS_APP_DIR="/实际存储池路径/bookmarkTool"
FNOS_DATA_DIR="/实际存储池路径/bookmarkTool-data"
FNOS_BACKUP_DIR="/实际存储池路径/bookmarkTool-backups"

mkdir -p "$FNOS_DATA_DIR" "$FNOS_BACKUP_DIR"
chown -R 10001:10001 "$FNOS_DATA_DIR" "$FNOS_BACKUP_DIR"
chmod 700 "$FNOS_DATA_DIR" "$FNOS_BACKUP_DIR"
cd "$FNOS_APP_DIR/deploy"
cp .env.fnos-lan.example .env
chmod 600 .env
```

后续重新登录 SSH 时，先重新设置这三个路径变量。

## 3. 配置

```bash
openssl rand -hex 32
```

编辑 `deploy/.env`：

```dotenv
APP_ENV=lan
SESSION_SECRET=<上一步生成的64字符随机值>
SESSION_COOKIE_SECURE=false
ALLOW_DOCS=false
LOG_LEVEL=info
LOG_FORMAT=json
TIMEZONE=Asia/Shanghai
IMAGE_TAG=1.0.0

BIND_IP=192.168.1.20
HTTP_PORT=18000
DATA_DIR=/实际存储池路径/bookmarkTool-data
BACKUP_DIR=/实际存储池路径/bookmarkTool-backups
```

`BIND_IP` 必须是 fnOS 固定内网 IPv4，不能写 `0.0.0.0`。fnOS 和 VPS 使用不同的 `SESSION_SECRET` 和数据目录；`.env` 不得提交到 Git。

## 4. 首次构建与启动

```bash
cd "$FNOS_APP_DIR/deploy"

# 展开检查：只能看到 fnOS内网IP:18000 -> 容器8000
docker compose -f docker-compose.fnos.yml config
docker compose -f docker-compose.fnos.yml build app

# 应用不会自动建库，必须先迁移
docker compose -f docker-compose.fnos.yml run --rm app python -m alembic upgrade head
docker compose -f docker-compose.fnos.yml up -d
docker compose -f docker-compose.fnos.yml ps
```

`app` 应显示 `healthy`。创建管理员，密码只在交互终端输入：

```bash
docker compose -f docker-compose.fnos.yml exec app python -m scripts.create_admin
```

## 5. 验收

```bash
cd "$FNOS_APP_DIR/deploy"
BIND_IP=$(sed -n 's/^BIND_IP=//p' .env)
HTTP_PORT=$(sed -n 's/^HTTP_PORT=//p' .env)

curl -fsS "http://${BIND_IP}:${HTTP_PORT}/health/ready"
docker compose -f docker-compose.fnos.yml logs --tail=100 app
```

浏览器访问 `http://<BIND_IP>:<HTTP_PORT>`，完成登录、创建书签、分类、软删除与恢复。随后重启并确认数据仍在：

```bash
docker compose -f docker-compose.fnos.yml restart
docker compose -f docker-compose.fnos.yml ps
curl -fsS "http://${BIND_IP}:${HTTP_PORT}/health/ready"
```

再从局域网外检查 18000 不可达；该项未确认时不能视为完成部署。

## 6. 备份与计划任务

```bash
docker compose -f docker-compose.fnos.yml exec -T app \
  python -m scripts.backup --type daily --dir /data/backups
ls -lh "$FNOS_BACKUP_DIR"
```

在 fnOS 计划任务中以 root 创建以下任务；如果界面分别填写时间与命令，就按每行拆开。必须把 `<FNOS_APP_DIR>` 换成绝对路径：

```cron
30 2 * * * cd <FNOS_APP_DIR>/deploy && docker compose -f docker-compose.fnos.yml exec -T app python -m scripts.backup --type daily --dir /data/backups
35 2 * * 0 cd <FNOS_APP_DIR>/deploy && docker compose -f docker-compose.fnos.yml exec -T app python -m scripts.backup --type weekly --dir /data/backups
40 2 1 * * cd <FNOS_APP_DIR>/deploy && docker compose -f docker-compose.fnos.yml exec -T app python -m scripts.backup --type monthly --dir /data/backups
0 3 * * * cd <FNOS_APP_DIR>/deploy && docker compose -f docker-compose.fnos.yml exec -T app python -m scripts.cleanup_import_jobs
```

备份目录还应由 fnOS 快照或另一台设备复制一份。不要复制运行中的 `bookmarks.db`、`-wal` 或 `-shm` 文件代替备份。

## 7. 升级与回退

```bash
cd "$FNOS_APP_DIR/deploy"
docker compose -f docker-compose.fnos.yml exec -T app python -m scripts.backup --type weekly --dir /data/backups

# 上传新 source，并把 .env 中 IMAGE_TAG 改为新版本
docker compose -f docker-compose.fnos.yml build app
docker compose -f docker-compose.fnos.yml stop app
docker compose -f docker-compose.fnos.yml run --rm app python -m alembic upgrade head
docker compose -f docker-compose.fnos.yml up -d
docker compose -f docker-compose.fnos.yml ps
```

迁移失败时不要启动新版本。迁移已经成功但新版本异常时，按[运维手册 §9](运维手册.md#9-回滚上一版本)恢复升级前备份并启用上一镜像标签。

## 8. 完成标准

- 浏览器通过 `http://<fnOS内网IP>:18000` 正常登录和操作；
- 端口只绑定 fnOS 固定内网 IPv4，未绑定 `0.0.0.0`；
- 没有 Caddy、HTTPS、宿主机 80/443 或公网端口转发；
- 容器重启后数据存在；
- 手动备份生成数据库与 SHA-256 清单，连续两次计划备份成功。

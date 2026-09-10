# 1 GB VPS 单文件部署指南

适用：64 位 Debian/Ubuntu VPS，独立运行一套签栖。VPS 不安装 Docker、Python、venv 或 pip；应用以一个 `qianqi` 可执行文件发布，SQLite、配置和备份外置。

构建文件：`deploy/Dockerfile.vps-binary`。部署文件：`deploy/bookmark-vps.service`、`deploy/bookmark-vps.env.example`、`deploy/bookmark-vps.cron.example`。

## 1. 构建发布文件

在任意装有 Docker Buildx 的机器执行；Docker 只用于构建，不装到 VPS：

```powershell
Set-Location D:\bookmarkTool
docker buildx build --platform linux/amd64 `
  --file deploy/Dockerfile.vps-binary `
  --output type=local,dest=dist source
```

产物：

- `dist/qianqi-linux-amd64`
- `dist/qianqi-linux-amd64.sha256`

构建过程会执行 `self-check`，确认模板、静态资源和 Alembic 迁移均已打入文件。ARM64 VPS 将平台改成 `linux/arm64`，产物名相应为 `qianqi-linux-arm64`。

## 2. VPS 一次性准备

确认域名已解析到 VPS，防火墙只开放 SSH、80、443，然后执行：

```bash
sudo apt update
sudo apt install -y ca-certificates openssl cron

sudo useradd --system --home-dir /var/lib/bookmark \
  --shell /usr/sbin/nologin bookmark
sudo install -d -o root -g root -m 755 /opt/bookmark/releases /etc/bookmark
sudo install -d -o bookmark -g bookmark -m 700 \
  /var/lib/bookmark /var/lib/bookmark/import_tmp /var/backups/bookmark
```

若 `bookmark` 已存在，先用 `id bookmark` 确认后忽略 `useradd` 报错。

1 GB 机器建议有 1 GB swap；已有 swap 不重复创建：

```bash
swapon --show
sudo fallocate -l 1G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

仅在 `swapon --show` 没有输出时执行创建命令。

## 3. 上传并安装

开发机执行：

```powershell
scp .\dist\qianqi-linux-amd64 `
  .\dist\qianqi-linux-amd64.sha256 `
  .\deploy\bookmark-vps.service `
  .\deploy\bookmark-vps.env.example `
  .\deploy\bookmark-vps.cron.example `
  root@<VPS_IP>:/tmp/
```

VPS 执行；`0.1.0` 替换为本次发布版本：

```bash
cd /tmp
sha256sum -c qianqi-linux-amd64.sha256

sudo install -m 755 qianqi-linux-amd64 /opt/bookmark/releases/qianqi-0.1.0
sudo ln -sfn /opt/bookmark/releases/qianqi-0.1.0 /opt/bookmark/qianqi
sudo install -m 640 -o root -g bookmark \
  bookmark-vps.env.example /etc/bookmark/bookmark.env
sudo install -m 644 bookmark-vps.service /etc/systemd/system/bookmark.service
sudo install -m 644 bookmark-vps.cron.example /etc/cron.d/bookmark-vps
```

生成密钥并编辑配置：

```bash
openssl rand -hex 32
sudoedit /etc/bookmark/bookmark.env
```

只需把 `SESSION_SECRET` 替换为生成的 64 字符值。VPS 与 fnOS 必须使用不同密钥和数据库。

## 4. 初始化并启动

```bash
sudo -u bookmark env HOME=/var/lib/bookmark /opt/bookmark/qianqi self-check
sudo -u bookmark env HOME=/var/lib/bookmark /opt/bookmark/qianqi migrate
sudo -u bookmark env HOME=/var/lib/bookmark /opt/bookmark/qianqi create-admin

sudo systemctl daemon-reload
sudo systemctl enable --now bookmark cron
sudo systemctl status bookmark --no-pager
```

程序固定监听 `127.0.0.1:8000`、单 worker，并由 systemd 设置 512 MB 内存上限。

## 5. 配置公网 HTTPS

按 [Caddy 官方 Debian/Ubuntu 安装说明](https://caddyserver.com/docs/install#debian-ubuntu-raspbian)安装稳定版，写入 `/etc/caddy/Caddyfile`：

```caddyfile
bookmarks.example.com {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8000
}
```

替换为真实域名，然后执行：

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
curl -fsS https://<真实域名>/health/ready
```

成功响应应为 `{"status":"ok"}`。确认 `ss -lntp` 中 8000 仅绑定 `127.0.0.1`。

## 6. 日常维护

查看状态和日志：

```bash
systemctl show bookmark -p ActiveState -p NRestarts -p MemoryCurrent -p MemoryPeak
journalctl -u bookmark -n 100 --no-pager
```

手动备份与恢复演练：

```bash
sudo -u bookmark env HOME=/var/lib/bookmark \
  /opt/bookmark/qianqi backup --type weekly --dir /var/backups/bookmark
sudo -u bookmark env HOME=/var/lib/bookmark \
  /opt/bookmark/qianqi restore-verify --backup /var/backups/bookmark/<备份文件>.db
```

cron 已配置日备份、周备份、月备份和临时任务清理；日志位于 `/var/backups/bookmark/cron.log`。至少把加密备份复制到 VPS 之外。

## 7. 升级

固定顺序：备份 → 上传并校验新文件 → 停服务 → 迁移 → 切换 → ready。

```bash
# 1. 先备份；失败立即中止
sudo -u bookmark env HOME=/var/lib/bookmark \
  /opt/bookmark/qianqi backup --type weekly --dir /var/backups/bookmark

# 2. 新文件已上传到 /tmp 后校验并安装，先不切换软链接
cd /tmp
sha256sum -c qianqi-linux-amd64.sha256
sudo install -m 755 qianqi-linux-amd64 /opt/bookmark/releases/qianqi-<新版本>
sudo -u bookmark env HOME=/var/lib/bookmark \
  /opt/bookmark/releases/qianqi-<新版本> self-check

# 3. 维护窗口内迁移并切换
sudo systemctl stop bookmark
sudo -u bookmark env HOME=/var/lib/bookmark \
  /opt/bookmark/releases/qianqi-<新版本> migrate
sudo ln -sfn /opt/bookmark/releases/qianqi-<新版本> /opt/bookmark/qianqi
sudo systemctl start bookmark
curl -fsS https://<真实域名>/health/ready
```

迁移失败时不要切换软链接，直接启动旧版本。正常升级只替换一个可执行文件，不再解源码、建 venv 或安装依赖。

## 8. 回退

- 迁移未执行或失败：把 `/opt/bookmark/qianqi` 链回旧版本并启动。
- 迁移已成功：不能只回退程序；先停服务，按[运维手册 §9](运维手册.md#9-回滚上一版本)恢复升级前备份，再链接旧版本。

示例：

```bash
sudo systemctl stop bookmark
sudo install -o bookmark -g bookmark -m 600 \
  /var/backups/bookmark/<升级前备份>.db /var/lib/bookmark/bookmarks.db.restore
sudo mv /var/lib/bookmark/bookmarks.db.restore /var/lib/bookmark/bookmarks.db
sudo rm -f /var/lib/bookmark/bookmarks.db-wal /var/lib/bookmark/bookmarks.db-shm
sudo ln -sfn /opt/bookmark/releases/qianqi-<旧版本> /opt/bookmark/qianqi
sudo -u bookmark env HOME=/var/lib/bookmark /opt/bookmark/qianqi migrate
sudo systemctl start bookmark
curl -fsS https://<真实域名>/health/ready
```

## 9. 完成标准

- `self-check`、数据库迁移和 HTTPS ready 全部成功；
- 管理员可以登录，核心增删改查和一次最大规模导入通过；
- 8000 未暴露公网，无 OOM 或持续 swap 增长；
- 手动备份及 `restore-verify` 成功，VPS 外存在可恢复副本；
- 主机重启后签栖、Caddy 和 cron 均正常。

说明：SCIE 首次运行会把内置 Python 解压到 `/var/lib/bookmark` 下的私有缓存，之后复用；这是自动过程，不需要安装或维护 Python。

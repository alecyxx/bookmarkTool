# 1 GB VPS 原生部署指南

适用场景：在 1 GB 内存的 Debian/Ubuntu VPS 上运行一套独立签栖实例。它拥有自己的数据库、管理员、密钥和备份，不与 fnOS 同步。

本机采用 `systemd + Python venv + Caddy`，不安装 Docker。原因是应用本身只有单 Python 进程和 SQLite；在 1 GB 专用小机上，省去 Docker daemon/containerd 的常驻开销更有价值。只有 VPS 本来就在运行 Docker、且更看重环境一致性时，才考虑容器方案。

使用文件：`deploy/bookmark-vps.service`、`deploy/bookmark-vps.env.example`。通用迁移和数据回滚规则仍以[运维手册](运维手册.md)为准。

## 1. 部署前确认

- 64 位 Debian/Ubuntu，至少 1 GB 内存、5 GB 可用磁盘；
- Python 3.11 或更高版本；
- 一个解析到 VPS 公网 IP 的域名；
- 云防火墙允许 TCP 80/443，SSH 仅允许可信来源；
- VPS 不运行 MySQL、Redis、桌面环境等无关常驻服务。

```bash
dpkg --print-architecture
python3 --version
free -h
df -h /
```

## 2. 配置 1 GB swap

```bash
swapon --show
```

没有输出时创建一次；已有 swap 不要重复执行：

```bash
sudo fallocate -l 1G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h
```

swap 只用于避免瞬时 OOM；如果系统长期换页，应减少其他服务或升级内存。

## 3. 安装运行环境

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip sqlite3 openssl ca-certificates
```

按 [Caddy 官方 Debian/Ubuntu 安装说明](https://caddyserver.com/docs/install#debian-ubuntu-raspbian)安装稳定版，然后确认：

```bash
caddy version
systemctl status caddy --no-pager
```

## 4. 创建低权限账户和目录

```bash
sudo useradd --system --home /nonexistent --shell /usr/sbin/nologin bookmark
sudo mkdir -p /opt/bookmark /var/lib/bookmark/import_tmp /var/backups/bookmark /etc/bookmark
sudo chown -R bookmark:bookmark /var/lib/bookmark /var/backups/bookmark
sudo chmod 700 /var/lib/bookmark /var/backups/bookmark
```

如果 `bookmark` 用户已存在，`useradd` 报错可忽略；先用 `id bookmark` 确认身份正确。

## 5. 从开发机上传发布包

在 Windows 开发机执行。`git archive` 只打包已提交的 `source`，不会携带测试数据库、虚拟环境或密钥：

```powershell
Set-Location D:\bookmarkTool
git archive --format=tar --prefix=source/ `
  --output=bookmark-source-1.0.0.tar HEAD:source

scp .\bookmark-source-1.0.0.tar root@<VPS_IP>:/opt/bookmark/
scp .\deploy\bookmark-vps.service root@<VPS_IP>:/tmp/
scp .\deploy\bookmark-vps.env.example root@<VPS_IP>:/tmp/
```

没有 root SSH 时，上传到普通用户目录，再使用 `sudo` 移到目标路径。

在 VPS 解包并安装依赖：

```bash
sudo tar -xf /opt/bookmark/bookmark-source-1.0.0.tar -C /opt/bookmark
sudo python3 -m venv /opt/bookmark/venv-1.0.0
sudo /opt/bookmark/venv-1.0.0/bin/python -m pip install --upgrade pip
sudo /opt/bookmark/venv-1.0.0/bin/python -m pip install -r /opt/bookmark/source/requirements.txt
sudo /opt/bookmark/venv-1.0.0/bin/python -m pip check
sudo ln -sfn /opt/bookmark/venv-1.0.0 /opt/bookmark/venv
sudo chown -R root:root /opt/bookmark/source /opt/bookmark/venv-1.0.0
sudo chmod -R a-w /opt/bookmark/source
```

依赖安装若提示缺少编译工具，再执行 `sudo apt install -y build-essential python3-dev` 后重试；不要预先安装整套编译环境。

## 6. 配置应用

```bash
sudo install -m 640 -o root -g bookmark \
  /tmp/bookmark-vps.env.example /etc/bookmark/bookmark.env
openssl rand -hex 32
sudoedit /etc/bookmark/bookmark.env
```

把 `SESSION_SECRET` 替换为上一步生成的 64 字符随机值，最终配置如下：

```dotenv
APP_ENV=production
PYTHONDONTWRITEBYTECODE=1
PYTHONUNBUFFERED=1
DATABASE_URL=sqlite:////var/lib/bookmark/bookmarks.db
IMPORT_TMP_DIR=/var/lib/bookmark/import_tmp
SESSION_SECRET=<64字符随机值>
SESSION_COOKIE_SECURE=true
TIMEZONE=Asia/Shanghai
LOG_LEVEL=info
LOG_FORMAT=json
```

VPS 与 fnOS 必须使用不同的密钥和数据库。

## 7. 迁移、管理员和 systemd

先迁移数据库：

```bash
sudo -u bookmark sh -c '
  set -a
  . /etc/bookmark/bookmark.env
  set +a
  cd /opt/bookmark/source
  /opt/bookmark/venv/bin/python -m alembic upgrade head
'
```

创建管理员；密码在交互终端输入：

```bash
sudo -u bookmark sh -c '
  set -a
  . /etc/bookmark/bookmark.env
  set +a
  cd /opt/bookmark/source
  /opt/bookmark/venv/bin/python -m scripts.create_admin
'
```

安装服务：

```bash
sudo install -m 644 /tmp/bookmark-vps.service /etc/systemd/system/bookmark.service
sudo systemctl daemon-reload
sudo systemctl enable --now bookmark
sudo systemctl status bookmark --no-pager
```

服务只监听 `127.0.0.1:8000`，并由 systemd 限制为 `MemoryHigh=384M`、`MemoryMax=512M`。

## 8. 配置 Caddy HTTPS

编辑 `/etc/caddy/Caddyfile`：

```caddyfile
bookmarks-vps.example.com {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8000
}
```

替换为真实域名，然后验证并加载：

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
sudo systemctl status caddy --no-pager
```

应用 8000 只监听回环地址；公网只开放 80/443。

## 9. 验收内存和功能

```bash
curl -fsS https://<真实域名>/health/ready
systemctl show bookmark -p ActiveState -p NRestarts -p MemoryCurrent -p MemoryPeak
free -h
journalctl -u bookmark -n 100 --no-pager
journalctl -u caddy -n 100 --no-pager
```

验收要求：

- ready 返回 `{"status":"ok"}`，浏览器可登录并完成增删改查；
- 普通访问和一次最大规模导入均没有服务重启或 OOM；
- app 峰值低于 512 MB 限额的 70%（约 358 MB）；
- 长时间运行时 swap 不持续增长；
- `ss -lntp` 显示 8000 只绑定 `127.0.0.1`。

检查 OOM：

```bash
systemctl show bookmark -p Result -p NRestarts
journalctl -k --since "1 hour ago" | grep -i -E 'oom|out of memory' || true
```

超出内存门禁时不要下调 Argon2 密码安全参数；先清理同机服务，仍不足再升级内存。

## 10. 备份与计划任务

手动备份：

```bash
sudo -u bookmark sh -c '
  set -a
  . /etc/bookmark/bookmark.env
  set +a
  cd /opt/bookmark/source
  /opt/bookmark/venv/bin/python -m scripts.backup --type daily --dir /var/backups/bookmark
'
sudo ls -lh /var/backups/bookmark
```

在 `/etc/cron.d/bookmark-vps` 配置：

```cron
30 2 * * * root /usr/sbin/runuser -u bookmark -- /bin/sh -c 'set -a; . /etc/bookmark/bookmark.env; set +a; cd /opt/bookmark/source && /opt/bookmark/venv/bin/python -m scripts.backup --type daily --dir /var/backups/bookmark' >> /var/log/bookmark-backup.log 2>&1
35 2 * * 0 root /usr/sbin/runuser -u bookmark -- /bin/sh -c 'set -a; . /etc/bookmark/bookmark.env; set +a; cd /opt/bookmark/source && /opt/bookmark/venv/bin/python -m scripts.backup --type weekly --dir /var/backups/bookmark' >> /var/log/bookmark-backup.log 2>&1
40 2 1 * * root /usr/sbin/runuser -u bookmark -- /bin/sh -c 'set -a; . /etc/bookmark/bookmark.env; set +a; cd /opt/bookmark/source && /opt/bookmark/venv/bin/python -m scripts.backup --type monthly --dir /var/backups/bookmark' >> /var/log/bookmark-backup.log 2>&1
0 3 * * * root /usr/sbin/runuser -u bookmark -- /bin/sh -c 'set -a; . /etc/bookmark/bookmark.env; set +a; cd /opt/bookmark/source && /opt/bookmark/venv/bin/python -m scripts.cleanup_import_jobs' >> /var/log/bookmark-backup.log 2>&1
```

至少把加密备份复制到 VPS 之外。不要复制运行中的 SQLite 文件代替备份。

## 11. 升级与回退

固定顺序：备份 → 上传新发布包 → 安装依赖 → 停服务 → 迁移 → 启动 → ready。

```bash
# 先按第 10 节生成 weekly 备份，并记下备份文件名
sudo systemctl stop bookmark

# 保留当前代码；随后解包新版本到 /opt/bookmark/source
PREVIOUS_SOURCE="/opt/bookmark/source.previous.$(date +%Y%m%d%H%M%S)"
PREVIOUS_VENV=$(readlink -f /opt/bookmark/venv)
echo "旧代码目录：$PREVIOUS_SOURCE"
echo "旧虚拟环境：$PREVIOUS_VENV"
sudo mv /opt/bookmark/source "$PREVIOUS_SOURCE"
sudo tar -xf /opt/bookmark/bookmark-source-<新版本>.tar -C /opt/bookmark

sudo python3 -m venv /opt/bookmark/venv-<新版本>
sudo /opt/bookmark/venv-<新版本>/bin/python -m pip install -r /opt/bookmark/source/requirements.txt
sudo /opt/bookmark/venv-<新版本>/bin/python -m pip check
sudo ln -sfn /opt/bookmark/venv-<新版本> /opt/bookmark/venv
sudo chown -R root:root /opt/bookmark/source
sudo chmod -R a-w /opt/bookmark/source

sudo -u bookmark sh -c '
  set -a
  . /etc/bookmark/bookmark.env
  set +a
  cd /opt/bookmark/source
  /opt/bookmark/venv/bin/python -m alembic upgrade head
'
sudo systemctl start bookmark
curl -fsS https://<真实域名>/health/ready
```

迁移失败时移走新 `source`，把 `$PREVIOUS_SOURCE` 改回 `/opt/bookmark/source`，并把 `/opt/bookmark/venv` 链接恢复到 `$PREVIOUS_VENV`，再启动旧版本。迁移成功后若需降级，必须先按[运维手册 §9](运维手册.md#9-回滚上一版本)恢复升级前数据库备份。

## 12. 完成标准

- HTTPS、ready、核心功能和主机重启均通过；
- 最大规模导入没有 OOM，内存峰值符合门禁；
- 8000 未对公网开放；
- 手动备份生成数据库与 SHA-256 清单；
- 连续两次自动备份成功，且 VPS 外存在可恢复副本。

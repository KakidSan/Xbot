# Xbot

Xbot 2.0 起只支持 **Docker / Docker Compose** 运行。Telegram、Redis、MySQL 等连接信息通过 Compose `.env` 环境变量传入，程序启动时会由这些环境变量生成内部运行 Config；不再提供或映射外部 YAML 样例配置文件，也不再使用脚本直接安装、虚拟环境或 systemd 部署。

- 镜像地址：`ghcr.io/kakidsan/xbot`
- 版本检测：`https://api.github.com/repos/KakidSan/Xbot/tags`

## 快速开始

```bash
cp .env.example .env
# 编辑 .env，填写 Telegram / Redis / MySQL 参数
docker compose up -d
```

查看日志：

```bash
docker compose logs -f xbot
```

停止：

```bash
docker compose down
```

## 环境变量

`docker-compose.yml` 会把 `.env` 参数注入容器。默认只需要配置 Telegram、Redis、MySQL；这些值会写入程序内部运行 Config。

### Telegram

```env
TELEGRAM_BOT_TOKEN=123456:replace_me
TELEGRAM_ADMIN_USER_ID=123456789
TELEGRAM_MANAGER_USER_IDS=
TELEGRAM_AUTHORIZED_USER_IDS=
```

说明：

- `TELEGRAM_ADMIN_USER_ID` 是唯一超级管理员。
- `TELEGRAM_MANAGER_USER_IDS` 是普通管理员，多个 ID 用英文逗号分隔。
- `TELEGRAM_AUTHORIZED_USER_IDS` 是普通授权用户，多个 ID 用英文逗号分隔。

### Redis

```env
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_PASSWORD=
REDIS_DB=0
```

### MySQL

```env
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_DATABASE=xboard
MYSQL_USERNAME=xbot_readonly
MYSQL_PASSWORD=replace_me
```

建议使用只有 `SELECT` 权限的 MySQL 只读账号。

## 内置运行参数

以下参数不需要在 Compose 中配置：

- SQLite 缓存路径：容器内 `/app/data/xbot.sqlite3`，宿主机 `./data/xbot.sqlite3`
- 采集间隔：60 秒
- IP-API 限速：30 次/分钟
- 缓存保留时间：在 Bot 的「参数配置 → 缓存保留时间」中调整

## 数据目录

Compose 默认挂载：

- `./data:/app/data`：SQLite 缓存
- `./logs:/app/logs`：日志
- `./.install-state:/app/.install-state`：Bot 后台更新状态

这些目录不应提交到仓库。

## Bot 内参数配置

缓存保留时间在 Telegram Bot 的「参数配置 → 缓存保留时间」中调整，可选：

- 一月
- 一季
- 一年
- 一切

确认后会立即删除超出期限的本地老缓存记录。该操作只影响 Bot 本地 SQLite 缓存，不会修改 XBoard / MySQL / Redis。

## Bot 内后台更新

`docker-compose.yml` 已保留后台更新所需参数：

```yaml
UPDATE_MODE: docker-compose
COMPOSE_PROJECT_DIR: /compose
COMPOSE_FILES: docker-compose.yml
COMPOSE_SERVICE: xbot
GHCR_IMAGE: ghcr.io/kakidsan/xbot
```

如需允许 Telegram Bot 内点击「后台更新」后自动拉取 GHCR 镜像并重建容器，需要在 `docker-compose.yml` 中取消注释：

```yaml
# - ./:/compose
# - /var/run/docker.sock:/var/run/docker.sock
```

安全提醒：挂载 `/var/run/docker.sock` 后，容器拥有宿主机 Docker 控制权。只建议可信管理员环境开启。

## 升级

手动升级：

```bash
docker compose pull
docker compose up -d
```

本地构建测试：

```bash
docker compose build
docker compose up -d
```

## 不再提供的部署文件

从 2.0 开始，仓库只保留 Docker / Docker Compose 部署链路，不再提供：

- `install.sh`
- `run.sh`
- 外部 YAML 样例配置文件 / 配置文件映射
- 多个 Compose override 文件
- systemd 服务示例

## Heki / Soga 在线 IP 兼容

Xbot 2.0-Beta 的活跃 IP 采集会自动兼容 Heki、Soga 或二者混用的 Redis Key，并统一写入本地缓存，前台查询体验保持一致。

当前支持的 Redis Key：

```text
Heki: heki:ip:<user_id>:<ip>
Soga: soga_conn_<user_id>_<ip>
```

注意：Soga 2.13.x 实测不是 `soga:ip:<user_id>:<ip>`。`soga_conn_*` 属于 Soga 的连接/IP 限制缓存；如果用户没有有效设备/IP 限制，Soga 可能不会为该用户写入此类 Key。

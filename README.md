# Xbot

Xbot 自 2.0 起只支持 **Docker / Docker Compose** 运行。Telegram、Redis、MySQL 等连接信息通过 Compose `.env` 环境变量传入，程序启动时会由这些环境变量生成内部运行 Config；不再提供或映射外部 YAML 样例配置文件，也不再使用脚本直接安装、虚拟环境或 systemd 部署。

当前版本：**3.0 Beta**

3.0 Beta 延续 2.0 的 Docker-only 部署基础，重点重构 Bot handler、router、callback data、运行时服务边界与测试结构。

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

- `TELEGRAM_BOT_TOKEN`：Telegram Bot Token，必填。
- `TELEGRAM_ADMIN_USER_ID`：超级管理员 Telegram 用户 ID，必填；可填写一个或多个 ID，多个 ID 用英文逗号分隔，例如 `123456789,987654321`。超级管理员只能通过环境变量修改。
- `TELEGRAM_MANAGER_USER_IDS`：普通管理员 Telegram 用户 ID，可选；多个 ID 用英文逗号分隔。普通管理员可在 Bot 内由超级管理员管理。
- `TELEGRAM_AUTHORIZED_USER_IDS`：授权用户 Telegram 用户 ID，可选；多个 ID 用英文逗号分隔。授权用户也可在 Bot 内管理。

### Redis

```env
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_PASSWORD=
REDIS_DB=0
```

说明：

- `REDIS_HOST`：Redis 主机地址，必填。
- `REDIS_PORT`：Redis 端口，默认 `6379`。
- `REDIS_PASSWORD`：Redis 密码，没有密码时留空。
- `REDIS_DB`：Redis DB 编号，默认 `0`。

### MySQL

```env
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_DATABASE=xboard
MYSQL_USERNAME=xbot_readonly
MYSQL_PASSWORD=replace_me
```

说明：

- `MYSQL_HOST`：MySQL 主机地址，必填。
- `MYSQL_PORT`：MySQL 端口，默认 `3306`。
- `MYSQL_DATABASE`：XBoard 数据库名，必填。
- `MYSQL_USERNAME`：MySQL 用户名，必填。
- `MYSQL_PASSWORD`：MySQL 密码，必填。

建议使用只有 `SELECT` 权限的 MySQL 只读账号；Xbot 只需要读取 XBoard 用户、流量、节点等数据，不需要写入 MySQL。

## 部署参数总览

| 参数 | 必填 | 示例 | 说明 |
| --- | --- | --- | --- |
| `TZ` | 否 | `Asia/Shanghai` | 容器时区。 |
| `TELEGRAM_BOT_TOKEN` | 是 | `123456:replace_me` | Telegram Bot Token。 |
| `TELEGRAM_ADMIN_USER_ID` | 是 | `123456789` / `123,456` | 超级管理员 Telegram 用户 ID；多个 ID 用英文逗号分隔。 |
| `TELEGRAM_MANAGER_USER_IDS` | 否 | `123,456` | 普通管理员 Telegram 用户 ID；多个 ID 用英文逗号分隔。 |
| `TELEGRAM_AUTHORIZED_USER_IDS` | 否 | `123,456` | 授权用户 Telegram 用户 ID；多个 ID 用英文逗号分隔。 |
| `REDIS_HOST` | 是 | `127.0.0.1` | Redis 主机地址。 |
| `REDIS_PORT` | 否 | `6379` | Redis 端口。 |
| `REDIS_PASSWORD` | 否 | `replace_me` | Redis 密码；无密码留空。 |
| `REDIS_DB` | 否 | `0` | Redis DB 编号。 |
| `MYSQL_HOST` | 是 | `127.0.0.1` | MySQL 主机地址。 |
| `MYSQL_PORT` | 否 | `3306` | MySQL 端口。 |
| `MYSQL_DATABASE` | 是 | `xboard` | XBoard 数据库名。 |
| `MYSQL_USERNAME` | 是 | `xbot_readonly` | MySQL 用户名，建议只读账号。 |
| `MYSQL_PASSWORD` | 是 | `replace_me` | MySQL 密码。 |

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

## IP 监控与真实客户端 IP

Xbot 的 IP 监控、异地登录告警、IP 忽略列表等功能依赖 Redis 中采集到的客户端 IP。若 XBoard / Heki / Soga 位于负载均衡、反向代理、转发入口之后，请确保入口已正确传递真实客户端 IP，例如启用 Proxy Protocol 或等价的真实 IP 转发配置。

如果真实客户端 IP 没有正确传递，Xbot 仍会展示 Redis 中看到的 IP，但统计结果、归属地判断和异地登录告警准确性都会受影响。

Heki 相关配置可参考：<https://hekicore.github.io/heki-docs/#/other/forward-get-real-ip>

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

Xbot 2.0 的活跃 IP 采集会自动兼容 Heki、Soga 或二者混用的 Redis Key，并统一写入本地缓存，前台查询体验保持一致。

当前支持的 Redis Key：

```text
Heki: heki:ip:<user_id>:<ip>
Soga: soga_conn_<user_id>_<ip>
```

注意：Soga 2.13.x 实测不是 `soga:ip:<user_id>:<ip>`。`soga_conn_*` 属于 Soga 的连接/IP 限制缓存；如果用户没有有效设备/IP 限制，Soga 可能不会为该用户写入此类 Key。

## 代码模块结构

2.0 代码按职责拆分为 Python package：

```text
xbot/
  __main__.py          # Docker/CLI 入口：python -m xbot
  config.py            # AppConfig / dataclasses / 环境变量加载
  db/
    cache.py           # SQLite init + CRUD
    mysql.py           # XBoard 只读查询
    redis.py           # Heki/Soga 在线 IP 采集
  bot/
    application.py     # 启动、生命周期、后台任务
    router.py          # Telegram handler 注册
    context.py         # BotContext / BotRuntime
    callback_data.py   # callback pattern / 兼容映射
    permissions.py     # 权限判断
    message_utils.py   # 通用消息工具
    messaging.py       # 运行时消息服务构造
    keyboards.py       # InlineKeyboardMarkup 构造
    menus.py           # 主菜单/一级菜单键盘
    formatters.py      # 文本渲染
    handlers/
      commands.py
      main_menu.py
      traffic.py
      ip_monitor.py
      alerts.py
      parameters.py
      debug.py
      auth.py
      operation_logs.py
      text_input.py
      version.py
  collector.py         # 定时采集循环
  geo.py               # IP geo 查询 + 缓存
  node_monitor.py      # 官方订阅链接提取缓存/展示
  alerts.py            # 流量/IP 告警逻辑
  updater.py           # 版本检查 + Docker Compose 后台更新
```

`xbot.py` 仍保留为兼容包装入口；Docker 镜像默认使用 `python -m xbot`。

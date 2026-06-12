# Xbot

Xbot 是面向 XBoard 运维场景的 Telegram 监控、统计与告警 Bot。

它通过读取 XBoard 的 MySQL 数据，并结合 Redis 中由后端上报的在线 IP 信息，提供用户 IP 活跃统计、流量统计、异常提醒、日报/周报/月报、授权管理和运维工具。

> 重要前提：Xbot 依赖 Redis 在线 IP 上报数据。你的 XBoard 使用的后端必须是 **Soga** 或 **Heki**，并且后端需要正常向 Redis 写入兼容格式的数据。

## 功能概览

- Telegram 白名单访问控制。
- Redis / MySQL / SQLite 健康检查。
- Redis 在线 IP 采集，兼容 Soga / Heki 上报格式。
- 用户活跃 IP 与地区统计。
- 用户 / 节点流量统计。
- 日报、周报、月报推送。
- 用量异常告警。
- 异地登录告警。
- 单用户与全局告警阈值配置。
- 自定义时间范围统计。
- 操作日志查看。
- 调试工具：缓存重置、特定用户 IP 记录清理等。
- 配置文件热加载。
- Git Tag 版本识别与更新检查。
- Telegram 内二次确认后触发后台更新。

## 运行要求

- Linux 服务器。
- systemd。
- Python 3。
- Redis。
- XBoard MySQL 数据库。
- Telegram Bot Token。
- XBoard 使用的后端必须是 Soga 或 Heki。

建议为 Xbot 准备一个 **只读 MySQL 账号**。

Xbot 只需要查询 XBoard 数据。请不要给它不必要的写权限。

## 部署方式

Xbot 支持三种部署方案：

1. **直接部署 / systemd**：使用 `install.sh` 安装到服务器，创建 Python 虚拟环境和 `xbot.service`。
2. **Docker 部署 / docker run**：直接运行 GHCR 镜像，适合不使用 Compose 的用户。
3. **Docker Compose 部署**：推荐的 Docker 方式，配置、数据、日志更容易管理，也支持通过 Bot 触发容器升级。

三种方式都使用同一套持久化文件：

```text
config.yaml
data/xbot.sqlite3
logs/
.install-state/
```

只要保留这些文件/目录，就可以在 systemd、Docker、Docker Compose 之间切换。

## 方式一：直接部署 / systemd

```bash
git clone https://github.com/KakidSan/Xbot.git
cd xbot
chmod +x install.sh
./install.sh
```

安装脚本会自动完成：

1. 检查系统依赖。
2. 如缺少 Python / venv / pip / git / curl 等依赖，会询问是否自动安装。
3. 创建 `.venv` Python 虚拟环境。
4. 安装 `requirements.txt` 中的 Python 项目依赖。
5. 交互式生成 `config.yaml`。
6. 测试 MySQL、Redis、Telegram Bot Token。
7. 写入 `/etc/systemd/system/xbot.service`。
8. 启动服务并设置开机自启。

安装完成后，在 Telegram 找到你的 Bot，发送：

```text
/start
```

## 方式二：Docker 部署 / docker run

Xbot 镜像托管在 GitHub Container Registry：

```text
ghcr.io/kakidsan/xbot:latest
ghcr.io/kakidsan/xbot:vX.Y.Z
```

仓库发布后，镜像地址使用小写命名空间：`ghcr.io/kakidsan/xbot`。

### 1. 准备配置

Docker 部署可以复用安装器的配置生成向导，但不执行完整安装：

```bash
git clone https://github.com/KakidSan/Xbot.git
cd xbot
chmod +x install.sh
./install.sh
# 选择：仅生成 config.yaml（Docker 可用）
mkdir -p data logs .install-state
```

也可以手动复制模板：

```bash
cp config.docker.example.yaml config.yaml
nano config.yaml
mkdir -p data logs .install-state
```

### 2. 启动容器

```bash
docker run -d \
  --name xbot \
  --restart unless-stopped \
  -e TZ=Asia/Shanghai \
  -v "$PWD/config.yaml:/app/config.yaml:ro" \
  -v "$PWD/data:/app/data" \
  -v "$PWD/logs:/app/logs" \
  -v "$PWD/.install-state:/app/.install-state" \
  ghcr.io/kakidsan/xbot:latest
```

查看日志：

```bash
docker logs -f xbot
```

停止：

```bash
docker stop xbot
```

删除容器但保留配置和数据：

```bash
docker rm xbot
```

### 3. docker run 模式下升级

纯 `docker run` 模式建议通过 SSH 手动升级：

```bash
docker pull ghcr.io/kakidsan/xbot:latest
docker stop xbot
docker rm xbot
# 重新执行上面的 docker run 命令
```

如果你希望在 Telegram Bot 内点击“后台更新”后自动拉取镜像并重建容器，推荐使用下面的 Docker Compose 部署，并启用 `docker-compose.self-update.yml`。

## 方式三：Docker Compose 部署

Docker Compose 是推荐的 Docker 部署方式。

### 1. 准备配置

```bash
git clone https://github.com/KakidSan/Xbot.git
cd xbot
chmod +x install.sh
./install.sh
# 选择：仅生成 config.yaml（Docker 可用）
mkdir -p data logs .install-state
```

或者：

```bash
cp config.docker.example.yaml config.yaml
nano config.yaml
mkdir -p data logs .install-state
```

### 2. Compose 示例

项目已提供 `docker-compose.yml`：

```yaml
services:
  xbot:
    image: ghcr.io/kakidsan/xbot:latest
    # 如果暂时不用 GHCR，可以改成本地构建：
    # build: .
    container_name: xbot
    restart: unless-stopped
    working_dir: /app
    volumes:
      - ./config.yaml:/app/config.yaml:ro
      - ./data:/app/data
      - ./logs:/app/logs
      - ./.install-state:/app/.install-state
    environment:
      TZ: Asia/Shanghai
      XBOT_UPDATE_MODE: docker-compose
      XBOT_COMPOSE_PROJECT_DIR: /compose
      XBOT_COMPOSE_FILES: docker-compose.yml
      XBOT_COMPOSE_SERVICE: xbot
      # 可选：设置后 Bot 更新会优先拉取指定版本镜像
      # XBOT_GHCR_IMAGE: ghcr.io/kakidsan/xbot
```

启动：

```bash
docker compose up -d
```

查看日志：

```bash
docker compose logs -f
```

停止：

```bash
docker compose down
```

### 3. Compose 模式下通过 Bot 升级

如果希望通过 Telegram Bot 点击“后台更新”后自动执行：

```bash
docker compose pull
docker compose up -d
```

请使用额外的覆盖文件 `docker-compose.self-update.yml`：

```bash
docker compose -f docker-compose.yml -f docker-compose.self-update.yml up -d
```

`docker-compose.self-update.yml` 会额外挂载：

```yaml
services:
  xbot:
    volumes:
      - ./:/compose
      - /var/run/docker.sock:/var/run/docker.sock
    environment:
      XBOT_UPDATE_MODE: docker-compose
      XBOT_COMPOSE_PROJECT_DIR: /compose
      XBOT_COMPOSE_FILES: docker-compose.yml:docker-compose.self-update.yml
      XBOT_COMPOSE_SERVICE: xbot
```

安全提醒：挂载 `/var/run/docker.sock` 后，容器拥有宿主机 Docker 控制权。只建议可信环境开启。

### 4. 本地构建镜像

如果暂时不使用 GHCR，可以把 `docker-compose.yml` 里的：

```yaml
image: ghcr.io/kakidsan/xbot:latest
```

注释掉，并启用：

```yaml
build: .
```

然后：

```bash
docker compose up -d --build
```

## Docker 配置如何填写

Docker / Docker Compose 部署不需要运行 `install.sh` 的完整安装流程，但可以复用它的配置生成向导：

```bash
./install.sh
# 选择：仅生成 config.yaml（Docker 可用）
```

该模式只写入 `config.yaml`，不会安装系统依赖，不会创建 systemd 服务，也不会启动 Xbot。

也可以手动复制模板填写：

```bash
cp config.docker.example.yaml config.yaml
nano config.yaml
```

需要填写的内容与脚本安装时的交互输入一致：

- Telegram Bot Token
- Telegram 白名单用户 ID
- Redis host / port / password / db / ssl / prefix
- MySQL host / port / database / username / password
- 采集间隔、缓存保留天数等 app 配置

注意：容器内的 `127.0.0.1` 指的是容器自己，不是宿主机。

如果 MySQL / Redis 在宿主机，请填写容器可访问的宿主机 IP；如果它们也是 compose 服务，可以填写服务名。

为了便于 systemd、Docker、Docker Compose 相互切换，请保持：

```yaml
app:
  cache_path: "data/xbot.sqlite3"
```

不要写成宿主机绝对路径。

## systemd / Docker / Docker Compose 相互切换

Xbot 的持久化文件固定放在项目目录：

```text
xbot/
├── config.yaml
├── data/
│   └── xbot.sqlite3
├── logs/
└── .install-state/
```

三种部署方式都使用这些文件。

### systemd → Docker / Compose

```bash
cd /path/to/xbot

# 停止直接部署服务，避免 Telegram long polling 和 SQLite 写入冲突
sudo systemctl stop xbot

# 启动 Docker run 或 Compose
# Docker run：参考上面的 docker run 命令
# Compose：
docker compose up -d
```

### Docker / Compose → systemd

```bash
cd /path/to/xbot

# 停止 Docker 版
docker stop xbot 2>/dev/null || true
docker rm xbot 2>/dev/null || true
docker compose down 2>/dev/null || true

# 进入安装器，选择安装或升级/修复
./install.sh
```

安装器检测到已有 `config.yaml` 时会默认保留，不会覆盖。

### Docker run ↔ Docker Compose

只要都挂载同一个项目目录，就可以直接切换：

```bash
# docker run → compose
docker stop xbot
docker rm xbot
docker compose up -d

# compose → docker run
docker compose down
# 再执行 docker run 命令
```

### 切换前备份

建议切换前备份持久化文件：

```bash
tar -czf xbot-backup-$(date +%F-%H%M%S).tar.gz config.yaml data logs .install-state
```

恢复：

```bash
tar -xzf xbot-backup-YYYY-MM-DD-HHMMSS.tar.gz
```

## 项目结构

```text
xbot/
├── xbot.py                 # Bot 主程序
├── install.sh              # 安装 / 管理脚本
├── run.sh                  # 统一启动入口
├── scripts/
│   └── update.sh           # Bot 内后台自更新脚本
├── VERSION
├── README.md
├── requirements.txt
├── config.example.yaml
├── config.docker.example.yaml
├── Dockerfile
├── docker-compose.yml
├── systemd.service.example
├── config.yaml             # 安装后生成，不进入 Git
├── .venv/                  # 安装后生成，不进入 Git
├── .deps/                  # 可选离线依赖目录，不进入 Git
├── .install-state/         # 安装/更新状态，不进入 Git
├── data/
│   ├── .gitkeep
│   └── xbot.sqlite3        # 运行后生成，不进入 Git
└── logs/
    ├── .gitkeep
    └── xbot.log            # 运行后生成，不进入 Git
```

除了 systemd 服务文件外，Xbot 使用到的文件都位于当前项目目录。

systemd 服务文件路径：

```text
/etc/systemd/system/xbot.service
```

## 配置文件与数据保护

以下文件不会被 Git 管理，也不会被 `git pull` / `git checkout` 覆盖：

```text
config.yaml
config.yaml.bak.*
data/
logs/
.venv/
.deps/
.install-state/
```

Docker 镜像和 `.dockerignore` 也会排除这些运行期文件，避免把私密配置、数据库和日志打进镜像。

安装脚本遇到已有 `config.yaml` 时，会默认保留现有配置。

升级 / 修复安装不会删除或覆盖：

- `config.yaml`
- `data/xbot.sqlite3`
- `logs/xbot.log`

如果未来版本新增配置项，应由程序提供默认值，或通过安装脚本做配置补全/迁移；不应该直接覆盖用户现有配置文件。

## 配置说明

安装器会交互式生成 `config.yaml`。

模板文件：

```text
config.example.yaml
```

核心配置包括：

```yaml
telegram:
  bot_token: "123456:replace_me"
  allowed_user_ids:
    - 123456789

redis:
  host: "127.0.0.1"
  port: 6379
  password: null
  db: 0
  ssl: false
  prefix: ""

mysql:
  host: "127.0.0.1"
  port: 3306
  database: ""
  username: ""
  password: ""

app:
  cache_path: "data/xbot.sqlite3"
  collector_interval_seconds: 60
```

## 常用命令

查看服务状态：

```bash
sudo systemctl status xbot
```

查看 systemd 日志：

```bash
sudo journalctl -u xbot -f
```

查看项目日志：

```bash
tail -f logs/xbot.log
```

重启服务：

```bash
sudo systemctl restart xbot
```

停止服务：

```bash
sudo systemctl stop xbot
```

重新进入安装器：

```bash
./install.sh
```

## 升级

### systemd 部署升级

#### 方式一：SSH 手动升级 / 修复安装

在项目目录执行：

```bash
./install.sh
```

选择：

```text
升级 / 修复安装
```

该流程会尝试：

1. 停止 `xbot.service`。
2. 执行 `git pull --ff-only`。
3. 更新 `.venv` 中的 Python 项目依赖。
4. 重写 `run.sh`。
5. 重写 systemd 服务文件。
6. 重启 `xbot.service`。

不会覆盖 `config.yaml`、`data/`、`logs/`。

#### 方式二：Telegram 内后台更新

Xbot 支持在 Telegram 内二次确认后触发后台更新。

后台更新脚本位于：

```text
scripts/update.sh
```

它负责：

- 校验目标版本号。
- 拉取 Git Tag。
- 切换到目标版本。
- 执行 `pip install -r requirements.txt` 更新 Python 依赖。
- 通过 systemd 重启 `xbot.service`。
- 写入 `.install-state/update-status.json` 供 Bot 查询状态。

注意：

- 新版本如果新增 **Python 依赖**，只要依赖已经写入新版 `requirements.txt`，后台更新会安装。
- 新版本如果新增 **系统依赖**，后台更新不会自动安装系统包，也不会把系统包写入安装清单。请通过 SSH 执行 `./install.sh` 的升级 / 修复安装。
- 后台更新需要 Bot 运行用户拥有项目目录写权限。
- 自动重启服务可能需要受限 sudo / systemd-run 权限；如果权限不足，更新状态会显示失败原因。

### Docker 部署升级

使用 GHCR 镜像时：

```bash
cd /path/to/xbot
docker compose pull
docker compose up -d
```

使用本地构建时：

```bash
cd /path/to/xbot
git pull
docker compose up -d --build
```

Docker 部署不会使用 Telegram 内的 systemd 后台更新流程。

## 安装脚本测试模式

为了方便在不影响现有服务的情况下测试安装脚本，可使用环境变量指定临时服务名、临时 service 文件，并跳过启动：

```bash
XBOT_SERVICE_NAME=xbot-test \
XBOT_SERVICE_FILE=/tmp/xbot-test.service \
XBOT_SKIP_START=1 \
./install.sh
```

这种方式适合验证脚本生成结果，不会覆盖 `/etc/systemd/system/xbot.service`，也不会启动真实 Bot。

## 卸载

在项目目录执行：

```bash
./install.sh
```

选择：

```text
卸载 Xbot
```

卸载会执行：

1. 停止 `xbot.service`。
2. 禁用开机自启。
3. 删除 `/etc/systemd/system/xbot.service`。
4. 重新加载 systemd。
5. 询问是否删除当前项目目录。
6. 询问是否卸载部分由 Xbot 安装器新增的辅助系统依赖。

如果选择保留项目目录，以下文件也会保留：

```text
config.yaml
data/xbot.sqlite3
logs/
```

## 安全说明

- 请使用 Telegram 白名单限制可访问用户。
- 请使用只读 MySQL 账号运行 Xbot。
- 不要把 `config.yaml` 提交到 GitHub。
- 不要公开 `logs/`、`data/`、`.install-state/`。
- 不要把 `config.yaml`、`data/`、`logs/` 打进 Docker 镜像。项目已通过 `.dockerignore` 默认排除。
- Telegram 内后台更新属于敏感操作，建议只开放给可信管理员。

## Todo

- 继续完善操作日志的呈现形式，让不同类型操作的详情更统一、更易读。
- 增强配置迁移能力，在未来新增配置项时自动补全默认值。
- 完善安装脚本的非交互模式，方便自动化部署。
- 增加更完整的安装前/安装后诊断命令。
- 补充 GitHub Actions，自动发布 GHCR 镜像。

## License

待补充。

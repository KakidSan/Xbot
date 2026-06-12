#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${XBOT_SERVICE_NAME:-xbot}"
SERVICE_FILE="${XBOT_SERVICE_FILE:-/etc/systemd/system/${SERVICE_NAME}.service}"
INSTALL_STATE_DIR="${XBOT_INSTALL_STATE_DIR:-.install-state}"
INSTALL_STATE_FILE="${INSTALL_STATE_DIR}/system-packages.json"
PROJECT_DIR="${XBOT_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
RUN_USER="${XBOT_RUN_USER:-$(id -un)}"
XBOT_SKIP_START="${XBOT_SKIP_START:-0}"
XBOT_SKIP_SYSTEM_PACKAGES="${XBOT_SKIP_SYSTEM_PACKAGES:-0}"
XBOT_SKIP_VENV="${XBOT_SKIP_VENV:-0}"
XBOT_SKIP_CONNECTION_TEST="${XBOT_SKIP_CONNECTION_TEST:-0}"
SUDO=""
if [[ "$(id -u)" -ne 0 ]]; then
  SUDO="sudo"
fi

COLOR_RESET="\033[0m"
COLOR_GREEN="\033[32m"
COLOR_YELLOW="\033[33m"
COLOR_RED="\033[31m"
COLOR_BLUE="\033[34m"

info() { printf "${COLOR_BLUE}%s${COLOR_RESET}\n" "$*"; }
success() { printf "${COLOR_GREEN}%s${COLOR_RESET}\n" "$*"; }
warn() { printf "${COLOR_YELLOW}%s${COLOR_RESET}\n" "$*"; }
err() { printf "${COLOR_RED}%s${COLOR_RESET}\n" "$*"; }
plain() { printf "%s\n" "$*"; }

prompt() {
  local label="$1"
  local default="${2:-}"
  local value
  if [[ -n "$default" ]]; then
    read -r -p "${label} [${default}]: " value
    printf "%s" "${value:-$default}"
  else
    read -r -p "${label}: " value
    printf "%s" "$value"
  fi
}

prompt_secret() {
  local label="$1"
  local value
  read -r -s -p "${label}: " value
  printf "\n" >&2
  printf "%s" "$value"
}

confirm() {
  local label="$1"
  local default="${2:-Y}"
  local answer suffix
  if [[ "$default" =~ ^[Yy]$ ]]; then
    suffix="[Y/n]"
  else
    suffix="[y/N]"
  fi
  read -r -p "${label} ${suffix}: " answer
  answer="${answer:-$default}"
  [[ "$answer" =~ ^[Yy]$ ]]
}

require_sudo() {
  if [[ -n "$SUDO" ]]; then
    if ! command -v sudo >/dev/null 2>&1; then
      err "❌ 当前用户不是 root，且系统未安装 sudo。"
      err "请使用 root 运行，或先安装 sudo。"
      exit 1
    fi
    $SUDO -v
  fi
}

command_exists() { command -v "$1" >/dev/null 2>&1; }

is_systemd_available() {
  command_exists systemctl && [[ -d /run/systemd/system || -d /etc/systemd/system ]]
}

detect_pkg_manager() {
  if command_exists apt-get; then echo "apt"; return; fi
  if command_exists dnf; then echo "dnf"; return; fi
  if command_exists yum; then echo "yum"; return; fi
  if command_exists pacman; then echo "pacman"; return; fi
  echo "unknown"
}

package_installed() {
  local manager="$1" pkg="$2"
  case "$manager" in
    apt) dpkg -s "$pkg" >/dev/null 2>&1 ;;
    dnf|yum) rpm -q "$pkg" >/dev/null 2>&1 ;;
    pacman) pacman -Qi "$pkg" >/dev/null 2>&1 ;;
    *) return 1 ;;
  esac
}

install_packages() {
  local manager="$1"; shift
  local packages=("$@")
  [[ ${#packages[@]} -eq 0 ]] && return 0
  require_sudo
  case "$manager" in
    apt)
      $SUDO apt-get update
      $SUDO apt-get install -y "${packages[@]}"
      ;;
    dnf)
      $SUDO dnf install -y "${packages[@]}"
      ;;
    yum)
      $SUDO yum install -y "${packages[@]}"
      ;;
    pacman)
      $SUDO pacman -Sy --needed --noconfirm "${packages[@]}"
      ;;
    *)
      err "❌ 暂不支持自动安装依赖：未知包管理器。"
      exit 1
      ;;
  esac
}

system_packages_for_manager() {
  local manager="$1"
  case "$manager" in
    apt) echo "python3 python3-venv python3-pip git curl ca-certificates" ;;
    dnf|yum) echo "python3 python3-pip git curl ca-certificates" ;;
    pacman) echo "python python-pip git curl ca-certificates" ;;
    *) echo "" ;;
  esac
}

check_python_venv_ready() {
  command_exists python3 && python3 - <<'PY' >/dev/null 2>&1
import ensurepip, venv
PY
}

record_install_state() {
  local manager="$1" installed="$2" present="$3"
  mkdir -p "$INSTALL_STATE_DIR"
  python3 - "$manager" "$installed" "$present" "$PROJECT_DIR" "$RUN_USER" > "$INSTALL_STATE_FILE" <<'PY'
import json, sys
from datetime import datetime, timezone
manager, installed, present, project_dir, run_user = sys.argv[1:]
state = {
    "version": 1,
    "installed_at": datetime.now(timezone.utc).isoformat(),
    "project_dir": project_dir,
    "run_user": run_user,
    "service_name": "xbot.service",
    "package_manager": manager,
    "system_packages_installed_by_xbot": [x for x in installed.split(',') if x],
    "system_packages_already_present": [x for x in present.split(',') if x],
    "python_venv": ".venv"
}
json.dump(state, sys.stdout, ensure_ascii=False, indent=2)
print()
PY
}

load_installed_packages_from_state() {
  [[ -f "$INSTALL_STATE_FILE" ]] || return 0
  python3 - "$INSTALL_STATE_FILE" <<'PY'
import json, sys
try:
    data=json.load(open(sys.argv[1], encoding='utf-8'))
    print(' '.join(data.get('system_packages_installed_by_xbot') or []))
except Exception:
    pass
PY
}

ensure_system_dependencies() {
  info "[1/7] 正在检查系统依赖..."
  if [[ "$XBOT_SKIP_SYSTEM_PACKAGES" == "1" ]]; then
    warn "⚠️ 已跳过系统依赖检查。"
    record_install_state "skipped" "" ""
    return 0
  fi
  if ! is_systemd_available; then
    err "❌ 当前系统未检测到 systemd，暂不支持自动安装为后台服务。"
    exit 1
  fi

  local manager required_packages missing present installed_now
  manager="$(detect_pkg_manager)"
  if [[ "$manager" == "unknown" ]]; then
    err "❌ 未识别系统包管理器，无法自动安装系统依赖。"
    err "请手动安装 python3、python3-venv/python3-pip、git、curl 后重试。"
    exit 1
  fi

  read -r -a required_packages <<< "$(system_packages_for_manager "$manager")"
  missing=()
  present=()
  for pkg in "${required_packages[@]}"; do
    if package_installed "$manager" "$pkg"; then
      success "✅ ${pkg}：已安装"
      present+=("$pkg")
    else
      warn "❌ ${pkg}：未安装"
      missing+=("$pkg")
    fi
  done

  if [[ ${#missing[@]} -gt 0 ]]; then
    plain ""
    warn "检测到缺少以下系统依赖："
    printf ' - %s\n' "${missing[@]}"
    plain ""
    if [[ "$XBOT_SKIP_SYSTEM_PACKAGES" == "1" ]]; then
      err "❌ 当前为跳过系统依赖安装模式，无法继续完整安装。"
      exit 1
    elif confirm "是否现在自动安装缺失依赖？" "Y"; then
      plain ""
      info "安装程序将使用 ${manager} 安装缺失依赖。"
      install_packages "$manager" "${missing[@]}"
      installed_now=("${missing[@]}")
      success "✅ 系统依赖安装完成。"
    else
      err "❌ 缺少必要依赖，安装已取消。"
      exit 1
    fi
  else
    installed_now=()
  fi

  if ! check_python_venv_ready; then
    warn "⚠️ Python venv/ensurepip 仍不可用。"
    warn "如果后续创建虚拟环境失败，请手动安装对应系统的 Python venv/pip 包。"
  fi

  record_install_state "$manager" "$(IFS=,; echo "${installed_now[*]:-}")" "$(IFS=,; echo "${present[*]:-}")"
}

create_venv() {
  info "[2/7] 正在创建 Python 虚拟环境..."
  if [[ "$XBOT_SKIP_VENV" == "1" ]]; then
    warn "⚠️ 已跳过 Python 虚拟环境与依赖安装。"
    return 0
  fi
  cd "$PROJECT_DIR"
  if [[ ! -d .venv ]]; then
    python3 -m venv .venv
  fi
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
  success "✅ Python 虚拟环境与项目依赖已准备完成。"
}

write_run_script() {
  cat > "${PROJECT_DIR}/run.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ -d "${PWD}/.deps" ]]; then
  export PYTHONPATH="${PWD}/.deps${PYTHONPATH:+:${PYTHONPATH}}"
fi
if [[ -x "${PWD}/bin/xbot" ]]; then
  exec -a xbot "${PWD}/bin/xbot" --config "${PWD}/config.yaml"
fi
if [[ -x "${PWD}/.venv/bin/python" ]]; then
  exec -a xbot "${PWD}/.venv/bin/python" "${PWD}/xbot.py" --config "${PWD}/config.yaml"
fi
exec -a xbot python3 "${PWD}/xbot.py" --config "${PWD}/config.yaml"
EOF
  chmod +x "${PROJECT_DIR}/run.sh"
}

python_yaml_quote() {
  python3 - "$1" <<'PY'
import json, sys
print(json.dumps(sys.argv[1], ensure_ascii=False))
PY
}

python_cmd() {
  if [[ -x "${PROJECT_DIR}/.venv/bin/python" ]]; then
    printf '%s\n' "${PROJECT_DIR}/.venv/bin/python"
  else
    printf '%s\n' "python3"
  fi
}

write_config() {
  info "[3/7] 正在配置 Xbot..."
  cd "$PROJECT_DIR"
  if [[ -f config.yaml ]]; then
    warn "检测到已有 config.yaml。"
    plain "1) 保留现有配置"
    plain "2) 重新生成配置，并备份旧文件"
    plain "3) 退出"
    local choice
    read -r -p "请输入选项 [1-3]: " choice
    case "${choice:-1}" in
      1) success "✅ 已保留现有 config.yaml。"; return 0 ;;
      2)
        local bak="config.yaml.bak.$(date +%Y%m%d-%H%M%S)"
        cp config.yaml "$bak"
        success "✅ 已备份旧配置：${bak}"
        ;;
      3) exit 0 ;;
      *) err "❌ 无效选项。"; exit 1 ;;
    esac
  fi

  local mysql_host mysql_port mysql_db mysql_user mysql_password
  local redis_host redis_port redis_db redis_password redis_ssl redis_prefix
  local bot_token allow_ids collect_interval dashboard_interval retention geo_limit

  plain ""
  plain "请输入 XBoard MySQL 连接信息。建议使用只有 SELECT 权限的只读账号。"
  mysql_host="$(prompt "MySQL Host" "127.0.0.1")"
  mysql_port="$(prompt "MySQL Port" "3306")"
  mysql_db="$(prompt "MySQL Database")"
  mysql_user="$(prompt "MySQL Username")"
  mysql_password="$(prompt_secret "MySQL Password")"

  plain ""
  plain "请输入 Redis 连接信息。"
  redis_host="$(prompt "Redis Host" "127.0.0.1")"
  redis_port="$(prompt "Redis Port" "6379")"
  redis_db="$(prompt "Redis DB" "0")"
  redis_password="$(prompt_secret "Redis Password（留空表示无密码）")"
  if confirm "是否启用 Redis SSL？" "N"; then redis_ssl="true"; else redis_ssl="false"; fi
  redis_prefix="$(prompt "Redis Key Prefix（没有则留空）" "")"

  plain ""
  plain "请输入 Telegram Bot 配置。"
  bot_token="$(prompt_secret "Telegram Bot Token")"
  allow_ids="$(prompt "白名单用户 ID，多个用英文逗号分隔")"

  plain ""
  if confirm "采集参数是否使用默认值？" "Y"; then
    collect_interval="60"
    dashboard_interval="60"
    retention="7"
    geo_limit="30"
  else
    collect_interval="$(prompt "缓存采集间隔秒数" "60")"
    dashboard_interval="$(prompt "流量面板刷新间隔秒数" "60")"
    retention="$(prompt "缓存保留天数" "7")"
    geo_limit="$(prompt "IP 归属地每分钟查询上限" "30")"
  fi

  "$(python_cmd)" - "$mysql_host" "$mysql_port" "$mysql_db" "$mysql_user" "$mysql_password" \
    "$redis_host" "$redis_port" "$redis_db" "$redis_password" "$redis_ssl" "$redis_prefix" \
    "$bot_token" "$allow_ids" "$collect_interval" "$dashboard_interval" "$retention" "$geo_limit" > config.yaml <<'PY'
import json, sys
(
 mysql_host, mysql_port, mysql_db, mysql_user, mysql_password,
 redis_host, redis_port, redis_db, redis_password, redis_ssl, redis_prefix,
 bot_token, allow_ids, collect_interval, dashboard_interval, retention, geo_limit,
) = sys.argv[1:]
ids=[]
for item in allow_ids.replace('，', ',').split(','):
    item=item.strip()
    if item:
        if not item.isdigit():
            raise SystemExit(f'白名单用户 ID 必须是数字：{item}')
        ids.append(int(item))

def q(value):
    return json.dumps(value, ensure_ascii=False)

def yaml_scalar_or_null(value):
    return "null" if value in (None, "") else q(value)

print('# Xbot 配置文件')
print('# 修改本文件后，服务会自动检测并重新加载生效。')
print('')
print('telegram:')
print(f'  bot_token: {q(bot_token)}')
print('  allowed_user_ids:')
if ids:
    for uid in ids:
        print(f'    - {uid}')
else:
    print('    []')
print('')
print('redis:')
print(f'  host: {q(redis_host)}')
print(f'  port: {int(redis_port)}')
print(f'  password: {yaml_scalar_or_null(redis_password)}')
print(f'  db: {int(redis_db)}')
print(f'  ssl: {"true" if redis_ssl.lower() == "true" else "false"}')
print(f'  prefix: {q(redis_prefix)}')
print('')
print('mysql:')
print(f'  host: {q(mysql_host)}')
print(f'  port: {int(mysql_port)}')
print(f'  database: {q(mysql_db)}')
print(f'  username: {q(mysql_user)}')
print(f'  password: {q(mysql_password)}')
print('')
print('app:')
print('  config_watch_interval_seconds: 2')
print('  cache_path: "data/xbot.sqlite3"')
print(f'  collector_interval_seconds: {int(collect_interval)}')
print(f'  traffic_dashboard_refresh_seconds: {int(dashboard_interval)}')
print(f'  cache_retention_days: {int(retention)}')
print(f'  ip_geo_queries_per_minute: {int(geo_limit)}')
PY
  chmod 600 config.yaml
  mkdir -p data logs
  success "✅ config.yaml 已生成，并已设置权限 600。"
}

test_connections() {
  info "[4/7] 正在测试配置..."
  if [[ "$XBOT_SKIP_CONNECTION_TEST" == "1" ]]; then
    warn "⚠️ 已跳过连接测试。"
    return 0
  fi
  cd "$PROJECT_DIR"
  local rc
  if .venv/bin/python - <<'PY'
import asyncio, pathlib, sys
import pymysql, redis, yaml
import urllib.request, json
cfg=yaml.safe_load(open('config.yaml', encoding='utf-8'))
errors=[]
try:
    m=cfg['mysql']
    conn=pymysql.connect(host=m['host'], port=int(m['port']), user=m['username'], password=m['password'], database=m['database'], connect_timeout=5, read_timeout=5, write_timeout=5, charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)
    with conn.cursor() as cur:
        cur.execute('SELECT 1 AS ok')
        cur.fetchone()
    conn.close()
    print('✅ MySQL：连接成功，只读查询通过')
except Exception as exc:
    errors.append(f'MySQL：{type(exc).__name__}: {exc}')
try:
    r=cfg['redis']
    client=redis.Redis(host=r['host'], port=int(r['port']), password=r.get('password'), db=int(r.get('db') or 0), ssl=bool(r.get('ssl')), socket_connect_timeout=5, socket_timeout=5, decode_responses=True)
    client.ping()
    client.close()
    print('✅ Redis：PING 成功')
except Exception as exc:
    errors.append(f'Redis：{type(exc).__name__}: {exc}')
try:
    token=cfg['telegram']['bot_token']
    with urllib.request.urlopen(f'https://api.telegram.org/bot{token}/getMe', timeout=10) as resp:
        data=json.load(resp)
    if not data.get('ok'):
        raise RuntimeError(data)
    print(f"✅ Telegram：Bot Token 可用（@{data['result'].get('username','unknown')}）")
except Exception as exc:
    errors.append(f'Telegram：{type(exc).__name__}: {exc}')
if errors:
    print('\n⚠️ 以下配置测试未通过：')
    for e in errors:
        print(' -', e)
    print('\n你可以继续安装，但建议先修正 config.yaml。')
    sys.exit(2)
PY
  then
    rc=0
  else
    rc=$?
  fi
  if [[ $rc -eq 2 ]]; then
    if ! confirm "是否忽略测试失败并继续安装 systemd 服务？" "N"; then
      exit 1
    fi
  fi
}

write_systemd() {
  info "[5/7] 正在写入 systemd 服务..."
  if [[ "$SERVICE_FILE" == /etc/systemd/system/* ]]; then
    require_sudo
  fi
  write_run_script
  mkdir -p "$PROJECT_DIR/logs" "$PROJECT_DIR/data"
  local tmp
  tmp="$(mktemp)"
  cat > "$tmp" <<EOF
# Managed by Xbot installer
# ProjectDir=${PROJECT_DIR}
[Unit]
Description=Xbot Telegram Monitor Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${PROJECT_DIR}
ExecStart=${PROJECT_DIR}/run.sh
Restart=always
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=30
User=${RUN_USER}
Environment=PYTHONUNBUFFERED=1
StandardOutput=append:${PROJECT_DIR}/logs/xbot.log
StandardError=append:${PROJECT_DIR}/logs/xbot.log

[Install]
WantedBy=multi-user.target
EOF
  if [[ "$SERVICE_FILE" == /etc/systemd/system/* ]]; then
    $SUDO install -m 0644 "$tmp" "$SERVICE_FILE"
    rm -f "$tmp"
    $SUDO systemctl daemon-reload
  else
    install -m 0644 "$tmp" "$SERVICE_FILE"
    rm -f "$tmp"
    warn "⚠️ systemd 服务文件已写入测试路径，未执行 daemon-reload：${SERVICE_FILE}"
  fi
  success "✅ systemd 服务文件已写入：${SERVICE_FILE}"
}

start_service() {
  info "[6/7] 正在启动服务..."
  if [[ "$XBOT_SKIP_START" == "1" ]]; then
    warn "⚠️ 已跳过启动服务：${SERVICE_NAME}"
    return 0
  fi
  require_sudo
  $SUDO systemctl enable --now "$SERVICE_NAME"
  sleep 2
  if $SUDO systemctl is-active --quiet "$SERVICE_NAME"; then
    success "✅ ${SERVICE_NAME}.service 已启动，并已设置开机自启。"
  else
    err "❌ xbot.service 启动失败。"
    $SUDO systemctl status "$SERVICE_NAME" --no-pager || true
    exit 1
  fi
}

show_done() {
  info "[7/7] 安装完成"
  plain ""
  success "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  success "✅ Xbot 安装完成"
  success "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  plain ""
  plain "项目目录：${PROJECT_DIR}"
  plain "配置文件：${PROJECT_DIR}/config.yaml"
  plain "数据文件：${PROJECT_DIR}/data/xbot.sqlite3"
  plain "日志文件：${PROJECT_DIR}/logs/xbot.log"
  plain "systemd：${SERVICE_FILE}"
  plain ""
  plain "常用命令："
  plain "  sudo systemctl status ${SERVICE_NAME}"
  plain "  sudo journalctl -u ${SERVICE_NAME} -f"
  plain "  tail -f ${PROJECT_DIR}/logs/xbot.log"
  plain "  sudo systemctl restart ${SERVICE_NAME}"
  plain ""
  plain "下一步：打开 Telegram，找到你的 Bot，发送 /start。"
}

service_exists() { [[ -f "$SERVICE_FILE" ]]; }
process_exists() {
  pgrep -af -- "--config ${PROJECT_DIR}/config.yaml" 2>/dev/null | grep -E '(^| )xbot( |$)|xbot.py' >/dev/null 2>&1
}

show_status() {
  plain "服务文件：$(service_exists && echo 已存在 || echo 不存在)"
  if command_exists systemctl && service_exists; then
    plain "运行状态：$(systemctl is-active "$SERVICE_NAME" 2>/dev/null || true)"
    plain "开机自启：$(systemctl is-enabled "$SERVICE_NAME" 2>/dev/null || true)"
  fi
  plain "进程："
  pgrep -af -- "--config ${PROJECT_DIR}/config.yaml" 2>/dev/null | grep -E '(^| )xbot( |$)|xbot.py' || plain "  未检测到 xbot 进程"
}

upgrade_or_repair() {
  warn "升级 / 修复安装将会停止服务、尝试 git pull、更新依赖、重写 systemd 并重启。"
  plain "不会删除 config.yaml、data/、logs/。"
  if ! confirm "是否继续？" "Y"; then return; fi
  require_sudo
  if [[ "$XBOT_SKIP_START" != "1" ]]; then
    $SUDO systemctl stop "$SERVICE_NAME" 2>/dev/null || true
  fi
  cd "$PROJECT_DIR"
  if [[ -d .git ]]; then
    if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
      warn "⚠️ 检测到已跟踪文件存在本地修改，默认跳过 git pull。"
      if confirm "是否仍然执行 git pull？" "N"; then git pull --ff-only; fi
    else
      git pull --ff-only || warn "⚠️ git pull 未成功，请检查网络或仓库状态。"
    fi
  else
    warn "当前目录不是 Git 仓库，跳过 git pull。"
  fi
  create_venv
  write_systemd
  start_service
  success "✅ 升级 / 修复完成。"
}

restart_service() {
  require_sudo
  $SUDO systemctl restart "$SERVICE_NAME"
  sleep 2
  show_status
}

safe_project_dir() {
  local dir="$PROJECT_DIR"
  [[ -n "$dir" ]] || return 1
  case "$dir" in
    /|/root|/home|/opt|/usr|/etc|/tmp) return 1 ;;
  esac
  [[ -f "$dir/install.sh" && -f "$dir/xbot.py" && -f "$dir/VERSION" ]]
}

remove_system_packages_prompt() {
  [[ -f "$INSTALL_STATE_FILE" ]] || return 0
  local pkgs removable=() manager
  manager="$(python3 - "$INSTALL_STATE_FILE" <<'PY'
import json,sys
try: print(json.load(open(sys.argv[1])).get('package_manager',''))
except Exception: print('')
PY
)"
  read -r -a pkgs <<< "$(load_installed_packages_from_state)"
  [[ ${#pkgs[@]} -eq 0 ]] && return 0
  plain ""
  warn "检测到 Xbot 安装器曾新增以下系统依赖："
  printf ' - %s\n' "${pkgs[@]}"
  plain ""
  warn "为避免影响系统，安装程序不会自动卸载 python3、git、curl、ca-certificates 等基础组件。"
  for pkg in "${pkgs[@]}"; do
    case "$pkg" in
      python3-venv|python3-pip|python-pip) removable+=("$pkg") ;;
    esac
  done
  [[ ${#removable[@]} -eq 0 ]] && return 0
  warn "可选卸载的辅助依赖："
  printf ' - %s\n' "${removable[@]}"
  warn "卸载它们可能影响其他 Python 程序。"
  if confirm "是否卸载这些辅助依赖？" "N"; then
    require_sudo
    case "$manager" in
      apt) $SUDO apt-get remove -y "${removable[@]}" ;;
      dnf) $SUDO dnf remove -y "${removable[@]}" ;;
      yum) $SUDO yum remove -y "${removable[@]}" ;;
      pacman) $SUDO pacman -Rns --noconfirm "${removable[@]}" ;;
      *) warn "未知包管理器，跳过系统依赖卸载。" ;;
    esac
  fi
}

uninstall_xbot() {
  warn "⚠️ 即将卸载 Xbot。"
  plain "将停止服务、禁用开机自启、删除 systemd 文件，并可选删除当前项目目录。"
  if ! confirm "继续卸载？" "N"; then return; fi
  require_sudo
  $SUDO systemctl stop "$SERVICE_NAME" 2>/dev/null || true
  $SUDO systemctl disable "$SERVICE_NAME" 2>/dev/null || true
  $SUDO rm -f "$SERVICE_FILE"
  $SUDO systemctl daemon-reload
  success "✅ systemd 服务已删除。"

  remove_system_packages_prompt

  plain ""
  if confirm "是否同时删除当前项目目录？" "Y"; then
    if ! safe_project_dir; then
      err "❌ 当前目录未通过安全检查，拒绝自动删除：${PROJECT_DIR}"
      err "如需删除，请手动检查后执行。"
      exit 1
    fi
    warn "⚠️ 即将删除当前项目目录：${PROJECT_DIR}"
    read -r -p "请再次按 Enter 确认删除，或按 Ctrl+C 取消。"
    local parent base tmp
    parent="$(dirname "$PROJECT_DIR")"
    base="$(basename "$PROJECT_DIR")"
    tmp="$(mktemp /tmp/xbot-uninstall.XXXXXX.sh)"
    cat > "$tmp" <<EOF
#!/usr/bin/env bash
set -euo pipefail
cd "${parent}"
rm -rf -- "${base}"
echo "✅ Xbot 已卸载，项目目录已删除：${PROJECT_DIR}"
rm -f -- "\$0"
EOF
    chmod +x "$tmp"
    exec "$tmp"
  else
    plain "已保留当前项目目录。"
    plain "配置文件：${PROJECT_DIR}/config.yaml"
    plain "数据文件：${PROJECT_DIR}/data/xbot.sqlite3"
    plain "日志目录：${PROJECT_DIR}/logs/"
    plain "如需彻底删除，可日后手动删除：${PROJECT_DIR}"
  fi
}

generate_config_only() {
  plain "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  plain "Xbot 配置生成向导"
  plain "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  plain "该模式只生成或更新 config.yaml，适合 Docker 部署前使用。"
  plain "不会安装依赖，不会写入 systemd，也不会启动服务。"
  plain ""
  write_config
  plain ""
  success "✅ 配置已准备好。Docker 部署可继续执行：docker compose up -d"
}

install_xbot() {
  plain "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  plain "Xbot v$(cat "$PROJECT_DIR/VERSION" 2>/dev/null || echo 1.0.0) 安装向导"
  plain "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  plain "项目目录：${PROJECT_DIR}"
  plain "运行用户：${RUN_USER}"
  plain "systemd：${SERVICE_FILE}"
  plain ""
  if ! confirm "继续安装？" "Y"; then exit 0; fi
  ensure_system_dependencies
  create_venv
  write_config
  test_connections
  write_systemd
  start_service
  show_done
}

main_menu() {
  cd "$PROJECT_DIR"
  local installed="no"
  if service_exists || process_exists; then installed="yes"; fi
  plain "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  plain "Xbot v$(cat VERSION 2>/dev/null || echo 1.0.0)"
  plain "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  plain "项目目录：${PROJECT_DIR}"
  plain "运行用户：${RUN_USER}"
  plain ""
  if [[ "$installed" == "yes" ]]; then
    plain "当前状态：已安装或检测到运行进程"
    show_status
    plain ""
    plain "请选择操作："
    plain "1) 升级 / 修复安装"
    plain "2) 重启服务"
    plain "3) 查看服务状态"
    plain "4) 仅生成 / 更新 config.yaml（Docker 可用）"
    plain "5) 卸载 Xbot"
    plain "6) 退出"
    local choice
    read -r -p "请输入选项 [1-6]: " choice
    case "${choice:-6}" in
      1) upgrade_or_repair ;;
      2) restart_service ;;
      3) show_status ;;
      4) generate_config_only ;;
      5) uninstall_xbot ;;
      6) exit 0 ;;
      *) err "❌ 无效选项。"; exit 1 ;;
    esac
  else
    plain "当前状态：未安装"
    plain ""
    plain "请选择操作："
    plain "1) 安装 Xbot"
    plain "2) 仅生成 config.yaml（Docker 可用）"
    plain "3) 退出"
    local choice
    read -r -p "请输入选项 [1-3]: " choice
    case "${choice:-1}" in
      1) install_xbot ;;
      2) generate_config_only ;;
      3) exit 0 ;;
      *) err "❌ 无效选项。"; exit 1 ;;
    esac
  fi
}

main_menu "$@"

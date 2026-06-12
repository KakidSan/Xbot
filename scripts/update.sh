#!/usr/bin/env bash
set -euo pipefail

TARGET_VERSION="${1:-}"
CHAT_ID="${2:-}"
PROJECT_DIR="${XBOT_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
STATE_DIR="${PROJECT_DIR}/.install-state"
STATE_FILE="${STATE_DIR}/update-status.json"
SERVICE_NAME="${XBOT_SERVICE_NAME:-xbot}"
UPDATE_MODE="${XBOT_UPDATE_MODE:-auto}"
COMPOSE_PROJECT_DIR="${XBOT_COMPOSE_PROJECT_DIR:-${PROJECT_DIR}}"
COMPOSE_FILES="${XBOT_COMPOSE_FILES:-${XBOT_COMPOSE_FILE:-docker-compose.yml}}"
COMPOSE_SERVICE="${XBOT_COMPOSE_SERVICE:-xbot}"
GHCR_IMAGE="${XBOT_GHCR_IMAGE:-}"
SUDO=""
if [[ "$(id -u)" -ne 0 ]]; then
  SUDO="sudo"
fi

json_escape() {
  python3 - "$1" <<'PY'
import json, sys
print(json.dumps(sys.argv[1], ensure_ascii=False))
PY
}

write_state() {
  local status="$1"
  local stage="$2"
  local message="$3"
  local now
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  mkdir -p "$STATE_DIR"
  cat > "$STATE_FILE" <<EOF
{
  "status": $(json_escape "$status"),
  "stage": $(json_escape "$stage"),
  "message": $(json_escape "$message"),
  "target_version": $(json_escape "$TARGET_VERSION"),
  "chat_id": $(json_escape "$CHAT_ID"),
  "mode": $(json_escape "$UPDATE_MODE"),
  "updated_at": $(json_escape "$now")
}
EOF
}

fail() {
  local stage="$1"
  local message="$2"
  write_state "failed" "$stage" "$message"
  exit 1
}

validate_target_version() {
  if [[ -z "$TARGET_VERSION" || ! "$TARGET_VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.-]+)?$ ]]; then
    fail "validate" "目标版本无效：${TARGET_VERSION}"
  fi
}

has_docker_socket() {
  [[ -S /var/run/docker.sock ]] || [[ -n "${DOCKER_HOST:-}" ]]
}

has_docker_compose() {
  command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1
}

resolve_mode() {
  if [[ "$UPDATE_MODE" != "auto" ]]; then
    return 0
  fi
  if has_docker_socket && has_docker_compose; then
    UPDATE_MODE="docker-compose"
  elif command -v systemctl >/dev/null 2>&1; then
    UPDATE_MODE="systemd"
  else
    UPDATE_MODE="git-only"
  fi
}

ensure_git_repo_clean() {
  cd "$PROJECT_DIR"
  if [[ ! -d .git ]]; then
    fail "git" "当前目录不是 Git 仓库，无法自动更新。"
  fi
  if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    fail "git_status" "检测到本地源码有修改，已拒绝自动更新。请先通过 SSH 处理本地修改。"
  fi
}

checkout_target_version() {
  cd "$PROJECT_DIR"
  write_state "running" "fetch" "正在拉取远程版本标签。"
  git fetch --tags origin || fail "fetch" "git fetch --tags origin 失败。"

  if ! git rev-parse -q --verify "refs/tags/${TARGET_VERSION}" >/dev/null; then
    fail "tag" "目标版本标签不存在：${TARGET_VERSION}"
  fi

  write_state "running" "checkout" "正在切换到 ${TARGET_VERSION}。"
  git checkout -q "$TARGET_VERSION" || fail "checkout" "切换到 ${TARGET_VERSION} 失败。"
}

update_python_dependencies() {
  cd "$PROJECT_DIR"
  write_state "running" "dependencies" "正在更新 Python 依赖。"
  if [[ ! -x .venv/bin/python ]]; then
    python3 -m venv .venv || fail "venv" "创建 Python 虚拟环境失败。"
  fi
  .venv/bin/python -m pip install --upgrade pip || fail "pip" "升级 pip 失败。"
  .venv/bin/pip install -r requirements.txt || fail "requirements" "安装 Python 依赖失败。"
  if [[ -x ./run.sh ]]; then
    chmod +x ./run.sh
  fi
}

restart_systemd() {
  write_state "restarting" "restart" "更新已写入，正在重启 ${SERVICE_NAME}.service。"
  if ! command -v systemctl >/dev/null 2>&1; then
    fail "restart" "未检测到 systemctl，无法重启服务。"
  fi
  if command -v systemd-run >/dev/null 2>&1; then
    ${SUDO} systemd-run \
      --unit=xbot-self-update-restart \
      --description="Restart Xbot after self update" \
      --on-active=2 \
      --collect \
      /bin/systemctl restart "$SERVICE_NAME" >/dev/null || fail "restart" "创建 systemd 重启任务失败。"
  else
    fail "restart" "未检测到 systemd-run，无法安全执行后台重启。"
  fi
}

compose_cmd() {
  local args=()
  IFS=':' read -r -a files <<< "$COMPOSE_FILES"
  local file
  for file in "${files[@]}"; do
    args+=("-f" "${COMPOSE_PROJECT_DIR}/${file}")
  done
  docker compose "${args[@]}" --project-directory "$COMPOSE_PROJECT_DIR" "$@"
}

restart_docker_compose() {
  write_state "running" "docker" "正在通过 Docker Compose 更新容器。"
  has_docker_socket || fail "docker" "未挂载 /var/run/docker.sock，Docker 模式无法由 Bot 执行升级。"
  has_docker_compose || fail "docker" "未检测到 docker compose。"
  IFS=':' read -r -a files <<< "$COMPOSE_FILES"
  local file
  for file in "${files[@]}"; do
    [[ -f "${COMPOSE_PROJECT_DIR}/${file}" ]] || fail "docker" "未找到 Compose 文件：${COMPOSE_PROJECT_DIR}/${file}"
  done

  if [[ -n "$GHCR_IMAGE" ]]; then
    write_state "running" "docker_pull_tag" "正在拉取镜像：${GHCR_IMAGE}:${TARGET_VERSION}。"
    docker pull "${GHCR_IMAGE}:${TARGET_VERSION}" || fail "docker_pull_tag" "拉取目标版本镜像失败：${GHCR_IMAGE}:${TARGET_VERSION}"
  else
    write_state "running" "docker_pull" "正在拉取 Compose 镜像。"
    compose_cmd pull "$COMPOSE_SERVICE" || fail "docker_pull" "docker compose pull 失败。"
  fi

  write_state "restarting" "docker_up" "镜像已更新，正在重建 Xbot 容器。"
  # 当前脚本在容器内运行。docker compose up -d 会替换当前容器，因此后续状态由新容器启动后读取。
  compose_cmd up -d --no-deps "$COMPOSE_SERVICE" || fail "docker_up" "docker compose up -d 失败。"
}

validate_target_version
resolve_mode
write_state "running" "start" "后台更新已开始，模式：${UPDATE_MODE}。"

case "$UPDATE_MODE" in
  systemd)
    ensure_git_repo_clean
    checkout_target_version
    update_python_dependencies
    restart_systemd
    ;;
  docker-compose)
    restart_docker_compose
    ;;
  git-only)
    ensure_git_repo_clean
    checkout_target_version
    write_state "succeeded" "done" "源码已更新到 ${TARGET_VERSION}，请手动重启 Xbot。"
    ;;
  *)
    fail "mode" "未知更新模式：${UPDATE_MODE}"
    ;;
esac

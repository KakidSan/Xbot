#!/usr/bin/env bash
set -euo pipefail

TARGET_VERSION="${1:-}"
CHAT_ID="${2:-}"
PROJECT_DIR="${COMPOSE_PROJECT_DIR:-${PROJECT_DIR:-/compose}}"
STATE_DIR="${PROJECT_DIR}/.install-state"
STATE_FILE="${STATE_DIR}/update-status.json"
COMPOSE_FILES="${COMPOSE_FILES:-docker-compose.yml}"
COMPOSE_SERVICE="${COMPOSE_SERVICE:-xbot}"
GHCR_IMAGE="${GHCR_IMAGE:-}"

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
  "mode": "docker-compose",
  "updated_at": $(json_escape "$now")
}
EOF
}

fail() {
  write_state "failed" "$1" "$2"
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

compose_cmd() {
  local args=()
  IFS=':' read -r -a files <<< "$COMPOSE_FILES"
  local file
  for file in "${files[@]}"; do
    [[ -f "${PROJECT_DIR}/${file}" ]] || fail "docker" "未找到 Compose 文件：${PROJECT_DIR}/${file}"
    args+=("-f" "${PROJECT_DIR}/${file}")
  done
  docker compose "${args[@]}" --project-directory "$PROJECT_DIR" "$@"
}

validate_target_version
write_state "running" "start" "后台更新已开始，模式：docker-compose。"
has_docker_socket || fail "docker" "未挂载 /var/run/docker.sock，Docker 模式无法由 Bot 执行升级。"
has_docker_compose || fail "docker" "未检测到 docker compose。"

if [[ -n "$GHCR_IMAGE" ]]; then
  write_state "running" "docker_pull_tag" "正在拉取镜像：${GHCR_IMAGE}:${TARGET_VERSION}。"
  docker pull "${GHCR_IMAGE}:${TARGET_VERSION}" || fail "docker_pull_tag" "拉取目标版本镜像失败：${GHCR_IMAGE}:${TARGET_VERSION}"
else
  write_state "running" "docker_pull" "正在拉取 Compose 镜像。"
  compose_cmd pull "$COMPOSE_SERVICE" || fail "docker_pull" "docker compose pull 失败。"
fi

write_state "restarting" "docker_up" "镜像已更新，正在重建 Xbot 容器。"
compose_cmd up -d --no-deps "$COMPOSE_SERVICE" || fail "docker_up" "docker compose up -d 失败。"

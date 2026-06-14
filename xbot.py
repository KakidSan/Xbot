#!/usr/bin/env python3
"""Xbot Telegram Monitor Bot.

当前版本能力：
- Docker / Docker Compose 后台常驻运行；
- 使用环境变量传入 Telegram / Redis / MySQL 连接参数；
- Telegram 用户白名单校验；
- MySQL 连接测试，只执行只读查询；
- Redis 连接测试，并读取少量摘要信息格式化展示。
"""

from __future__ import annotations

import argparse
import asyncio
import calendar
import hashlib
import html
import ipaddress
import json
import logging
import os
import re
import signal
import socket
import sqlite3
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pymysql
import redis
from pymysql import MySQLError
from redis.exceptions import (
    AuthenticationError,
    ConnectionError as RedisConnectionError,
    RedisError,
    TimeoutError as RedisTimeoutError,
)
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.error import BadRequest
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
BOT_COMMANDS = [
    BotCommand("start", "主菜单"),
    BotCommand("help", "Proxy Protocol 说明"),
    BotCommand("version", "查看版本"),
    BotCommand("clear_history", "清除对话记录"),
]
log = logging.getLogger("xbot")
PROCESS_STARTED_AT = datetime.now()
TRAFFIC_SAMPLE_INTERVAL_SECONDS = 60
TRAFFIC_SAMPLE_GAP_TOLERANCE_SECONDS = 90
BEIJING_TZ = timezone(timedelta(hours=8))
TRAFFIC_REPORT_KINDS = {"daily": "流量日报", "weekly": "流量周报", "monthly": "流量月报"}
ALERT_NOTIFICATION_KINDS = {"traffic_alert": "用量异常", "ip_alert": "异地登录"}
NOTIFICATION_KINDS = {"collector": "采集异常", **ALERT_NOTIFICATION_KINDS, **TRAFFIC_REPORT_KINDS, "version_update": "版本更新"}
DEFAULT_ALLOWLIST_NOTIFICATION_KINDS = {"collector", "traffic_alert", "ip_alert", "version_update"}
COLLECTOR_HEALTH_SERVICES = {"redis": "Redis", "mysql": "MySQL", "ip_api": "IP-API"}
TRAFFIC_ALERT_DEFAULT_THRESHOLD_BYTES = 100 * 1024 ** 3
IP_ALERT_DEFAULT_CITY_THRESHOLD = 3
DEFAULT_CACHE_RETENTION_DAYS = 31
DEFAULT_COLLECTOR_INTERVAL_SECONDS = 60.0
DEFAULT_IP_GEO_QUERIES_PER_MINUTE = 30
CACHE_RETENTION_OPTIONS = {
    "1m": (31, "一月"),
    "1q": (93, "一季"),
    "1y": (366, "一年"),
    "all": (0, "一切"),
}
ALERT_DEFAULT_PERIOD = "24h"
ALERT_PERIOD_LABELS = {"1h": "近 1 小时", "24h": "近 24 小时", "7d": "近 7 天", "today": "今天", "week": "本周"}
PROXY_PROTOCOL_NOTICE = "⚠️ 此功能准确性受 Proxy Protocol 配置影响，可点击 /help 查看说明。"
APP_DIR = Path(__file__).resolve().parent
DEFAULT_CACHE_PATH = APP_DIR / "data" / "xbot.sqlite3"
TAGS_API_URL = "https://api.github.com/repos/KakidSan/Xbot/tags"
GHCR_IMAGE = "ghcr.io/kakidsan/xbot"
VERSION_FILE = APP_DIR / "VERSION"
FALLBACK_VERSION = "0.0.0-dev"
UPDATE_SCRIPT = APP_DIR / "scripts" / "update.sh"
UPDATE_STATUS_FILE = APP_DIR / ".install-state" / "update-status.json"
VERSION_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
_INITIALIZED_CACHE_PATHS: set[Path] = set()


def run_command_sync(args: list[str], cwd: Path = APP_DIR, timeout: int = 20) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(args, cwd=str(cwd), text=True, capture_output=True, timeout=timeout)
    except Exception as exc:
        return 1, "", f"{type(exc).__name__}: {exc}"
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def current_git_version_sync() -> tuple[str | None, str | None, bool, str]:
    rc, _, _ = run_command_sync(["git", "rev-parse", "--is-inside-work-tree"])
    if rc != 0:
        return None, None, False, "not_git"
    rc, desc, err = run_command_sync(["git", "describe", "--tags", "--always", "--dirty"])
    if rc != 0:
        return None, None, False, err or "git describe failed"
    rc, commit, _ = run_command_sync(["git", "rev-parse", "--short", "HEAD"])
    dirty = desc.endswith("-dirty")
    return desc, commit if rc == 0 else None, dirty, "git"


def read_app_version() -> str:
    git_version, _, _, source = current_git_version_sync()
    if source == "git" and git_version:
        return git_version
    try:
        version = VERSION_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return FALLBACK_VERSION
    return version or FALLBACK_VERSION


def parse_version_tuple(tag: str) -> tuple[int, int, int, str]:
    tag = tag.strip()
    m = re.match(r"^v(\d+)\.(\d+)\.(\d+)(.*)$", tag)
    if not m:
        return (-1, -1, -1, tag)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4) or "")


def latest_remote_version_sync() -> tuple[str | None, str | None]:
    try:
        req = urllib.request.Request(
            TAGS_API_URL,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "xbot-version-check"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tags = [str(item.get("name") or "").strip() for item in data if isinstance(item, dict)]
        tags = [tag for tag in tags if VERSION_TAG_RE.fullmatch(tag)]
        if tags:
            tags.sort(key=parse_version_tuple, reverse=True)
            return tags[0], None
        return None, "GitHub tags 中没有符合 vX.Y.Z 格式的版本标签。"
    except Exception as exc:
        return None, f"读取 GitHub tags 失败：{type(exc).__name__}: {exc}"


def current_release_tag(version: str | None = None) -> str | None:
    version = version or read_app_version()
    m = re.match(r"^(v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)", version)
    return m.group(1) if m else None


def is_remote_newer(current: str | None, latest: str | None) -> bool:
    if not latest:
        return False
    if not current:
        return True
    return parse_version_tuple(latest) > parse_version_tuple(current)


def version_check_sync() -> dict[str, Any]:
    git_version, commit, dirty, source = current_git_version_sync()
    current = git_version or read_app_version()
    current_tag = current_release_tag(current)
    latest, error = latest_remote_version_sync()
    return {
        "current": current,
        "current_tag": current_tag,
        "commit": commit,
        "dirty": dirty,
        "source": source,
        "latest": latest,
        "error": error,
        "has_update": bool(latest and is_remote_newer(current_tag, latest)),
    }


def version_text(check: dict[str, Any] | None = None, admin_view: bool = True) -> str:
    check = check or {"current": read_app_version(), "latest": None, "error": None, "has_update": False, "source": "local"}
    lines = [
        "🔖 <b>Xbot 版本信息</b>",
        "────────────",
        f"当前版本：<code>{html.escape(str(check.get('current') or read_app_version()))}</code>",
    ]
    if not admin_view:
        return "\n".join(lines)
    commit = check.get("commit")
    if commit:
        lines.append(f"当前提交：<code>{html.escape(str(commit))}</code>")
    if check.get("source"):
        lines.append(f"版本来源：{html.escape(str(check.get('source')))}")
    lines.append(f"本地修改：{'是' if check.get('dirty') else '否'}")
    lines.append("")
    if check.get("error"):
        lines.append(f"更新检查：⚠️ {html.escape(str(check['error']))}")
    elif check.get("latest"):
        lines.append(f"最新版本：<code>{html.escape(str(check['latest']))}</code>")
        lines.append("状态：⬆️ 发现新版本" if check.get("has_update") else "状态：✅ 当前已是最新版本")
    else:
        lines.append("更新检查：未发现远程版本信息")
    lines.append(f"检查时间：{beijing_now().strftime('%Y-%m-%d %H:%M:%S')} 北京时间")
    return "\n".join(lines)


def version_keyboard(check: dict[str, Any] | None = None, admin_view: bool = True) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    latest = str(check.get("latest") or "") if check else ""
    if admin_view and check and check.get("has_update") and VERSION_TAG_RE.fullmatch(latest):
        rows.append([InlineKeyboardButton(f"⬆️ 后台更新到 {latest}", callback_data=f"version_update:start:{latest}")])
    rows.append([InlineKeyboardButton("⬅️ 返回主菜单", callback_data="main_menu"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")])
    return InlineKeyboardMarkup(rows)


def update_confirm_keyboard(target_version: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ 确认后台更新", callback_data=f"version_update:confirm:{target_version}")],
        [InlineKeyboardButton("❌ 取消", callback_data="version_update:cancel")],
    ])


def update_started_text(target_version: str) -> str:
    return "\n".join([
        "⬆️ <b>即将开始后台更新</b>",
        "────────────",
        f"目标版本：<code>{html.escape(target_version)}</code>",
        "",
        "更新过程将会拉取远程代码、更新 Python 依赖并重启 xbot.service。",
        "",
        "⚠️ 即将开始更新，可能会影响数据采集连续性。",
        "请再次确认是否继续。",
    ])


def start_background_update_sync(target_version: str, chat_id: str) -> tuple[bool, str]:
    if not VERSION_TAG_RE.fullmatch(target_version):
        return False, "目标版本无效。"
    if not UPDATE_SCRIPT.exists():
        return False, "更新脚本不存在：scripts/update.sh"
    try:
        UPDATE_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(
            [str(UPDATE_SCRIPT), target_version, chat_id],
            cwd=str(APP_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        return False, f"启动后台更新失败：{type(exc).__name__}: {exc}"
    return True, "后台更新已启动。"


def consume_update_status_sync() -> dict[str, Any] | None:
    if not UPDATE_STATUS_FILE.exists():
        return None
    try:
        data = json.loads(UPDATE_STATUS_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("读取更新状态失败：%s", exc)
        return None
    status = str(data.get("status") or "")
    if status not in {"restarting", "failed"}:
        return None
    if status == "restarting":
        data["status"] = "success"
        data["message"] = "Xbot 已重启，后台更新完成。"
        data["current_version"] = read_app_version()
    try:
        UPDATE_STATUS_FILE.unlink()
    except OSError:
        pass
    return data


def update_result_text(data: dict[str, Any]) -> str:
    status = str(data.get("status") or "")
    if status == "success":
        return "\n".join([
            "✅ <b>Xbot 更新完成</b>",
            "────────────",
            f"目标版本：<code>{html.escape(str(data.get('target_version') or 'unknown'))}</code>",
            f"当前版本：<code>{html.escape(str(data.get('current_version') or read_app_version()))}</code>",
            f"完成时间：{beijing_now().strftime('%Y-%m-%d %H:%M:%S')} 北京时间",
        ])
    return "\n".join([
        "❌ <b>Xbot 更新失败</b>",
        "────────────",
        f"目标版本：<code>{html.escape(str(data.get('target_version') or 'unknown'))}</code>",
        f"失败阶段：<code>{html.escape(str(data.get('stage') or 'unknown'))}</code>",
        f"错误信息：{html.escape(str(data.get('message') or 'unknown'))}",
        "",
        "请通过 SSH 查看：",
        "<code>sudo journalctl -u xbot -f</code>",
    ])


def proxy_protocol_help_text() -> str:
    return "\n".join([
        "📘 <b>Proxy Protocol 配置说明</b>",
        "────────────",
        "转发入口通过 Proxy Protocol 把真实的客户端 IP 传递给后端服务。",
        "",
        "Xbot 的 IP 监控功能严重依赖 Proxy Protocol 的传递结果，如果没有正确配置，将无法保证统计的准确性。",
        "",
        "后端服务的相关配置，可参考 <a href=\"https://hekicore.github.io/heki-docs/#/other/forward-get-real-ip\">Heki Docs</a>。",
    ])


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    admin_user_id: int | None = None  # 唯一超级管理员，只能通过环境变量修改。
    manager_user_ids: set[int] = field(default_factory=set)  # 普通管理员，可由超级管理员在 Bot 内管理。
    authorized_user_ids: set[int] = field(default_factory=set)  # 普通授权用户。

    @property
    def admin_user_ids(self) -> set[int]:
        users = set(self.manager_user_ids)
        if self.admin_user_id is not None:
            users.add(self.admin_user_id)
        return users

    @property
    def allowed_user_ids(self) -> set[int]:
        users = set(self.authorized_user_ids) | set(self.manager_user_ids)
        if self.admin_user_id is not None:
            users.add(self.admin_user_id)
        return users


@dataclass(frozen=True)
class RedisConfig:
    # host / port 允许留空，用于明确提示“Config 未填写 Redis 信息”。
    host: str = ""
    port: int | None = None
    password: str | None = None
    db: int = 0


@dataclass(frozen=True)
class MySQLConfig:
    host: str = ""
    port: int | None = None
    database: str = ""
    username: str = ""
    password: str = ""


@dataclass(frozen=True)
class AppConfig:
    telegram: TelegramConfig
    redis: RedisConfig
    mysql: MySQLConfig
    cache_path: Path = Path("data/xbot.sqlite3")
    collector_interval_seconds: float = DEFAULT_COLLECTOR_INTERVAL_SECONDS
    traffic_dashboard_refresh_seconds: float = 60.0
    cache_retention_days: int = DEFAULT_CACHE_RETENTION_DAYS
    ip_geo_queries_per_minute: int = DEFAULT_IP_GEO_QUERIES_PER_MINUTE


def _as_int_set(value: Any) -> set[int]:
    """Parse Telegram user ids from env comma strings or internal JSON arrays."""
    if value is None:
        return set()
    if isinstance(value, str):
        raw_items = [item.strip() for item in value.split(",")]
    elif isinstance(value, list):
        raw_items = value
    else:
        raise ValueError("Telegram 用户 ID 列表必须是英文逗号分隔字符串")

    result: set[int] = set()
    for item in raw_items:
        if item in (None, ""):
            continue
        try:
            result.add(int(item))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"无效 Telegram 用户 ID：{item!r}") from exc
    return result


def _optional_int(value: Any) -> int | None:
    return int(value) if value not in (None, "") else None


def env_value(name: str, default: Any = None) -> Any:
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def env_int(name: str, default: Any = None) -> Any:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return int(value)


def apply_env_overrides(raw: dict[str, Any]) -> dict[str, Any]:
    """Populate the internal runtime Config from Docker/Compose environment variables."""
    telegram_raw = raw.setdefault("telegram", {})
    redis_raw = raw.setdefault("redis", {})
    mysql_raw = raw.setdefault("mysql", {})
    app_raw = raw.setdefault("app", {})

    telegram_raw["bot_token"] = env_value("TELEGRAM_BOT_TOKEN", telegram_raw.get("bot_token"))
    telegram_raw["admin_user_id"] = env_int("TELEGRAM_ADMIN_USER_ID", telegram_raw.get("admin_user_id"))
    telegram_raw["manager_user_ids"] = env_value("TELEGRAM_MANAGER_USER_IDS", telegram_raw.get("manager_user_ids"))
    telegram_raw["authorized_user_ids"] = env_value("TELEGRAM_AUTHORIZED_USER_IDS", telegram_raw.get("authorized_user_ids"))

    redis_raw["host"] = env_value("REDIS_HOST", redis_raw.get("host"))
    redis_raw["port"] = env_int("REDIS_PORT", redis_raw.get("port"))
    redis_raw["password"] = env_value("REDIS_PASSWORD", redis_raw.get("password"))
    redis_raw["db"] = env_int("REDIS_DB", redis_raw.get("db", 0))
    mysql_raw["host"] = env_value("MYSQL_HOST", mysql_raw.get("host"))
    mysql_raw["port"] = env_int("MYSQL_PORT", mysql_raw.get("port"))
    mysql_raw["database"] = env_value("MYSQL_DATABASE", mysql_raw.get("database"))
    mysql_raw["username"] = env_value("MYSQL_USERNAME", mysql_raw.get("username"))
    mysql_raw["password"] = env_value("MYSQL_PASSWORD", mysql_raw.get("password"))

    return raw


def build_config_from_env() -> AppConfig:
    raw: dict[str, Any] = {"telegram": {}, "redis": {}, "mysql": {}, "app": {}}
    raw = apply_env_overrides(raw)
    telegram_raw = raw.get("telegram") or {}
    redis_raw = raw.get("redis") or {}
    mysql_raw = raw.get("mysql") or {}
    app_raw = raw.get("app") or {}

    token = str(telegram_raw.get("bot_token") or "").strip()
    if not token or token == "123456:replace_me":
        raise ValueError("TELEGRAM_BOT_TOKEN 不能为空，请填写 BotFather 提供的 Token")

    cache_path = Path(str(app_raw.get("cache_path") or DEFAULT_CACHE_PATH)).expanduser()

    admin_raw = telegram_raw.get("admin_user_id")
    admin_user_id = int(admin_raw) if admin_raw not in (None, "") else None
    manager_user_ids = _as_int_set(telegram_raw.get("manager_user_ids"))
    authorized_user_ids = _as_int_set(telegram_raw.get("authorized_user_ids"))
    stored_roles = auth_roles_load_sync(cache_path)
    if stored_roles is None:
        auth_roles_save_sync(cache_path, manager_user_ids, authorized_user_ids)
    else:
        manager_user_ids, authorized_user_ids = stored_roles
    if admin_user_id is not None:
        manager_user_ids.discard(admin_user_id)
        authorized_user_ids.discard(admin_user_id)
    authorized_user_ids.difference_update(manager_user_ids)
    allowed_user_ids = set(authorized_user_ids) | set(manager_user_ids)
    if admin_user_id is not None:
        allowed_user_ids.add(admin_user_id)
    if not allowed_user_ids:
        log.warning("Telegram 授权用户为空，当前将拒绝所有 Telegram 用户访问")

    return AppConfig(
        telegram=TelegramConfig(bot_token=token, admin_user_id=admin_user_id, manager_user_ids=manager_user_ids, authorized_user_ids=authorized_user_ids),
        redis=RedisConfig(
            host=str(redis_raw.get("host", "") or ""),
            port=_optional_int(redis_raw.get("port")),
            password=redis_raw.get("password") or None,
            db=int(redis_raw.get("db", 0)),
        ),
        mysql=MySQLConfig(
            host=str(mysql_raw.get("host", "") or ""),
            port=_optional_int(mysql_raw.get("port")),
            database=str(mysql_raw.get("database", "") or ""),
            username=str(mysql_raw.get("username", "") or ""),
            password=str(mysql_raw.get("password", "") or ""),
        ),
        cache_path=cache_path,
        collector_interval_seconds=DEFAULT_COLLECTOR_INTERVAL_SECONDS,
        traffic_dashboard_refresh_seconds=60.0,
        cache_retention_days=DEFAULT_CACHE_RETENTION_DAYS,
        ip_geo_queries_per_minute=DEFAULT_IP_GEO_QUERIES_PER_MINUTE,
    )


def user_id(update: Update) -> int | None:
    return update.effective_user.id if update.effective_user else None


def user_display(update: Update) -> str:
    user = update.effective_user
    if not user:
        return "unknown"
    name = user.full_name or user.username or str(user.id)
    return f"{name} ({user.id})"


def html_code(value: Any) -> str:
    return f"<code>{html.escape(str(value))}</code>"


def bot_id_from_token(token: str) -> int | None:
    match = re.match(r"^(\d+):", token.strip())
    return int(match.group(1)) if match else None


def is_bot_self_update(update: Update, cfg: AppConfig) -> bool:
    user = update.effective_user
    if not user:
        return False
    token_bot_id = bot_id_from_token(cfg.telegram.bot_token)
    return bool(getattr(user, "is_bot", False)) or (token_bot_id is not None and user.id == token_bot_id)


def is_allowed(update: Update, cfg: AppConfig) -> bool:
    uid = user_id(update)
    return uid is not None and uid in cfg.telegram.allowed_user_ids


def is_super_admin_user_id(uid: int | None, cfg: AppConfig) -> bool:
    return uid is not None and cfg.telegram.admin_user_id is not None and uid == cfg.telegram.admin_user_id


def is_admin_user_id(uid: int | None, cfg: AppConfig) -> bool:
    return uid is not None and uid in cfg.telegram.admin_user_ids


def tcp_check(host: str, port: int, service_name: str) -> tuple[bool, list[str]]:
    """Check TCP reachability without exposing configured host/port in messages."""
    try:
        with socket.create_connection((host, port), timeout=3):
            return True, []
    except socket.timeout:
        return False, [
            f"❌ {service_name} 端口连接超时。",
            f"❌ 可能是防火墙丢弃连接、网络不通，或 {service_name} 未监听外部连接。",
        ]
    except ConnectionRefusedError as exc:
        return False, [
            f"❌ {service_name} 端口拒绝连接。",
            f"❌ 错误类型：{html_code(type(exc).__name__)}",
            f"❌ 目标主机可到达，但没有服务接受连接；常见原因是 {service_name} 未启动、未监听外部连接、端口未映射，或防火墙主动拒绝。",
        ]
    except OSError as exc:
        return False, [
            f"❌ {service_name} 端口无法访问。",
            f"❌ 错误类型：{html_code(type(exc).__name__)}",
            f"❌ 可能是地址错误、路由不可达、防火墙拦截，或 {service_name} 未监听外部连接。",
        ]


def redis_config_missing(cfg: RedisConfig) -> bool:
    return not cfg.host.strip() or not cfg.port


def mysql_config_missing(cfg: MySQLConfig) -> bool:
    return not cfg.host.strip() or not cfg.port or not cfg.username.strip() or not cfg.database.strip()


ONLINE_IP_KEY_SPECS: tuple[tuple[str, str, str], ...] = (
    ("Heki", "heki:ip:*", r"heki:ip:(\d+):(.+)"),
    ("Soga", "soga_conn_*", r"soga_conn_(\d+)_(.+)"),
)


def redis_readable_summary(client: redis.Redis, cfg: RedisConfig) -> list[str]:
    """Read a small Redis summary and render it in human-friendly lines.

    This avoids dumping raw values. It only inspects metadata: DB key count,
    sampled key types, TTL state, and XBoard-like online device key count.
    """
    lines: list[str] = []

    db_size = client.dbsize()
    lines.append(f"✅ Redis 当前 DB Key 数量：{db_size}")

    sample_keys = list(client.scan_iter(match="*", count=100))[:30]
    if not sample_keys:
        lines.append("✅ Redis 当前 DB 暂无可展示 Key。")
        return lines

    type_counter: Counter[str] = Counter()
    ttl_counter: Counter[str] = Counter()
    for key in sample_keys:
        try:
            key_type = client.type(key)
            ttl = client.ttl(key)
        except RedisError:
            continue
        type_counter[str(key_type)] += 1
        if ttl == -2:
            ttl_counter["已过期/不存在"] += 1
        elif ttl == -1:
            ttl_counter["永久"] += 1
        else:
            ttl_counter["有过期时间"] += 1

    if type_counter:
        type_text = "，".join(f"{name}: {count}" for name, count in sorted(type_counter.items()))
        lines.append(f"✅ 抽样 Key 类型分布：{type_text}")
    if ttl_counter:
        ttl_text = "，".join(f"{name}: {count}" for name, count in sorted(ttl_counter.items()))
        lines.append(f"✅ 抽样 Key 过期状态：{ttl_text}")

    online_key_counts: list[str] = []
    for label, pattern, _ in ONLINE_IP_KEY_SPECS:
        count = sum(1 for _ in client.scan_iter(match=pattern, count=100))
        online_key_counts.append(f"{label}: {count}")
    lines.append(f"✅ 在线 IP Key 数量：{'，'.join(online_key_counts)}")

    device_pattern = "user_devices:*"
    device_count = sum(1 for _ in client.scan_iter(match=device_pattern, count=100))
    lines.append(f"✅ XBoard 在线设备 Key 数量：{device_count}")
    return lines


def redis_client(cfg: RedisConfig) -> redis.Redis:
    return redis.Redis(
        host=cfg.host.strip(),
        port=int(cfg.port),
        password=cfg.password,
        db=cfg.db,
        socket_connect_timeout=3,
        socket_timeout=5,
        decode_responses=True,
    )


def redis_failure_message(exc: Exception) -> str:
    if isinstance(exc, AuthenticationError):
        return f"❌ Redis 认证失败。\n❌ 错误类型：{html_code(type(exc).__name__)}\n❌ Redis 密码不正确，或 Redis 要求认证但 Config 未填写密码。"
    if isinstance(exc, RedisTimeoutError):
        return f"❌ Redis 响应超时。\n❌ 错误类型：{html_code(type(exc).__name__)}\n❌ Redis 负载可能过高，或网络质量异常。"
    if isinstance(exc, RedisConnectionError):
        return f"❌ Redis 握手失败。\n❌ 错误类型：{html_code(type(exc).__name__)}\n❌ 端口可能不是 Redis 服务、TLS/SSL 配置不匹配，或连接被服务端关闭。"
    if isinstance(exc, RedisError):
        return f"❌ Redis 返回错误。\n❌ 错误类型：{html_code(type(exc).__name__)}"
    return f"❌ Redis 检查失败。\n❌ 错误类型：{html_code(type(exc).__name__)}"


def test_redis_connection_sync(cfg: RedisConfig) -> str:
    """Return a user-facing Redis diagnosis message.

    TCP is checked first, then Redis PING. When PING succeeds, a small metadata
    summary is read and formatted for humans. No raw Redis values are displayed.
    """
    if redis_config_missing(cfg):
        return "⚠️ Redis 连接失败\n\n❌ Redis 连接信息未输入完整。"

    host = cfg.host.strip()
    port = int(cfg.port)
    ok, tcp_lines = tcp_check(host, port, "Redis")
    if not ok:
        return "\n".join(["⚠️ Redis 连接失败", "", *tcp_lines])

    client = redis_client(cfg)
    try:
        pong = client.ping()
        if pong is not True:
            return f"⚠️ Redis 连接失败\n\n✅ Redis 端口可访问。\n❌ Redis PING 返回异常：{pong}"
        summary_lines = redis_readable_summary(client, cfg)
    except RedisError as exc:
        return "\n".join(["⚠️ Redis 连接失败", "", "✅ Redis 端口可访问。", redis_failure_message(exc)])
    finally:
        client.close()

    return "\n".join([
        "✅ Redis 连接成功",
        "",
        "✅ Redis 端口可访问。",
        "✅ Redis PING 测试成功。",
        *summary_lines,
    ])


def last_seen_from_ttl(ttl: int) -> datetime | None:
    """Roughly estimate last online time from Heki/Soga ip string key TTL."""
    if ttl < 0:
        return None
    base_ttl_seconds = 7 * 24 * 60 * 60
    elapsed_seconds = max(0, base_ttl_seconds - ttl)
    return datetime.now() - timedelta(seconds=elapsed_seconds)


def resolve_cache_path(path: Path, base_dir: Path | None = None) -> Path:
    if path.is_absolute():
        return path
    return (base_dir or Path.cwd()) / path


def cache_connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_cache(path: Path) -> None:
    cache_path = path.resolve()
    if cache_path in _INITIALIZED_CACHE_PATHS and cache_path.exists():
        return
    with cache_connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS active_ip_records (
                user_id INTEGER NOT NULL,
                ip TEXT NOT NULL,
                first_seen_at INTEGER NOT NULL,
                last_seen_at INTEGER NOT NULL,
                last_ttl INTEGER,
                source_key TEXT,
                ignored_at INTEGER,
                ignore_reason TEXT,
                ignore_note TEXT,
                PRIMARY KEY (user_id, ip)
            );
            CREATE INDEX IF NOT EXISTS idx_active_ip_records_last_seen_at
                ON active_ip_records(last_seen_at);
            CREATE INDEX IF NOT EXISTS idx_active_ip_records_user_last_seen
                ON active_ip_records(user_id, last_seen_at);

            CREATE TABLE IF NOT EXISTS debug_ip_record_suppressions (
                user_id INTEGER NOT NULL,
                ip TEXT NOT NULL,
                last_seen_floor INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (user_id, ip)
            );
            CREATE INDEX IF NOT EXISTS idx_debug_ip_record_suppressions_expires
                ON debug_ip_record_suppressions(expires_at);

            CREATE TABLE IF NOT EXISTS ignored_ip_rules (
                dimension TEXT NOT NULL,
                value TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (dimension, value)
            );

            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                display_name TEXT,
                remarks TEXT,
                email TEXT,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ip_geo_cache (
                ip TEXT PRIMARY KEY,
                country TEXT,
                region TEXT,
                city TEXT,
                district TEXT,
                isp TEXT,
                stat_area_key TEXT,
                stat_area_name TEXT,
                stat_area_level TEXT,
                raw TEXT,
                queried_at INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS collector_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pinned_dashboard_messages (
                kind TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                is_pinned INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (kind, chat_id)
            );

            CREATE TABLE IF NOT EXISTS dashboard_auto_delete_messages (
                chat_id TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                is_pinned INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (chat_id, message_id)
            );

            CREATE TABLE IF NOT EXISTS notification_subscriptions (
                chat_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (chat_id, kind)
            );

            CREATE TABLE IF NOT EXISTS traffic_counter_snapshots (
                kind TEXT NOT NULL,
                entity_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                total INTEGER NOT NULL,
                sampled_at INTEGER NOT NULL,
                PRIMARY KEY (kind, entity_id)
            );

            CREATE TABLE IF NOT EXISTS traffic_delta_samples (
                sampled_at INTEGER NOT NULL,
                kind TEXT NOT NULL,
                entity_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                delta INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS traffic_sample_gaps (
                gap_start_at INTEGER NOT NULL,
                gap_end_at INTEGER NOT NULL,
                gap_seconds INTEGER NOT NULL,
                detected_at INTEGER NOT NULL,
                PRIMARY KEY (gap_start_at, gap_end_at)
            );
            CREATE TABLE IF NOT EXISTS traffic_ranges (
                kind TEXT PRIMARY KEY,
                start_ts INTEGER NOT NULL,
                end_ts INTEGER NOT NULL,
                label TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ui_preferences (
                user_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (user_id, key)
            );

            CREATE TABLE IF NOT EXISTS operation_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at INTEGER NOT NULL,
                actor_tg_id INTEGER,
                actor_name TEXT,
                category TEXT NOT NULL,
                action TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_operation_logs_category_time
                ON operation_logs(category, created_at DESC);
            CREATE TABLE IF NOT EXISTS operation_log_reads (
                user_id INTEGER NOT NULL,
                log_id INTEGER NOT NULL,
                read_at INTEGER NOT NULL,
                PRIMARY KEY (user_id, log_id)
            );
            CREATE INDEX IF NOT EXISTS idx_operation_log_reads_user
                ON operation_log_reads(user_id, log_id);

            CREATE TABLE IF NOT EXISTS alert_user_settings (
                user_id INTEGER PRIMARY KEY,
                traffic_threshold_bytes INTEGER,
                traffic_whitelist INTEGER NOT NULL DEFAULT 0,
                ip_city_threshold INTEGER,
                ip_whitelist INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_traffic_delta_samples_window
                ON traffic_delta_samples(kind, sampled_at);
            CREATE INDEX IF NOT EXISTS idx_traffic_delta_samples_entity
                ON traffic_delta_samples(kind, entity_id, sampled_at);
            CREATE INDEX IF NOT EXISTS idx_traffic_sample_gaps_window
                ON traffic_sample_gaps(gap_start_at, gap_end_at);
            """
        )
        existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(alert_user_settings)").fetchall()}
        if "traffic_period" not in existing_columns:
            conn.execute("ALTER TABLE alert_user_settings ADD COLUMN traffic_period TEXT")
        if "ip_period" not in existing_columns:
            conn.execute("ALTER TABLE alert_user_settings ADD COLUMN ip_period TEXT")
        active_ip_columns = {row[1] for row in conn.execute("PRAGMA table_info(active_ip_records)").fetchall()}
        if "ignored_at" not in active_ip_columns:
            conn.execute("ALTER TABLE active_ip_records ADD COLUMN ignored_at INTEGER")
        if "ignore_reason" not in active_ip_columns:
            conn.execute("ALTER TABLE active_ip_records ADD COLUMN ignore_reason TEXT")
        if "ignore_note" not in active_ip_columns:
            conn.execute("ALTER TABLE active_ip_records ADD COLUMN ignore_note TEXT")
        geo_cache_columns = {row[1] for row in conn.execute("PRAGMA table_info(ip_geo_cache)").fetchall()}
        for column_name, column_type in {
            "district": "TEXT",
            "stat_area_key": "TEXT",
            "stat_area_name": "TEXT",
            "stat_area_level": "TEXT",
        }.items():
            if column_name not in geo_cache_columns:
                conn.execute(f"ALTER TABLE ip_geo_cache ADD COLUMN {column_name} {column_type}")
        rows_needing_stat_area = conn.execute(
            """
            SELECT ip, raw
            FROM ip_geo_cache
            WHERE (stat_area_key IS NULL OR stat_area_key = '')
              AND raw IS NOT NULL AND raw != ''
            LIMIT 1000
            """
        ).fetchall()
        for geo_row in rows_needing_stat_area:
            try:
                raw_data = json.loads(str(geo_row["raw"] or "{}"))
            except Exception:
                continue
            if not isinstance(raw_data, dict) or raw_data.get("status") not in (None, "success"):
                continue
            stat_area = build_geo_stat_area(raw_data)
            conn.execute(
                """
                UPDATE ip_geo_cache
                SET district = COALESCE(NULLIF(district, ''), ?),
                    stat_area_key = ?,
                    stat_area_name = ?,
                    stat_area_level = ?
                WHERE ip = ?
                """,
                (
                    str(raw_data.get("district") or ""),
                    stat_area["key"],
                    stat_area["name"],
                    stat_area["level"],
                    str(geo_row["ip"]),
                ),
            )
    _INITIALIZED_CACHE_PATHS.add(cache_path)


def ui_pref_get_sync(cache_path: Path, user_id: int, key: str) -> str | None:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        row = conn.execute(
            "SELECT value FROM ui_preferences WHERE user_id = ? AND key = ?",
 (user_id, key),
        ).fetchone()
    return str(row["value"]) if row else None


def ui_pref_set_sync(cache_path: Path, user_id: int, key: str, value: str) -> None:
    init_cache(cache_path)
    now_ts = int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        conn.execute(
            """
            INSERT INTO ui_preferences(user_id, key, value, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
 (user_id, key, value, now_ts),
        )


def ui_pref_delete_sync(cache_path: Path, user_id: int, key: str) -> None:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        conn.execute("DELETE FROM ui_preferences WHERE user_id = ? AND key = ?", (user_id, key))


def actor_name_from_user(user: Any) -> str:
    if not user:
        return "未知用户"
    username = getattr(user, "username", None)
    full_name = getattr(user, "full_name", None)
    if username and full_name:
        return f"{full_name} (@{username})"
    return str(full_name or username or getattr(user, "id", "未知用户"))


def operation_log_add_sync(cache_path: Path, actor_tg_id: int | None, actor_name: str | None, category: str, action: str, detail: str = "") -> None:
    init_cache(cache_path)
    now_ts = int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        conn.execute(
            """
            INSERT INTO operation_logs(created_at, actor_tg_id, actor_name, category, action, detail)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (now_ts, actor_tg_id, actor_name or "", category, action, detail or ""),
        )


def operation_logs_list_sync(cache_path: Path, category: str | None = None, limit: int = 30, viewer_user_id: int | None = None) -> list[dict[str, Any]]:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        if viewer_user_id is not None:
            rows = conn.execute(
                """
                SELECT l.*, CASE WHEN r.log_id IS NULL THEN 0 ELSE 1 END AS is_read
                FROM operation_logs l
                LEFT JOIN operation_log_reads r ON r.log_id = l.id AND r.user_id = ?
                WHERE l.category = ?
                ORDER BY l.created_at DESC, l.id DESC
                LIMIT ?
                """,
                (int(viewer_user_id), category or "", int(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM operation_logs
                WHERE category = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (category or "", int(limit)),
            ).fetchall()
    return [dict(row) for row in rows]


def operation_log_counts_sync(cache_path: Path, viewer_user_id: int, categories: list[str]) -> dict[str, tuple[int, int]]:
    init_cache(cache_path)
    result = {category: (0, 0) for category in categories}
    with cache_connect(cache_path) as conn:
        for category in categories:
            total = int(conn.execute("SELECT COUNT(*) FROM operation_logs WHERE category = ?", (category,)).fetchone()[0] or 0)
            read_count = int(conn.execute(
                """
                SELECT COUNT(*)
                FROM operation_logs l
                JOIN operation_log_reads r ON r.log_id = l.id AND r.user_id = ?
                WHERE l.category = ?
                """,
                (int(viewer_user_id), category),
            ).fetchone()[0] or 0)
            result[category] = (max(total - read_count, 0), total)
    return result


def operation_log_get_sync(cache_path: Path, log_id: int) -> dict[str, Any] | None:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        row = conn.execute("SELECT * FROM operation_logs WHERE id = ?", (int(log_id),)).fetchone()
    return dict(row) if row else None


def operation_log_mark_read_sync(cache_path: Path, viewer_user_id: int, log_id: int) -> None:
    init_cache(cache_path)
    now_ts = int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        conn.execute(
            """
            INSERT INTO operation_log_reads(user_id, log_id, read_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, log_id) DO UPDATE SET read_at = excluded.read_at
            """,
            (int(viewer_user_id), int(log_id), now_ts),
        )


def set_collector_state(conn: sqlite3.Connection, key: str, value: str, now_ts: int) -> None:
    conn.execute(
        """
        INSERT INTO collector_state(key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """,
 (key, value, now_ts),
    )


def get_collector_state_sync(cache_path: Path, key: str) -> tuple[str, int] | None:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        row = conn.execute("SELECT value, updated_at FROM collector_state WHERE key = ?", (key,)).fetchone()
    if not row:
        return None
    return str(row["value"]), int(row["updated_at"] or 0)


def auth_roles_load_sync(cache_path: Path) -> tuple[set[int], set[int]] | None:
    state = get_collector_state_sync(cache_path, "telegram_auth_roles")
    if not state:
        return None
    try:
        data = json.loads(state[0])
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return _as_int_set(data.get("manager_user_ids")), _as_int_set(data.get("authorized_user_ids"))


def auth_roles_save_sync(cache_path: Path, manager_user_ids: set[int], authorized_user_ids: set[int]) -> None:
    init_cache(cache_path)
    now_ts = int(datetime.now().timestamp())
    payload = json.dumps(
        {
            "manager_user_ids": sorted(int(uid) for uid in manager_user_ids),
            "authorized_user_ids": sorted(int(uid) for uid in authorized_user_ids),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    with cache_connect(cache_path) as conn:
        set_collector_state(conn, "telegram_auth_roles", payload, now_ts)


def update_telegram_roles_in_cache_sync(
    cache_path: Path,
    admin_user_id: int | None,
    current_manager_user_ids: set[int],
    current_authorized_user_ids: set[int],
    add_authorized_user_id: int | None = None,
    remove_authorized_user_ids: set[int] | None = None,
    promote_manager_user_ids: set[int] | None = None,
    demote_manager_user_ids: set[int] | None = None,
    remove_manager_user_ids: set[int] | None = None,
) -> tuple[set[int], set[int]]:
    managers = set(current_manager_user_ids)
    users = set(current_authorized_user_ids)

    def ensure_not_super_admin(uid: int) -> None:
        if admin_user_id is not None and int(uid) == admin_user_id:
            raise ValueError("超级管理员只允许通过环境变量管理")

    if add_authorized_user_id is not None:
        target = int(add_authorized_user_id)
        ensure_not_super_admin(target)
        if target not in managers:
            users.add(target)

    if remove_authorized_user_ids:
        for uid in remove_authorized_user_ids:
            target = int(uid)
            ensure_not_super_admin(target)
            users.discard(target)

    if promote_manager_user_ids:
        for uid in promote_manager_user_ids:
            target = int(uid)
            ensure_not_super_admin(target)
            users.discard(target)
            managers.add(target)

    if demote_manager_user_ids:
        for uid in demote_manager_user_ids:
            target = int(uid)
            ensure_not_super_admin(target)
            managers.discard(target)
            users.add(target)

    if remove_manager_user_ids:
        for uid in remove_manager_user_ids:
            target = int(uid)
            ensure_not_super_admin(target)
            managers.discard(target)
            users.discard(target)

    if admin_user_id is not None:
        managers.discard(admin_user_id)
        users.discard(admin_user_id)
    users.difference_update(managers)
    auth_roles_save_sync(cache_path, managers, users)
    return managers, users


def update_authorized_users_in_cache_sync(cache_path: Path, admin_user_id: int | None, current_manager_user_ids: set[int], current_authorized_user_ids: set[int], add_user_id: int | None = None, remove_user_ids: set[int] | None = None) -> set[int]:
    _, users = update_telegram_roles_in_cache_sync(
        cache_path,
        admin_user_id,
        current_manager_user_ids,
        current_authorized_user_ids,
        add_authorized_user_id=add_user_id,
        remove_authorized_user_ids=remove_user_ids,
    )
    return users


def notification_status_sync(cache_path: Path, chat_id: str, default_enabled_kinds: set[str] | None = None) -> dict[str, bool]:
    init_cache(cache_path)
    default_enabled_kinds = default_enabled_kinds or set()
    with cache_connect(cache_path) as conn:
        rows = conn.execute(
            "SELECT kind, enabled FROM notification_subscriptions WHERE chat_id = ?",
 (chat_id,),
        ).fetchall()
    status = {kind: kind in default_enabled_kinds for kind in NOTIFICATION_KINDS}
    for row in rows:
        kind = str(row["kind"] or "")
        if kind in status:
            status[kind] = bool(int(row["enabled"] or 0))
    return status


def notification_ip_alert_mode_sync(cache_path: Path, chat_id: str) -> str:
    """Return ip_alert delivery mode: off/basic/advanced. Stored as enabled 0/1/2 for compatibility."""
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        row = conn.execute(
            "SELECT enabled FROM notification_subscriptions WHERE chat_id = ? AND kind = 'ip_alert'",
            (chat_id,),
        ).fetchone()
    if row is None:
        return "basic" if "ip_alert" in DEFAULT_ALLOWLIST_NOTIFICATION_KINDS else "off"
    value = int(row["enabled"] or 0)
    if value >= 2:
        return "advanced"
    if value == 1:
        return "basic"
    return "off"


def notification_ip_alert_mode_label(mode: str) -> str:
    return {"off": "关闭", "basic": "基础", "advanced": "高级"}.get(mode, "基础")


def notification_toggle_sync(cache_path: Path, chat_id: str, kind: str) -> bool | str:
    if kind not in NOTIFICATION_KINDS:
        raise ValueError("unknown notification kind")
    init_cache(cache_path)
    now_ts = int(datetime.now().timestamp())
    default_enabled = 1 if kind in DEFAULT_ALLOWLIST_NOTIFICATION_KINDS else 0
    with cache_connect(cache_path) as conn:
        row = conn.execute(
            "SELECT enabled FROM notification_subscriptions WHERE chat_id = ? AND kind = ?",
 (chat_id, kind),
        ).fetchone()
        current_enabled = int(row["enabled"] if row else default_enabled)
        if kind == "ip_alert":
            # 三段式循环：关闭(0) -> 基础(1) -> 高级(2) -> 关闭(0)
            new_enabled = {0: 1, 1: 2, 2: 0}.get(current_enabled, 0)
        else:
            new_enabled = 0 if current_enabled else 1
        conn.execute(
            """
            INSERT INTO notification_subscriptions(chat_id, kind, enabled, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id, kind) DO UPDATE SET
                enabled=excluded.enabled,
                updated_at=excluded.updated_at
            """,
 (chat_id, kind, new_enabled, now_ts),
        )
    if kind == "ip_alert":
        return "advanced" if new_enabled >= 2 else ("basic" if new_enabled == 1 else "off")
    return bool(new_enabled)


def notification_enabled_chats_sync(cache_path: Path, kind: str) -> list[str]:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        rows = conn.execute(
            "SELECT chat_id FROM notification_subscriptions WHERE kind = ? AND enabled = 1",
 (kind,),
        ).fetchall()
    return [str(row["chat_id"]) for row in rows]


def default_allowlist_notification_chats_sync(cache_path: Path, cfg: AppConfig, kind: str) -> list[str]:
    """Notifications in DEFAULT_ALLOWLIST_NOTIFICATION_KINDS are enabled for Telegram allowlist unless explicitly disabled."""
    if kind == "version_update":
        chats: list[str] = []
        for admin_uid in sorted(cfg.telegram.admin_user_ids):
            admin_chat = str(admin_uid)
            if notification_status_sync(cache_path, admin_chat, DEFAULT_ALLOWLIST_NOTIFICATION_KINDS).get(kind):
                chats.append(admin_chat)
        return chats
    if kind not in DEFAULT_ALLOWLIST_NOTIFICATION_KINDS:
        return notification_enabled_chats_sync(cache_path, kind)
    init_cache(cache_path)
    chats = {str(uid) for uid in cfg.telegram.allowed_user_ids}
    with cache_connect(cache_path) as conn:
        rows = conn.execute(
            "SELECT chat_id, enabled FROM notification_subscriptions WHERE kind = ?",
 (kind,),
        ).fetchall()
    for row in rows:
        chat_id = str(row["chat_id"])
        if int(row["enabled"] or 0):
            chats.add(chat_id)
        else:
            chats.discard(chat_id)
    return sorted(chats)


def collector_notification_chats_sync(cache_path: Path, cfg: AppConfig) -> list[str]:
    return default_allowlist_notification_chats_sync(cache_path, cfg, "collector")


def alert_notification_chats_sync(cache_path: Path, cfg: AppConfig, alert_type: str) -> list[str]:
    kind = "traffic_alert" if alert_type == "traffic" else "ip_alert"
    return default_allowlist_notification_chats_sync(cache_path, cfg, kind)


def ip_alert_notification_chat_modes_sync(cache_path: Path, cfg: AppConfig) -> dict[str, str]:
    return {chat_id: notification_ip_alert_mode_sync(cache_path, chat_id) for chat_id in alert_notification_chats_sync(cache_path, cfg, "ip")}





def alert_period_label(period: str | None) -> str:
    return ALERT_PERIOD_LABELS.get(period or ALERT_DEFAULT_PERIOD, ALERT_PERIOD_LABELS[ALERT_DEFAULT_PERIOD])


def alert_period_window(period: str | None, now: datetime | None = None) -> tuple[int, int, str]:
    now = now or datetime.now()
    period = period or ALERT_DEFAULT_PERIOD
    end_ts = int(now.timestamp())
    if period == "1h":
        start = now - timedelta(hours=1)
    elif period == "7d":
        start = now - timedelta(days=7)
    elif period == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start_day = now - timedelta(days=now.weekday())
        start = start_day.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        period = ALERT_DEFAULT_PERIOD
        start = now - timedelta(hours=24)
    return int(start.timestamp()), end_ts, alert_period_label(period)


def alert_global_period_sync(cache_path: Path, alert_type: str) -> str:
    key = "traffic_alert_global_period" if alert_type == "traffic" else "ip_alert_global_period"
    value = alert_state_get_sync(cache_path, key)
    return value if value in ALERT_PERIOD_LABELS else ALERT_DEFAULT_PERIOD


def alert_set_global_period_sync(cache_path: Path, alert_type: str, period: str) -> str:
    if period not in ALERT_PERIOD_LABELS:
        raise ValueError("unknown alert period")
    key = "traffic_alert_global_period" if alert_type == "traffic" else "ip_alert_global_period"
    alert_state_set_sync(cache_path, key, period)
    return period

def alert_global_threshold_sync(cache_path: Path, alert_type: str) -> int:
    key = "traffic_alert_global_threshold_bytes" if alert_type == "traffic" else "ip_alert_global_city_threshold"
    value = alert_state_get_sync(cache_path, key)
    if value is not None:
        try:
            parsed = int(value)
            if parsed > 0:
                return parsed
        except ValueError:
            pass
    return TRAFFIC_ALERT_DEFAULT_THRESHOLD_BYTES if alert_type == "traffic" else IP_ALERT_DEFAULT_CITY_THRESHOLD


def alert_set_global_threshold_sync(cache_path: Path, alert_type: str, value: int) -> int:
    if value <= 0:
        raise ValueError("threshold must be positive")
    key = "traffic_alert_global_threshold_bytes" if alert_type == "traffic" else "ip_alert_global_city_threshold"
    stored = value * 1024 ** 3 if alert_type == "traffic" else value
    alert_state_set_sync(cache_path, key, str(stored))
    return stored


def alert_global_setting_text_sync(cache_path: Path, alert_type: str) -> str:
    threshold = alert_global_threshold_sync(cache_path, alert_type)
    period = alert_global_period_sync(cache_path, alert_type)
    period_label = alert_period_label(period)
    if alert_type == "traffic":
        threshold_text = format_bytes(threshold)
        title = "🎚 异常告警<b>默认规则</b>"
        suffix = ""
    else:
        threshold_text = f"{threshold} 个城市"
        title = "🎚 异地登录<b>默认规则</b>"
        suffix = "\n\n" + PROXY_PROTOCOL_NOTICE
    return "\n".join([title, "────────────", f"当前规则：<b>{period_label} / {threshold_text}</b>"] ) + suffix

def alert_user_setting_sync(cache_path: Path, xboard_user_id: int) -> dict[str, Any]:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        row = conn.execute(
            """
            SELECT user_id, traffic_threshold_bytes, traffic_whitelist, traffic_period, ip_city_threshold, ip_whitelist, ip_period
            FROM alert_user_settings WHERE user_id = ?
            """,
 (xboard_user_id,),
        ).fetchone()
    if not row:
        return {
            "user_id": xboard_user_id,
            "traffic_threshold_bytes": None,
            "traffic_whitelist": 0,
            "traffic_period": None,
            "ip_city_threshold": None,
            "ip_whitelist": 0,
            "ip_period": None,
        }
    return dict(row)


def alert_upsert_setting_sync(cache_path: Path, xboard_user_id: int, **changes: Any) -> dict[str, Any]:
    init_cache(cache_path)
    current = alert_user_setting_sync(cache_path, xboard_user_id)
    current.update(changes)
    now_ts = int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        conn.execute(
            """
            INSERT INTO alert_user_settings(user_id, traffic_threshold_bytes, traffic_whitelist, traffic_period, ip_city_threshold, ip_whitelist, ip_period, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                traffic_threshold_bytes=excluded.traffic_threshold_bytes,
                traffic_whitelist=excluded.traffic_whitelist,
                traffic_period=excluded.traffic_period,
                ip_city_threshold=excluded.ip_city_threshold,
                ip_whitelist=excluded.ip_whitelist,
                ip_period=excluded.ip_period,
                updated_at=excluded.updated_at
            """,
 (
                xboard_user_id,
                current.get("traffic_threshold_bytes"),
                int(current.get("traffic_whitelist") or 0),
                current.get("traffic_period"),
                current.get("ip_city_threshold"),
                int(current.get("ip_whitelist") or 0),
                current.get("ip_period"),
                now_ts,
            ),
        )
    return alert_user_setting_sync(cache_path, xboard_user_id)


def alert_reset_setting_sync(cache_path: Path, xboard_user_id: int, alert_type: str) -> dict[str, Any]:
    if alert_type == "traffic":
        return alert_upsert_setting_sync(cache_path, xboard_user_id, traffic_threshold_bytes=None, traffic_period=None, traffic_whitelist=0)
    if alert_type == "ip":
        return alert_upsert_setting_sync(cache_path, xboard_user_id, ip_city_threshold=None, ip_period=None, ip_whitelist=0)
    raise ValueError("unknown alert type")



def alert_setting_label(setting: dict[str, Any], alert_type: str, cache_path: Path | None = None) -> str:
    if alert_type == "traffic":
        if int(setting.get("traffic_whitelist") or 0):
            return "白名单"
        period = setting.get("traffic_period") or (alert_global_period_sync(cache_path, "traffic") if cache_path else ALERT_DEFAULT_PERIOD)
        threshold = setting.get("traffic_threshold_bytes") or (alert_global_threshold_sync(cache_path, "traffic") if cache_path else TRAFFIC_ALERT_DEFAULT_THRESHOLD_BYTES)
        return f"{alert_period_label(period)} / {format_bytes(int(threshold))}"
    if int(setting.get("ip_whitelist") or 0):
        return "白名单"
    period = setting.get("ip_period") or (alert_global_period_sync(cache_path, "ip") if cache_path else ALERT_DEFAULT_PERIOD)
    threshold = setting.get("ip_city_threshold") or (alert_global_threshold_sync(cache_path, "ip") if cache_path else IP_ALERT_DEFAULT_CITY_THRESHOLD)
    return f"{alert_period_label(period)} / {int(threshold)} 个城市"


def alert_user_list_sync(cache_path: Path, alert_type: str, limit: int = 500) -> list[dict[str, Any]]:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        rows = conn.execute(
            """
            SELECT u.user_id, u.display_name, u.remarks, u.email,
                   s.traffic_threshold_bytes, s.traffic_whitelist, s.traffic_period,
                   s.ip_city_threshold, s.ip_whitelist, s.ip_period
            FROM users AS u
            LEFT JOIN alert_user_settings AS s ON s.user_id = u.user_id
            ORDER BY u.user_id ASC
            LIMIT ?
            """,
 (limit,),
        ).fetchall()
    result = []
    for row in rows:
        name = str(row["display_name"] or row["remarks"] or row["email"] or f"用户{row['user_id']}").strip()
        setting = dict(row)
        result.append({"user_id": int(row["user_id"]), "name": name, "setting_label": alert_setting_label(setting, alert_type, cache_path)})
    return result

def alert_summary_sync(cache_path: Path, alert_type: str) -> str:
    init_cache(cache_path)
    if alert_type == "traffic":
        title = "🚨 <b>异常告警</b>"
        default_line = f"默认规则：{alert_period_label(alert_global_period_sync(cache_path, 'traffic'))} / <b>{format_bytes(alert_global_threshold_sync(cache_path, 'traffic'))}</b>"
    else:
        title = "🚨 <b>异地登录</b>"
        default_line = f"默认规则：{alert_period_label(alert_global_period_sync(cache_path, 'ip'))} / <b>{alert_global_threshold_sync(cache_path, 'ip')} 个城市</b>"
    lines = [title, "────────────", default_line]
    if alert_type == "ip":
        lines.extend(["", PROXY_PROTOCOL_NOTICE])
    return "\n".join(lines)

def alert_user_setting_text_sync(cache_path: Path, alert_type: str, xboard_user_id: int) -> str:
    setting = alert_user_setting_sync(cache_path, xboard_user_id)
    name = cached_user_name_by_id(cache_path, xboard_user_id) or f"用户{xboard_user_id}"
    if alert_type == "traffic":
        whitelisted = bool(int(setting.get("traffic_whitelist") or 0))
        effective = alert_setting_label(setting, "traffic", cache_path)
        uses_independent = setting.get("traffic_threshold_bytes") is not None or setting.get("traffic_period") is not None
        if whitelisted:
            current_line = "当前适用：白名单 (不提醒)"
        else:
            current_line = f"当前适用：{'独立规则' if uses_independent else '默认规则'} (<b>{effective}</b>)"
        lines = ["🚨 <b>用户流量告警设置</b>", "────────────", f"用户：{render_user_label(xboard_user_id, name)}", current_line]
    else:
        whitelisted = bool(int(setting.get("ip_whitelist") or 0))
        effective = alert_setting_label(setting, "ip", cache_path)
        uses_independent = setting.get("ip_city_threshold") is not None or setting.get("ip_period") is not None
        if whitelisted:
            current_line = "当前适用：白名单 (不提醒)"
        else:
            current_line = f"当前适用：{'独立规则' if uses_independent else '默认规则'} (<b>{effective}</b>)"
        lines = ["🚨 <b>用户异地告警设置</b>", "────────────", f"用户：{render_user_label(xboard_user_id, name)}", current_line, "", PROXY_PROTOCOL_NOTICE]
    return "\n".join(lines)

def traffic_alert_rows_sync(cache_path: Path) -> list[dict[str, Any]]:
    init_cache(cache_path)
    now = datetime.now()
    global_period = alert_global_period_sync(cache_path, "traffic")
    global_threshold = alert_global_threshold_sync(cache_path, "traffic")
    with cache_connect(cache_path) as conn:
        users = conn.execute(
            """
            SELECT DISTINCT t.entity_id AS user_id, COALESCE(MAX(t.name), '用户' || t.entity_id) AS name,
                   s.traffic_threshold_bytes, s.traffic_whitelist, s.traffic_period
            FROM traffic_delta_samples AS t
            LEFT JOIN alert_user_settings AS s ON s.user_id = t.entity_id
            WHERE t.kind = 'user'
            GROUP BY t.entity_id
            """
        ).fetchall()
        alerts = []
        for row in users:
            if int(row["traffic_whitelist"] or 0):
                continue
            period = row["traffic_period"] or global_period
            start_ts, end_ts, period_label = alert_period_window(period, now)
            total_row = conn.execute(
                """
                SELECT COALESCE(SUM(delta), 0) AS total
                FROM traffic_delta_samples
                WHERE kind = 'user' AND entity_id = ? AND sampled_at BETWEEN ? AND ?
                """,
 (int(row["user_id"]), start_ts, end_ts),
            ).fetchone()
            threshold = int(row["traffic_threshold_bytes"] or global_threshold)
            total = int(total_row["total"] or 0)
            rule_type = "独立规则" if row["traffic_threshold_bytes"] is not None or row["traffic_period"] is not None else "默认规则"
            if total > threshold:
                alerts.append({"user_id": int(row["user_id"]), "name": str(row["name"] or ""), "total": total, "threshold": threshold, "period": period, "period_label": period_label, "rule_type": rule_type})
    return alerts

def ip_alert_rows_sync(cache_path: Path) -> list[dict[str, Any]]:
    init_cache(cache_path)
    now = datetime.now()
    global_period = alert_global_period_sync(cache_path, "ip")
    global_threshold = alert_global_threshold_sync(cache_path, "ip")
    with cache_connect(cache_path) as conn:
        users = conn.execute(
            """
            SELECT DISTINCT a.user_id, COALESCE(MAX(u.display_name), MAX(u.remarks), MAX(u.email), '用户' || a.user_id) AS name,
                   s.ip_city_threshold, s.ip_whitelist, s.ip_period
            FROM active_ip_records AS a
            LEFT JOIN users AS u ON u.user_id = a.user_id
            LEFT JOIN alert_user_settings AS s ON s.user_id = a.user_id
            WHERE a.ignored_at IS NULL
            GROUP BY a.user_id
            """
        ).fetchall()
        alerts = []
        for row in users:
            if int(row["ip_whitelist"] or 0):
                continue
            period = row["ip_period"] or global_period
            start_ts, end_ts, period_label = alert_period_window(period, now)
            detail = conn.execute(
                """
                SELECT COUNT(DISTINCT COALESCE(NULLIF(g.stat_area_key, ''), NULLIF(g.city, ''), NULLIF(g.region, ''), NULLIF(g.country, ''))) AS city_count,
                       GROUP_CONCAT(DISTINCT COALESCE(NULLIF(g.stat_area_name, ''), NULLIF(g.city, ''), NULLIF(g.region, ''), NULLIF(g.country, ''))) AS cities
                FROM active_ip_records AS a
                LEFT JOIN ip_geo_cache AS g ON g.ip = a.ip
                WHERE a.user_id = ? AND a.ignored_at IS NULL AND a.last_seen_at BETWEEN ? AND ?
                """,
 (int(row["user_id"]), start_ts, end_ts),
            ).fetchone()
            threshold = int(row["ip_city_threshold"] or global_threshold)
            city_count = int(detail["city_count"] or 0)
            rule_type = "独立规则" if row["ip_city_threshold"] is not None or row["ip_period"] is not None else "默认规则"
            if city_count > threshold:
                cities = [c for c in str(detail["cities"] or "").split(",") if c]
                alerts.append({"user_id": int(row["user_id"]), "name": str(row["name"] or ""), "city_count": city_count, "threshold": threshold, "period": period, "period_label": period_label, "cities": cities[:12], "rule_type": rule_type})
    return alerts

def alert_effective_rule_detail_for_user_sync(cache_path: Path, alert_type: str, user_id: int) -> tuple[str, str, int, str]:
    init_cache(cache_path)
    global_period = alert_global_period_sync(cache_path, alert_type)
    global_threshold = alert_global_threshold_sync(cache_path, alert_type)
    with cache_connect(cache_path) as conn:
        row = conn.execute("SELECT * FROM alert_user_settings WHERE user_id = ?", (int(user_id),)).fetchone()
    if alert_type == "traffic":
        custom_threshold = row["traffic_threshold_bytes"] if row else None
        custom_period = row["traffic_period"] if row else None
    else:
        custom_threshold = row["ip_city_threshold"] if row else None
        custom_period = row["ip_period"] if row else None
    period = custom_period or global_period
    threshold = int(custom_threshold or global_threshold)
    rule_type = "独立规则" if custom_threshold is not None or custom_period is not None else "默认规则"
    return period, alert_period_label(period), threshold, rule_type


def alert_effective_rule_for_user_sync(cache_path: Path, alert_type: str, user_id: int) -> tuple[str, int, str]:
    _, period_label, threshold, rule_type = alert_effective_rule_detail_for_user_sync(cache_path, alert_type, user_id)
    return period_label, threshold, rule_type


def current_traffic_alert_value_for_user_sync(cache_path: Path, user_id: int, period: str) -> int:
    start_ts, end_ts, _ = alert_period_window(period, datetime.now())
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(delta), 0) AS total
            FROM traffic_delta_samples
            WHERE kind = 'user' AND entity_id = ? AND sampled_at BETWEEN ? AND ?
            """,
            (int(user_id), start_ts, end_ts),
        ).fetchone()
    return int(row["total"] or 0) if row else 0


def current_ip_alert_detail_for_user_sync(cache_path: Path, user_id: int, period: str) -> tuple[int, list[str]]:
    start_ts, end_ts, _ = alert_period_window(period, datetime.now())
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(DISTINCT COALESCE(NULLIF(g.stat_area_key, ''), NULLIF(g.city, ''), NULLIF(g.region, ''), NULLIF(g.country, ''))) AS city_count,
                   GROUP_CONCAT(DISTINCT COALESCE(NULLIF(g.stat_area_name, ''), NULLIF(g.city, ''), NULLIF(g.region, ''), NULLIF(g.country, ''))) AS cities
            FROM active_ip_records AS a
            LEFT JOIN ip_geo_cache AS g ON g.ip = a.ip
            WHERE a.user_id = ? AND a.ignored_at IS NULL AND a.last_seen_at BETWEEN ? AND ?
            """,
            (int(user_id), start_ts, end_ts),
        ).fetchone()
    if not row:
        return 0, []
    cities = [c for c in str(row["cities"] or "").split(",") if c]
    return int(row["city_count"] or 0), cities[:12]


def alert_state_get_sync(cache_path: Path, key: str) -> str | None:
    state = get_collector_state_sync(cache_path, key)
    return state[0] if state else None


def alert_state_set_sync(cache_path: Path, key: str, value: str) -> None:
    init_cache(cache_path)
    now_ts = int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        set_collector_state(conn, key, value, now_ts)


def alert_state_delete_sync(cache_path: Path, key: str) -> None:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        conn.execute("DELETE FROM collector_state WHERE key = ?", (key,))

def traffic_report_sent_key(kind: str, period_start: int, period_end: int, chat_id: str) -> str:
    return f"traffic_report_sent:{kind}:{period_start}:{period_end}:{chat_id}"


def mark_traffic_report_sent_sync(cache_path: Path, kind: str, period_start: int, period_end: int, chat_id: str) -> None:
    init_cache(cache_path)
    now_ts = int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        set_collector_state(
            conn,
            traffic_report_sent_key(kind, period_start, period_end, chat_id),
            "1",
            now_ts,
        )


def collector_health_key(service: str) -> str:
    return f"collector_health:{service}"


def set_collector_health_status_sync(cache_path: Path, service: str, ok: bool, detail: str = "") -> tuple[str | None, str]:
    """Store health status and return (previous_status, current_status)."""
    init_cache(cache_path)
    now_ts = int(datetime.now().timestamp())
    status = "ok" if ok else "fail"
    payload = json.dumps({"status": status, "detail": detail, "updated_at": now_ts}, ensure_ascii=False)
    with cache_connect(cache_path) as conn:
        row = conn.execute(
            "SELECT value FROM collector_state WHERE key = ?",
 (collector_health_key(service),),
        ).fetchone()
        previous_status: str | None = None
        if row:
            try:
                previous_status = str(json.loads(str(row["value"] or "{}")).get("status") or "") or None
            except json.JSONDecodeError:
                previous_status = str(row["value"] or "") or None
        set_collector_state(conn, collector_health_key(service), payload, now_ts)
    return previous_status, status


def redact_sensitive_text_for_non_admin(text: str) -> str:
    """Remove host/IP/port-like details from messages shown to non-admin Telegram users."""
    if not text:
        return text
    redacted = text
    redacted = re.sub(r"https?://[^\s<]+", "[已隐藏URL]", redacted)
    redacted = re.sub(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?", "[已隐藏IP]", redacted)
    redacted = re.sub(r"\[[0-9a-fA-F:]+\](?::\d{1,5})?", "[已隐藏IP]", redacted)
    redacted = re.sub(r"\b[0-9a-fA-F]{0,4}:[0-9a-fA-F:]{2,}(?::\d{1,5})?\b", "[已隐藏IP]", redacted)
    redacted = re.sub(r"(?i)\b([a-z0-9-]+\.)+[a-z]{2,}(?::\d{1,5})?\b", "[已隐藏主机]", redacted)
    redacted = re.sub(r"(?i)\b(host|hostname|server|addr|address|endpoint)\s*[:=]\s*[^\s，。；;]+", r"\1=[已隐藏]", redacted)
    redacted = re.sub(r"(?i)\b(port)\s*[:=]\s*\d{1,5}\b", r"\1=[已隐藏]", redacted)
    redacted = re.sub(r"端口\s*[:：]?\s*\d{1,5}", "端口：[已隐藏]", redacted)
    return redacted


def format_collector_health_alert(service: str, recovered: bool, detail: str = "", admin_view: bool = True) -> str:
    title = "✅ <b>采集异常恢复</b>" if recovered else "⚠️ <b>采集异常</b>"
    status_line = "状态：已恢复" if recovered else "状态：异常"
    lines = [
        title,
        "────────────",
        f"服务：{html.escape(COLLECTOR_HEALTH_SERVICES.get(service, service))}",
        status_line,
        f"时间：{format_timestamp(int(datetime.now().timestamp()))}",
    ]
    if detail:
        safe_detail = detail if admin_view else redact_sensitive_text_for_non_admin(detail)
        lines.extend(["", f"详情：{html.escape(safe_detail)[:500]}"])
    if not admin_view:
        lines.extend(["", "敏感连接信息已隐藏，仅管理员可查看完整详情。"])
    return "\n".join(lines)


def format_collector_gap_alert(previous_ts: int, current_ts: int, gap_seconds: int) -> str:
    return "\n".join([
        "✅ <b>采集异常恢复</b>",
        "────────────",
        "检测到 Bot 已恢复运行，但中断期间未能完成流量采样。",
        "",
        f"中断开始：{format_timestamp(previous_ts)}",
        f"恢复采样：{format_timestamp(current_ts)}",
        f"影响时长：{format_duration(gap_seconds)}",
    ])


def traffic_report_already_sent_sync(cache_path: Path, kind: str, period_start: int, period_end: int, chat_id: str) -> bool:
    return get_collector_state_sync(cache_path, traffic_report_sent_key(kind, period_start, period_end, chat_id)) is not None


def beijing_now() -> datetime:
    return datetime.now(BEIJING_TZ)


def beijing_midnight(dt: datetime) -> datetime:
    return dt.astimezone(BEIJING_TZ).replace(hour=0, minute=0, second=0, microsecond=0)


def traffic_report_window(kind: str, now: datetime | None = None) -> tuple[int, int, str]:
    current = now.astimezone(BEIJING_TZ) if now else beijing_now()
    today = beijing_midnight(current)
    if kind == "daily":
        start = today - timedelta(days=1)
        end = today
        label = f"昨天 {start.strftime('%Y-%m-%d')} 00:00 - 24:00 (北京时间)"
    elif kind == "weekly":
        this_week_start = today - timedelta(days=today.weekday())
        start = this_week_start - timedelta(days=7)
        end = this_week_start
        label = f"上周 {start.strftime('%Y-%m-%d')} - {(end - timedelta(seconds=1)).strftime('%Y-%m-%d')} (周一至周日，北京时间)"
    elif kind == "monthly":
        this_month_start = today.replace(day=1)
        last_month_end = this_month_start - timedelta(seconds=1)
        start = last_month_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = this_month_start
        label = f"上月 {start.strftime('%Y-%m')} (北京时间)"
    else:
        raise ValueError("unknown report kind")
    return int(start.timestamp()), int(end.timestamp()) - 1, label


def get_stats_floor_ts_sync(cache_path: Path) -> int | None:
    state = get_collector_state_sync(cache_path, "stats_floor_at")
    if not state:
        return None
    try:
        value = int(state[0])
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def effective_cache_cutoff_ts_sync(cache_path: Path, retention_days: int) -> int:
    if retention_days <= 0:
        return get_stats_floor_ts_sync(cache_path) or 0
    retention_cutoff = int((datetime.now() - timedelta(days=retention_days)).timestamp())
    stats_floor = get_stats_floor_ts_sync(cache_path)
    return max(retention_cutoff, stats_floor or 0)


def cache_retention_days_sync(cache_path: Path) -> int:
    value = alert_state_get_sync(cache_path, "cache_retention_days")
    if value is not None:
        try:
            parsed = int(value)
            if parsed >= 0:
                return parsed
        except ValueError:
            pass
    return DEFAULT_CACHE_RETENTION_DAYS


def cache_retention_option_key(days: int) -> str:
    for key, (option_days, _) in CACHE_RETENTION_OPTIONS.items():
        if int(days) == int(option_days):
            return key
    return "1m"


def cache_retention_label(days: int) -> str:
    return CACHE_RETENTION_OPTIONS.get(cache_retention_option_key(days), CACHE_RETENTION_OPTIONS["1m"])[1]


def cache_retention_cutoff_ts(days: int) -> int:
    if days <= 0:
        return 0
    return int((datetime.now() - timedelta(days=days)).timestamp())


def cache_retention_preview_sync(cache_path: Path, days: int) -> dict[str, int]:
    init_cache(cache_path)
    cutoff_ts = cache_retention_cutoff_ts(days)
    with cache_connect(cache_path) as conn:
        if cutoff_ts <= 0:
            counts = {"traffic_delta_samples": 0, "traffic_sample_gaps": 0, "traffic_ranges": 0, "active_ip_records": 0, "ip_geo_cache": 0}
        else:
            counts = {
                "traffic_delta_samples": int(conn.execute("SELECT COUNT(*) FROM traffic_delta_samples WHERE sampled_at < ?", (cutoff_ts,)).fetchone()[0] or 0),
                "traffic_sample_gaps": int(conn.execute("SELECT COUNT(*) FROM traffic_sample_gaps WHERE gap_end_at < ?", (cutoff_ts,)).fetchone()[0] or 0),
                "traffic_ranges": int(conn.execute("SELECT COUNT(*) FROM traffic_ranges WHERE end_ts < ?", (cutoff_ts,)).fetchone()[0] or 0),
                "active_ip_records": int(conn.execute("SELECT COUNT(*) FROM active_ip_records WHERE last_seen_at < ?", (cutoff_ts,)).fetchone()[0] or 0),
                "ip_geo_cache": int(conn.execute("""
                    SELECT COUNT(*) FROM ip_geo_cache
                    WHERE ip NOT IN (SELECT DISTINCT ip FROM active_ip_records WHERE last_seen_at >= ?)
                      AND (queried_at = 0 OR queried_at < ?)
                """, (cutoff_ts, cutoff_ts)).fetchone()[0] or 0),
            }
        counts["cutoff_ts"] = cutoff_ts
        return counts


def cache_retention_set_and_prune_sync(cache_path: Path, days: int) -> dict[str, int]:
    init_cache(cache_path)
    now_ts = int(datetime.now().timestamp())
    cutoff_ts = cache_retention_cutoff_ts(days)
    with cache_connect(cache_path) as conn:
        if cutoff_ts <= 0:
            counts = {"traffic_delta_samples": 0, "traffic_sample_gaps": 0, "traffic_ranges": 0, "active_ip_records": 0, "ip_geo_cache": 0}
        else:
            counts = {
                "traffic_delta_samples": int(conn.execute("SELECT COUNT(*) FROM traffic_delta_samples WHERE sampled_at < ?", (cutoff_ts,)).fetchone()[0] or 0),
                "traffic_sample_gaps": int(conn.execute("SELECT COUNT(*) FROM traffic_sample_gaps WHERE gap_end_at < ?", (cutoff_ts,)).fetchone()[0] or 0),
                "traffic_ranges": int(conn.execute("SELECT COUNT(*) FROM traffic_ranges WHERE end_ts < ?", (cutoff_ts,)).fetchone()[0] or 0),
                "active_ip_records": int(conn.execute("SELECT COUNT(*) FROM active_ip_records WHERE last_seen_at < ?", (cutoff_ts,)).fetchone()[0] or 0),
                "ip_geo_cache": int(conn.execute("""
                    SELECT COUNT(*) FROM ip_geo_cache
                    WHERE ip NOT IN (SELECT DISTINCT ip FROM active_ip_records WHERE last_seen_at >= ?)
                      AND (queried_at = 0 OR queried_at < ?)
                """, (cutoff_ts, cutoff_ts)).fetchone()[0] or 0),
            }
            conn.execute("DELETE FROM traffic_delta_samples WHERE sampled_at < ?", (cutoff_ts,))
            conn.execute("DELETE FROM traffic_sample_gaps WHERE gap_end_at < ?", (cutoff_ts,))
            conn.execute("DELETE FROM traffic_ranges WHERE end_ts < ?", (cutoff_ts,))
            conn.execute("DELETE FROM active_ip_records WHERE last_seen_at < ?", (cutoff_ts,))
            conn.execute("""
                DELETE FROM ip_geo_cache
                WHERE ip NOT IN (SELECT DISTINCT ip FROM active_ip_records WHERE last_seen_at >= ?)
                  AND (queried_at = 0 OR queried_at < ?)
            """, (cutoff_ts, cutoff_ts))
        set_collector_state(conn, "cache_retention_days", str(int(days)), now_ts)
        set_collector_state(conn, "last_cleanup_at", str(now_ts), now_ts)
    counts["cutoff_ts"] = cutoff_ts
    return counts


def prune_stats_before_sync(cache_path: Path, floor_ts: int) -> dict[str, int]:
    """Set local statistics floor and delete cached rows before it."""
    init_cache(cache_path)
    now_ts = int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        counts = {
            "traffic_delta_samples": int(conn.execute("SELECT COUNT(*) FROM traffic_delta_samples WHERE sampled_at < ?", (floor_ts,)).fetchone()[0] or 0),
            "traffic_sample_gaps": int(conn.execute("SELECT COUNT(*) FROM traffic_sample_gaps WHERE gap_end_at < ?", (floor_ts,)).fetchone()[0] or 0),
            "traffic_ranges": int(conn.execute("SELECT COUNT(*) FROM traffic_ranges WHERE end_ts < ?", (floor_ts,)).fetchone()[0] or 0),
            "active_ip_records": int(conn.execute("SELECT COUNT(*) FROM active_ip_records WHERE last_seen_at < ?", (floor_ts,)).fetchone()[0] or 0),
        }
        conn.execute("DELETE FROM traffic_delta_samples WHERE sampled_at < ?", (floor_ts,))
        conn.execute("DELETE FROM traffic_sample_gaps WHERE gap_end_at < ?", (floor_ts,))
        conn.execute("DELETE FROM traffic_ranges WHERE end_ts < ?", (floor_ts,))
        conn.execute("DELETE FROM active_ip_records WHERE last_seen_at < ?", (floor_ts,))
        set_collector_state(conn, "stats_floor_at", str(floor_ts), now_ts)
    return counts


def preview_prune_stats_before_sync(cache_path: Path, floor_ts: int) -> dict[str, int]:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        return {
            "traffic_delta_samples": int(conn.execute("SELECT COUNT(*) FROM traffic_delta_samples WHERE sampled_at < ?", (floor_ts,)).fetchone()[0] or 0),
            "traffic_sample_gaps": int(conn.execute("SELECT COUNT(*) FROM traffic_sample_gaps WHERE gap_end_at < ?", (floor_ts,)).fetchone()[0] or 0),
            "traffic_ranges": int(conn.execute("SELECT COUNT(*) FROM traffic_ranges WHERE end_ts < ?", (floor_ts,)).fetchone()[0] or 0),
            "active_ip_records": int(conn.execute("SELECT COUNT(*) FROM active_ip_records WHERE last_seen_at < ?", (floor_ts,)).fetchone()[0] or 0),
        }


def preview_clear_active_ip_records_sync(cache_path: Path) -> dict[str, int]:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS records,
                   COUNT(DISTINCT user_id) AS users,
                   COUNT(DISTINCT ip) AS ips,
                   MIN(first_seen_at) AS first_seen,
                   MAX(last_seen_at) AS last_seen
            FROM active_ip_records
            """
        ).fetchone() or {}
        geo_rows = conn.execute(
            """
            SELECT COUNT(*) AS geo_records
            FROM ip_geo_cache
            WHERE ip IN (SELECT DISTINCT ip FROM active_ip_records)
            """
        ).fetchone() or {}
    return {
        "records": int(row["records"] or 0),
        "users": int(row["users"] or 0),
        "ips": int(row["ips"] or 0),
        "geo_records": int(geo_rows["geo_records"] or 0),
        "first_seen": int(row["first_seen"] or 0),
        "last_seen": int(row["last_seen"] or 0),
    }


def clear_active_ip_records_sync(cache_path: Path) -> dict[str, int]:
    init_cache(cache_path)
    stats = preview_clear_active_ip_records_sync(cache_path)
    now_ts = int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        conn.execute(
            """
            DELETE FROM ip_geo_cache
            WHERE ip IN (SELECT DISTINCT ip FROM active_ip_records)
            """
        )
        conn.execute("DELETE FROM active_ip_records")
        set_collector_state(conn, "last_active_ip_records_cleared_at", str(now_ts), now_ts)
    return stats


def reset_local_cache_sync(cache_path: Path) -> dict[str, int]:
    """Clear local Bot cache/samples while preserving UI preferences."""
    init_cache(cache_path)
    now_ts = int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        counts = {
            "active_ip_records": int(conn.execute("SELECT COUNT(*) FROM active_ip_records").fetchone()[0] or 0),
            "ip_geo_cache": int(conn.execute("SELECT COUNT(*) FROM ip_geo_cache").fetchone()[0] or 0),
            "users": int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] or 0),
            "traffic_delta_samples": int(conn.execute("SELECT COUNT(*) FROM traffic_delta_samples").fetchone()[0] or 0),
            "traffic_sample_gaps": int(conn.execute("SELECT COUNT(*) FROM traffic_sample_gaps").fetchone()[0] or 0),
            "traffic_ranges": int(conn.execute("SELECT COUNT(*) FROM traffic_ranges").fetchone()[0] or 0),
            "pinned_dashboard_messages": int(conn.execute("SELECT COUNT(*) FROM pinned_dashboard_messages").fetchone()[0] or 0),
        }
        for table in (
            "active_ip_records",
            "ip_geo_cache",
            "users",
            "traffic_delta_samples",
            "traffic_sample_gaps",
            "traffic_ranges",
            "pinned_dashboard_messages",
        ):
            conn.execute(f"DELETE FROM {table}")
        conn.execute(
            "DELETE FROM collector_state WHERE key IN ('first_collect_at', 'last_collect_at', 'last_traffic_sample_at', 'stats_floor_at', 'last_active_ip_records_cleared_at')"
        )
        set_collector_state(conn, "cache_reset_at", str(now_ts), now_ts)
    return counts


def list_all_cached_user_buttons_sync(cache_path: Path) -> list[tuple[int, str]]:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        rows = conn.execute(
            """
            SELECT user_id, display_name, remarks, email
            FROM users
            ORDER BY user_id ASC
            """
        ).fetchall()
    return [(int(row["user_id"]), cached_user_button_label(row, int(row["user_id"]))) for row in rows]


def preview_clear_user_ip_records_multi_sync(cache_path: Path, user_ids: list[int]) -> dict[str, Any]:
    init_cache(cache_path)
    clean_ids = sorted({int(uid) for uid in user_ids if int(uid) > 0})
    if not clean_ids:
        return {"users": 0, "records": 0, "ips": 0, "first_seen": None, "last_seen": None, "labels": []}
    placeholders = ",".join("?" for _ in clean_ids)
    with cache_connect(cache_path) as conn:
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS records, COUNT(DISTINCT ip) AS ips,
                   MIN(first_seen_at) AS first_seen, MAX(last_seen_at) AS last_seen
            FROM active_ip_records
            WHERE user_id IN ({placeholders}) AND ignored_at IS NULL
            """,
            clean_ids,
        ).fetchone()
        user_rows = conn.execute(
            f"""
            SELECT user_id, display_name, remarks, email
            FROM users
            WHERE user_id IN ({placeholders})
            ORDER BY user_id ASC
            """,
            clean_ids,
        ).fetchall()
    labels = [cached_user_button_label(r, int(r["user_id"])) for r in user_rows]
    return {
        "users": len(clean_ids),
        "records": int(row["records"] or 0),
        "ips": int(row["ips"] or 0),
        "first_seen": int(row["first_seen"] or 0) or None,
        "last_seen": int(row["last_seen"] or 0) or None,
        "labels": labels,
    }


def clear_user_ip_records_multi_sync(cache_path: Path, user_ids: list[int]) -> dict[str, Any]:
    stats = preview_clear_user_ip_records_multi_sync(cache_path, user_ids)
    clean_ids = sorted({int(uid) for uid in user_ids if int(uid) > 0})
    if not clean_ids:
        return stats
    placeholders = ",".join("?" for _ in clean_ids)
    now_ts = int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        cursor = conn.execute(
            f"""
            UPDATE active_ip_records
            SET ignored_at = ?, ignore_reason = ?, ignore_note = ?
            WHERE user_id IN ({placeholders}) AND ignored_at IS NULL
            """,
            [now_ts, "debug_reset_user_ip", "调试功能：清空用户 IP 记录", *clean_ids],
        )
        active_ips = int(conn.execute("SELECT COUNT(*) FROM active_ip_records WHERE ignored_at IS NULL").fetchone()[0] or 0)
        previous_row = conn.execute("SELECT value FROM collector_state WHERE key = ?", ("ip_alert_active_users",)).fetchone()
        previous_raw = str(previous_row["value"]) if previous_row else "{}"
        try:
            previous = json.loads(previous_raw)
            if isinstance(previous, dict):
                for user_id_value in clean_ids:
                    previous.pop(str(user_id_value), None)
                set_collector_state(conn, "ip_alert_active_users", json.dumps(previous, sort_keys=True), now_ts)
        except (TypeError, ValueError):
            pass
        set_collector_state(conn, "last_active_ip_records_cleared_at", str(now_ts), now_ts)
    stats["remaining_active_ips"] = active_ips
    stats["ignored"] = int(cursor.rowcount or 0)
    return stats


def get_cache_counts_sync(cache_path: Path) -> dict[str, int]:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        active_ips = int(conn.execute("SELECT COUNT(*) FROM active_ip_records WHERE ignored_at IS NULL").fetchone()[0] or 0)
        users = int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] or 0)
        geo_total = int(conn.execute("SELECT COUNT(*) FROM ip_geo_cache").fetchone()[0] or 0)
        traffic_samples = int(conn.execute("SELECT COUNT(*) FROM traffic_delta_samples").fetchone()[0] or 0)
        pinned_dashboards = int(conn.execute("SELECT COUNT(*) FROM pinned_dashboard_messages").fetchone()[0] or 0)
    return {
        "active_ips": active_ips,
        "users": users,
        "geo_total": geo_total,
        "traffic_samples": traffic_samples,
        "pinned_dashboards": pinned_dashboards,
    }


def collect_redis_ip_records_sync(cfg: RedisConfig) -> list[tuple[int, str, int, int, str]] | str:
    """Collect Redis Heki/Soga IP records as (user_id, ip, last_seen_ts, ttl, source_key).

    Heki writes heki:ip:<user_id>:<ip> keys. Current Soga writes
    soga_conn_<user_id>_<ip> keys for connection/device-limit records.
    Mixed Heki + Soga deployments are collected into the same local cache.
    """
    if redis_config_missing(cfg):
        return "Redis 连接信息未输入完整"

    client = redis_client(cfg)
    records: list[tuple[int, str, int, int, str]] = []
    try:
        client.ping()
        # Soga does not mirror Heki's heki:ip:<user_id>:<ip> format by replacing
        # "heki" with "soga". Soga 2.13.x writes Redis connection-limit records as
        # soga_conn_<user_id>_<ip> when device/IP limiting is enabled.
        for _, pattern, key_regex in ONLINE_IP_KEY_SPECS:
            key_batch: list[Any] = []

            def flush_key_batch() -> None:
                if not key_batch:
                    return
                pipe = client.pipeline(transaction=False)
                for redis_key in key_batch:
                    pipe.ttl(redis_key)
                ttls = pipe.execute()
                for redis_key, ttl in zip(key_batch, ttls):
                    key_text = str(redis_key)
                    match = re.fullmatch(key_regex, key_text)
                    if not match:
                        continue
                    last_seen = last_seen_from_ttl(int(ttl))
                    if last_seen is None:
                        continue
                    records.append((
                        int(match.group(1)),
                        match.group(2),
                        int(last_seen.timestamp()),
                        int(ttl),
                        key_text,
                    ))
                key_batch.clear()

            for key in client.scan_iter(match=pattern, count=1000):
                key_text = str(key)
                if not re.fullmatch(key_regex, key_text):
                    continue
                key_batch.append(key)
                if len(key_batch) >= 500:
                    flush_key_batch()
            flush_key_batch()
    except RedisError as exc:
        return redis_failure_message(exc)
    finally:
        client.close()
    return records


def upsert_cache_records(cache_path: Path, records: list[tuple[int, str, int, int, str]], retention_days: int) -> set[int]:
    now_ts = int(datetime.now().timestamp())
    cutoff_ts = effective_cache_cutoff_ts_sync(cache_path, retention_days)
    user_ids = {user_id for user_id, *_ in records}
    ips = {ip for _, ip, *_ in records}
    with cache_connect(cache_path) as conn:
        first_state = conn.execute("SELECT value FROM collector_state WHERE key = ?", ("first_collect_at",)).fetchone()
        if not first_state:
            set_collector_state(conn, "first_collect_at", str(now_ts), now_ts)
        conn.executemany(
            """
            INSERT INTO active_ip_records(user_id, ip, first_seen_at, last_seen_at, last_ttl, source_key)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, ip) DO UPDATE SET
                last_seen_at = MAX(active_ip_records.last_seen_at, excluded.last_seen_at),
                last_ttl = excluded.last_ttl,
                source_key = excluded.source_key
            """,
 ((user_id, ip, last_seen_ts, last_seen_ts, ttl, source_key) for user_id, ip, last_seen_ts, ttl, source_key in records),
        )
        conn.executemany(
            """
            INSERT INTO ip_geo_cache(ip, queried_at)
            VALUES (?, 0)
            ON CONFLICT(ip) DO NOTHING
            """,
 ((ip,) for ip in ips),
        )
        apply_ignored_rules_conn(conn, now_ts)
        conn.execute("DELETE FROM active_ip_records WHERE last_seen_at < ?", (cutoff_ts,))
        set_collector_state(conn, "last_collect_at", str(now_ts), now_ts)
        set_collector_state(conn, "last_collect_attempt_at", str(now_ts), now_ts)
        set_collector_state(conn, "last_cleanup_at", str(now_ts), now_ts)
    return user_ids


def upsert_cache_users(cache_path: Path, mysql_cfg: MySQLConfig, user_ids: set[int]) -> None:
    if not user_ids:
        return
    names = fetch_user_display_details_sync(mysql_cfg, user_ids)
    now_ts = int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        conn.executemany(
            """
            INSERT INTO users(user_id, display_name, remarks, email, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                display_name=excluded.display_name,
                remarks=excluded.remarks,
                email=excluded.email,
                updated_at=excluded.updated_at
            """,
 ((user_id, row["display_name"], row["remarks"], row["email"], now_ts) for user_id, row in names.items()),
        )


def run_cache_collection_once(cfg: AppConfig, cache_path: Path) -> tuple[bool, str, bool, str, int, int, int]:
    init_cache(cache_path)
    records = collect_redis_ip_records_sync(cfg.redis)
    if isinstance(records, str):
        log.warning("缓存采集 Redis 失败：%s", records)
        return False, records, True, "", 0, 0, 0
    user_ids = upsert_cache_records(cache_path, records, cache_retention_days_sync(cache_path))
    mysql_ok = True
    mysql_detail = ""
    try:
        upsert_cache_users(cache_path, cfg.mysql, user_ids)
    except MySQLError as exc:
        log.warning("缓存采集 MySQL 用户信息失败：%s", exc)
        mysql_ok = False
        mysql_detail = f"{type(exc).__name__}: {exc}"

    # 采集器发现新 IP 后自动补全归属地，不依赖前台查询或手动 /init。
    # 默认 collector_interval_seconds=60、ip_geo_queries_per_minute=30，即每轮最多自动查 30 个。
    # 如果短时间新增量超过免费 API 安全速率，剩余 pending 会在后续采集轮次继续自动补全。
    geo_limit = max(1, int(cfg.ip_geo_queries_per_minute * max(5.0, cfg.collector_interval_seconds) / 60))
    geo_total, geo_success, geo_failed, _ = backfill_geo_pending_rate_limited(
        cache_path,
        limit=geo_limit,
        queries_per_minute=cfg.ip_geo_queries_per_minute,
        stop_when_rate_limited=True,
    )
    if geo_total:
        pending_after = cache_geo_status_sync(cache_path)["geo_pending"]
        log.info(
            "后台 IP 归属地自动补全：本轮待处理 %s 个，成功 %s 个，失败 %s 个，剩余 %s 个",
            geo_total, geo_success, geo_failed, pending_after,
        )
    log.info("缓存采集完成：Redis IP 记录 %s 条，用户 %s 个", len(records), len(user_ids))
    return True, "", mysql_ok, mysql_detail, geo_total, geo_success, geo_failed


async def send_collector_alert(app: Application, cfg: AppConfig, cache_path: Path, service: str, ok: bool, detail: str = "") -> None:
    chats = await asyncio.to_thread(collector_notification_chats_sync, cache_path, cfg)
    for chat_id in chats:
        try:
            admin_view = is_admin_user_id(int(chat_id), cfg) if str(chat_id).lstrip("-").isdigit() else False
            text = format_collector_health_alert(service, recovered=ok, detail=detail, admin_view=admin_view)
            await app.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
        except Exception as exc:
            log.warning("发送采集异常通知失败 chat=%s：%s", chat_id, exc)


async def send_collector_text_alert(app: Application, cfg: AppConfig, cache_path: Path, text: str) -> None:
    chats = await asyncio.to_thread(collector_notification_chats_sync, cache_path, cfg)
    for chat_id in chats:
        try:
            admin_view = is_admin_user_id(int(chat_id), cfg) if str(chat_id).lstrip("-").isdigit() else False
            safe_text = text if admin_view else redact_sensitive_text_for_non_admin(text) + "\n\n敏感连接信息已隐藏，仅管理员可查看完整详情。"
            await app.bot.send_message(chat_id=chat_id, text=safe_text, parse_mode="HTML")
        except Exception as exc:
            log.warning("发送采集异常通知失败 chat=%s：%s", chat_id, exc)


async def notify_collector_health_transition(
    app: Application,
    cfg: AppConfig,
    cache_path: Path,
    service: str,
    ok: bool,
    detail: str = "",
) -> None:
    previous_status, current_status = await asyncio.to_thread(set_collector_health_status_sync, cache_path, service, ok, detail)
    if previous_status == current_status:
        return
    if previous_status is None and ok:
        return
    await send_collector_alert(app, cfg, cache_path, service, ok, detail)


async def cache_collector_loop(app: Application, cfg: AppConfig, cache_path: Path, stop_event: asyncio.Event) -> None:
    """Run Redis/MySQL -> SQLite cache collection immediately, then periodically."""
    while not stop_event.is_set():
        try:
            redis_ok, redis_detail, mysql_ok, mysql_detail, geo_total, geo_success, geo_failed = await asyncio.to_thread(run_cache_collection_once, cfg, cache_path)
            await notify_collector_health_transition(app, cfg, cache_path, "redis", redis_ok, redis_detail or "Redis 缓存采集已恢复成功。")
            # Redis 失败时，本轮不会继续检查 MySQL 用户信息；不要把“未检查”误判成 MySQL 恢复，
            # 否则会和流量采样循环的 MySQL 失败状态互相覆盖，造成“失败-恢复”反复通知。
            if redis_ok or mysql_detail:
                await notify_collector_health_transition(app, cfg, cache_path, "mysql", mysql_ok, mysql_detail or "MySQL 用户信息采集已恢复成功。")
            if geo_success:
                await notify_collector_health_transition(app, cfg, cache_path, "ip_api", True, "IP-API 已恢复响应，本轮已有 IP 归属地补全成功。")
            elif geo_failed:
                await notify_collector_health_transition(app, cfg, cache_path, "ip_api", False, f"本轮 IP 归属地补全失败 {geo_failed} 个。")
            await check_ip_alerts(app, cfg, cache_path)
        except Exception as exc:
            log.exception("缓存采集任务异常：%s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=max(5.0, cfg.collector_interval_seconds))
        except asyncio.TimeoutError:
            continue


async def send_user_alert_to_chats(app: Application, chat_ids: list[str], text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    for chat_id in chat_ids:
        try:
            await app.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=reply_markup)
        except Exception as exc:
            log.warning("发送异常提醒失败 chat=%s：%s", chat_id, exc)


async def send_user_alert(app: Application, cfg: AppConfig, cache_path: Path, alert_type: str, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    await send_user_alert_to_chats(app, alert_notification_chats_sync(cache_path, cfg, alert_type), text, reply_markup)


def format_traffic_alert(row: dict[str, Any], recovered: bool = False) -> str:
    title = "✅ <b>流量异常恢复</b>" if recovered else "🚨 <b>流量异常告警</b>"
    label = render_user_label(int(row["user_id"]), str(row.get("name") or ""))
    period_label = str(row.get("period_label") or alert_period_label(row.get("period")))
    rule_type = str(row.get("rule_type") or "默认规则")
    rule_line = f"当前适用：{rule_type} (<b>{period_label} / {format_bytes(int(row['threshold']))}</b>)"
    if recovered:
        return "\n".join([title, "────────────", f"用户：{label}", rule_line, f"当前{period_label}用量：{format_bytes(int(row['total']))}"])
    return "\n".join([title, "────────────", f"用户：{label}", rule_line, f"{period_label}用量：<b>{format_bytes(int(row['total']))}</b>", "", "连续超出规则期间只会首次通知；恢复到规则内后会推送恢复。"] )


def format_ip_alert(row: dict[str, Any], recovered: bool = False, previous_city_count: int | None = None) -> str:
    if recovered:
        title = "✅ <b>异地登录恢复</b>"
    elif previous_city_count is not None:
        trend = "📈" if int(row.get("city_count") or 0) > int(previous_city_count) else "📉"
        title = f"{trend} <b>异地登录变化</b>"
    else:
        title = "🚨 <b>异地登录</b>"
    label = render_user_label(int(row["user_id"]), str(row.get("name") or ""))
    period_label = str(row.get("period_label") or alert_period_label(row.get("period")))
    cities = "、".join(html.escape(c) for c in row.get("cities", []) if c) or "未知"
    rule_type = str(row.get("rule_type") or "默认规则")
    rule_line = f"当前适用：{rule_type} (<b>{period_label} / {int(row.get('threshold') or IP_ALERT_DEFAULT_CITY_THRESHOLD)} 个城市</b>)"
    city_count = int(row.get("city_count") or 0)
    change_line = f"城市数变化：{int(previous_city_count)} → {city_count}" if previous_city_count is not None else ""
    if recovered:
        lines = [title, "────────────", f"用户：{label}", rule_line]
        if change_line:
            lines.append(change_line)
        lines.extend([f"{period_label}城市数：{city_count}", f"涉及城市：{cities}", "", PROXY_PROTOCOL_NOTICE])
        return "\n".join(lines)
    lines = [title, "────────────", f"用户：{label}", rule_line]
    if change_line:
        lines.append(change_line)
        lines.append("状态：仍超过阈值")
    lines.extend([f"{period_label}城市数：<b>{city_count}</b>", f"涉及城市：{cities}", "", PROXY_PROTOCOL_NOTICE])
    if previous_city_count is None:
        lines.extend(["", "基础版只在首次超出和恢复时提醒；高级版会在超出阈值后的城市数量变化时再次提醒。"])
    return "\n".join(lines)


def ip_alert_keyboard(row: dict[str, Any]) -> InlineKeyboardMarkup:
    user_id = int(row["user_id"])
    period = str(row.get("period") or ALERT_DEFAULT_PERIOD)
    start_ts, end_ts, _ = alert_period_window(period, datetime.now())
    kind = ip_range_kind(start_ts, end_ts)
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔍 查询详情", callback_data=f"ip_active_user_detail:{kind}:{user_id}:0:alert"),
        InlineKeyboardButton("🎚 调整规则", callback_data=f"alert_user:ip:{user_id}:alert"),
    ], [
        InlineKeyboardButton("忽略地区", callback_data=f"ip_ignore_page:area:{kind}:{user_id}:0:0:alert"),
        InlineKeyboardButton("忽略 ASN", callback_data=f"ip_ignore_page:asn:{kind}:{user_id}:0:0:alert"),
        InlineKeyboardButton("忽略 IP", callback_data=f"ip_ignore_page:cidr:{kind}:{user_id}:0:0:alert"),
    ]])


async def check_traffic_alerts(app: Application, cfg: AppConfig, cache_path: Path) -> None:
    rows = traffic_alert_rows_sync(cache_path)
    current = {int(row["user_id"]): row for row in rows}
    previous_raw = alert_state_get_sync(cache_path, "traffic_alert_active_users") or "[]"
    try:
        previous = {int(x) for x in json.loads(previous_raw)}
    except Exception:
        previous = set()
    for user_id, row in current.items():
        if user_id not in previous:
            await send_user_alert(app, cfg, cache_path, "traffic", format_traffic_alert(row))
    for user_id in sorted(previous - set(current)):
        period, period_label, threshold, rule_type = alert_effective_rule_detail_for_user_sync(cache_path, "traffic", user_id)
        total = current_traffic_alert_value_for_user_sync(cache_path, user_id, period)
        row = {"user_id": user_id, "name": cached_user_name_by_id(cache_path, user_id) or f"用户{user_id}", "total": total, "threshold": threshold, "period": period, "period_label": period_label, "rule_type": rule_type}
        await send_user_alert(app, cfg, cache_path, "traffic", format_traffic_alert(row, recovered=True))
    alert_state_set_sync(cache_path, "traffic_alert_active_users", json.dumps(sorted(current)))


async def check_ip_alerts(app: Application, cfg: AppConfig, cache_path: Path) -> None:
    rows = ip_alert_rows_sync(cache_path)
    current = {int(row["user_id"]): row for row in rows}
    previous_raw = alert_state_get_sync(cache_path, "ip_alert_active_users") or "{}"
    try:
        loaded = json.loads(previous_raw)
        if isinstance(loaded, dict):
            previous = {int(user_id): int(count) for user_id, count in loaded.items()}
        else:
            # 兼容上一版只存活跃 user_id 列表的状态。
            previous = {int(user_id): -1 for user_id in loaded}
    except Exception:
        previous = {}
    chat_modes = ip_alert_notification_chat_modes_sync(cache_path, cfg)
    basic_or_advanced_chats = [chat_id for chat_id, mode in chat_modes.items() if mode in {"basic", "advanced"}]
    advanced_chats = [chat_id for chat_id, mode in chat_modes.items() if mode == "advanced"]
    for user_id, row in current.items():
        city_count = int(row.get("city_count") or 0)
        previous_count = previous.get(user_id)
        if previous_count is None:
            await send_user_alert_to_chats(app, basic_or_advanced_chats, format_ip_alert(row), ip_alert_keyboard(row))
        elif previous_count != city_count:
            await send_user_alert_to_chats(app, advanced_chats, format_ip_alert(row, previous_city_count=previous_count), ip_alert_keyboard(row))
    for user_id in sorted(set(previous) - set(current)):
        period, period_label, threshold, rule_type = alert_effective_rule_detail_for_user_sync(cache_path, "ip", user_id)
        city_count, cities = current_ip_alert_detail_for_user_sync(cache_path, user_id, period)
        row = {"user_id": user_id, "name": cached_user_name_by_id(cache_path, user_id) or f"用户{user_id}", "city_count": city_count, "threshold": threshold, "period": period, "period_label": period_label, "cities": cities, "rule_type": rule_type}
        await send_user_alert_to_chats(app, basic_or_advanced_chats, format_ip_alert(row, recovered=True, previous_city_count=previous.get(user_id)))
    alert_state_set_sync(cache_path, "ip_alert_active_users", json.dumps({str(user_id): int(row.get("city_count") or 0) for user_id, row in current.items()}, sort_keys=True))


def version_notice_sent_key(latest: str, date_text: str) -> str:
    return f"version_update_notice:{latest}:{date_text}"


def version_notice_already_sent_sync(cache_path: Path, latest: str, date_text: str) -> bool:
    return get_collector_state_sync(cache_path, version_notice_sent_key(latest, date_text)) is not None


def mark_version_notice_sent_sync(cache_path: Path, latest: str, date_text: str) -> None:
    alert_state_set_sync(cache_path, version_notice_sent_key(latest, date_text), "1")


def version_update_notice_text(check: dict[str, Any]) -> str:
    return "\n".join([
        "⬆️ <b>发现 Xbot 新版本</b>",
        "────────────",
        f"当前版本：<code>{html.escape(str(check.get('current') or 'unknown'))}</code>",
        f"最新版本：<code>{html.escape(str(check.get('latest') or 'unknown'))}</code>",
        "",
        "你可以点击下方按钮执行后台更新。",
        "更新前会再次确认。",
    ])


async def send_update_result_notice(app: Application) -> None:
    data = await asyncio.to_thread(consume_update_status_sync)
    if not data:
        return
    chat_id = str(data.get("chat_id") or "")
    if not chat_id:
        log.info("更新状态已读取，但没有 chat_id：%s", data)
        return
    try:
        await app.bot.send_message(chat_id=chat_id, text=update_result_text(data), parse_mode="HTML")
    except Exception as exc:
        log.warning("发送更新结果通知失败 chat=%s：%s", chat_id, exc)


async def version_update_check_loop(app: Application, cfg: AppConfig, cache_path: Path, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            now = beijing_now()
            if now.hour == 12 and now.minute == 0:
                date_text = now.strftime("%Y-%m-%d")
                check = await asyncio.to_thread(version_check_sync)
                latest = str(check.get("latest") or "")
                if check.get("has_update") and latest and not await asyncio.to_thread(version_notice_already_sent_sync, cache_path, latest, date_text):
                    chats = await asyncio.to_thread(default_allowlist_notification_chats_sync, cache_path, cfg, "version_update")
                    for chat_id in chats:
                        try:
                            admin_view = str(chat_id) == str(cfg.telegram.admin_user_id)
                            await app.bot.send_message(chat_id=chat_id, text=version_update_notice_text(check), parse_mode="HTML", reply_markup=version_keyboard(check, admin_view=admin_view))
                        except Exception as exc:
                            log.warning("发送版本更新通知失败 chat=%s：%s", chat_id, exc)
                    await asyncio.to_thread(mark_version_notice_sent_sync, cache_path, latest, date_text)
        except Exception as exc:
            log.exception("版本更新检查任务异常：%s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=60)
        except asyncio.TimeoutError:
            continue


async def traffic_sampler_loop(app: Application, cfg: AppConfig, cache_path: Path, stop_event: asyncio.Event) -> None:
    """Sample Xboard cumulative counters once per minute and store local deltas."""
    while not stop_event.is_set():
        try:
            users, nodes, deltas, gap_seconds, previous_ts, current_ts = await asyncio.to_thread(sample_traffic_deltas_sync, cache_path, cfg.mysql)
            await check_traffic_alerts(app, cfg, cache_path)
            if gap_seconds > TRAFFIC_SAMPLE_GAP_TOLERANCE_SECONDS:
                log.warning("检测到流量采样间隔异常：%s", format_duration(gap_seconds))
                await send_collector_text_alert(app, cfg, cache_path, format_collector_gap_alert(previous_ts, current_ts, gap_seconds))
            log.info("流量采样完成：用户 %s 个，节点 %s 个，增量记录 %s 条", users, nodes, deltas)
            await notify_collector_health_transition(app, cfg, cache_path, "mysql", True, "流量采样只读查询已恢复成功。")
        except Exception as exc:
            log.exception("流量采样任务异常：%s", exc)
            await notify_collector_health_transition(app, cfg, cache_path, "mysql", False, f"{type(exc).__name__}: {exc}")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=max(30.0, cfg.traffic_dashboard_refresh_seconds))
        except asyncio.TimeoutError:
            continue


async def cleanup_legacy_traffic_dashboard_messages(app: Application, cache_path: Path) -> None:
    rows = await asyncio.to_thread(pinned_dashboard_all_sync, cache_path)
    for row in rows:
        kind = str(row.get("kind") or "")
        if kind not in {"users", "nodes"}:
            continue
        chat_id = str(row.get("chat_id") or "")
        message_id = int(row.get("message_id") or 0)
        try:
            await app.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except BadRequest:
            pass
        await asyncio.to_thread(pinned_dashboard_delete_sync, cache_path, kind, chat_id)


def traffic_report_text_sync(cache_path: Path, kind: str) -> tuple[str, int, int]:
    start_ts, end_ts, label = traffic_report_window(kind)
    title = f"📰 {TRAFFIC_REPORT_KINDS[kind]}"
    text = render_traffic_dashboard_text(title, label, start_ts, end_ts, cache_path)
    return text, start_ts, end_ts


def due_traffic_report_kinds(now: datetime | None = None) -> list[str]:
    current = now.astimezone(BEIJING_TZ) if now else beijing_now()
    # 00:03 以后发送，给 00:00 附近最后一轮采样一点缓冲。
    if current.hour != 0 or current.minute < 3:
        return []
    kinds = ["daily"]
    if current.weekday() == 0:  # 周一发送上周周报
        kinds.append("weekly")
    if current.day == 1:
        kinds.append("monthly")
    return kinds


def traffic_dashboard_keyboard_static(kind: str, is_pinned: bool = False) -> InlineKeyboardMarkup:
    pin_text = "⬇️ 取消置顶" if is_pinned else "⬆️ 置顶"
    pin_action = "unpin" if is_pinned else "pin"
    rows: list[list[InlineKeyboardButton]] = []
    if kind.startswith("ip_") or kind.startswith("iprange_"):
        if kind in {"ip_1h", "ip_24h", "ip_7d", "ip_30d"}:
            period_labels = {
                "ip_1h": "近 1 小时",
                "ip_24h": "近 24 小时",
                "ip_7d": "近 7 天",
                "ip_30d": "近 30 天",
            }
            rows.append([
                InlineKeyboardButton(label, callback_data=f"active_users:{period_kind.removeprefix('ip_')}")
                for period_kind, label in period_labels.items()
                if period_kind != kind
            ])
        rows.append([InlineKeyboardButton("自选区间", callback_data="ip_custom:start")])
        rows.append([InlineKeyboardButton("🔍 用户详情", callback_data=f"ip_detail_list:{kind}:0")])
    else:
        switch_callback = f"traffic_menu:{kind}"
        rows.append([InlineKeyboardButton("💫 切换周期", callback_data=switch_callback)])
    rows.append([
        InlineKeyboardButton(pin_text, callback_data=f"traffic_dashboard:{pin_action}:{kind}"),
        InlineKeyboardButton("❌ 关闭", callback_data=f"traffic_dashboard:delete:{kind}"),
    ])
    return InlineKeyboardMarkup(rows)


async def traffic_report_push_loop(
    app: Application,
    cache_path: Path,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        try:
            now = beijing_now()
            for kind in due_traffic_report_kinds(now):
                text, start_ts, end_ts = await asyncio.to_thread(traffic_report_text_sync, cache_path, kind)
                chats = await asyncio.to_thread(notification_enabled_chats_sync, cache_path, kind)
                sent_chats: list[str] = []
                for chat_id in chats:
                    if await asyncio.to_thread(traffic_report_already_sent_sync, cache_path, kind, start_ts, end_ts, chat_id):
                        continue
                    try:
                        await app.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
                        await asyncio.to_thread(mark_traffic_report_sent_sync, cache_path, kind, start_ts, end_ts, chat_id)
                        sent_chats.append(chat_id)
                    except Exception as exc:
                        log.warning("发送 %s 失败 chat=%s：%s", TRAFFIC_REPORT_KINDS[kind], chat_id, exc)
                if sent_chats:
                    log.info("%s 推送完成：%s 个聊天", TRAFFIC_REPORT_KINDS[kind], len(sent_chats))
        except Exception as exc:
            log.exception("流量报表推送任务异常：%s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=60)
        except asyncio.TimeoutError:
            continue


async def traffic_dashboard_refresh_loop(
    app: Application,
    cfg: AppConfig,
    cache_path: Path,
    stop_event: asyncio.Event,
) -> None:
    """Refresh pinned dashboard messages and delete unpinned interactive messages after 3 minutes."""
    while not stop_event.is_set():
        try:
            now_ts = int(datetime.now().timestamp())
            due_rows = await asyncio.to_thread(auto_delete_due_messages_sync, cache_path, now_ts - 180)
            for row in due_rows:
                chat_id = str(row.get("chat_id") or "")
                message_id = int(row.get("message_id") or 0)
                if not chat_id or not message_id:
                    continue
                try:
                    chat = await app.bot.get_chat(chat_id=chat_id)
                    pinned = getattr(chat, "pinned_message", None)
                    if pinned and getattr(pinned, "message_id", None) == message_id:
                        await asyncio.to_thread(auto_delete_message_set_sync, cache_path, chat_id, message_id, True)
                        continue
                except Exception:
                    pass
                try:
                    await app.bot.delete_message(chat_id=chat_id, message_id=message_id)
                except BadRequest:
                    pass
                await asyncio.to_thread(pinned_dashboard_delete_message_sync, cache_path, chat_id, message_id)
                await asyncio.to_thread(auto_delete_message_delete_sync, cache_path, chat_id, message_id)

            rows = await asyncio.to_thread(pinned_dashboard_all_sync, cache_path)
            for row in rows:
                kind = str(row.get("kind") or "")
                chat_id = str(row.get("chat_id") or "")
                message_id = int(row.get("message_id") or 0)
                is_pinned = bool(int(row.get("is_pinned") or 0))
                if not is_pinned:
                    continue
                if kind.startswith("ip_") or kind.startswith("iprange_"):
                    continue
                if kind == "combined" or kind.startswith("preset_") or kind.startswith("users_") or kind.startswith("nodes_") or kind.startswith("range_"):
                    text = await asyncio.to_thread(traffic_dashboard_text_from_kind_sync, cache_path, kind)
                else:
                    continue
                try:
                    await app.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=text,
                        parse_mode="HTML",
                        reply_markup=traffic_dashboard_keyboard_static(kind, is_pinned),
                    )
                except BadRequest as exc:
                    if "message is not modified" in str(exc).lower():
                        continue
                    log.warning("刷新流量仪表盘消息失败，移除记录 kind=%s chat=%s msg=%s：%s", kind, chat_id, message_id, exc)
                    await asyncio.to_thread(pinned_dashboard_delete_message_sync, cache_path, chat_id, message_id)
        except Exception as exc:
            log.exception("流量仪表盘刷新任务异常：%s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=10)
        except asyncio.TimeoutError:
            continue


def cache_geo_status_sync(cache_path: Path) -> dict[str, int]:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        active_ips = int(conn.execute("SELECT COUNT(DISTINCT ip) FROM active_ip_records WHERE ignored_at IS NULL").fetchone()[0] or 0)
        geo_total = int(conn.execute("SELECT COUNT(*) FROM ip_geo_cache").fetchone()[0] or 0)
        geo_pending = int(conn.execute(
            """
            SELECT COUNT(*) FROM ip_geo_cache
            WHERE (queried_at IS NULL OR queried_at <= 0)
              AND (raw IS NULL OR raw = '')
            """
        ).fetchone()[0] or 0)
    return {"active_ips": active_ips, "geo_total": geo_total, "geo_pending": geo_pending}


def pending_geo_ips_sync(cache_path: Path, limit: int | None = None) -> list[str]:
    init_cache(cache_path)
    sql = """
        SELECT ip FROM ip_geo_cache
        WHERE (queried_at IS NULL OR queried_at <= 0)
          AND (raw IS NULL OR raw = '')
        ORDER BY queried_at ASC, ip ASC
    """
    params: tuple[Any, ...] = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (limit,)
    with cache_connect(cache_path) as conn:
        return [str(row["ip"]) for row in conn.execute(sql, params).fetchall()]


def estimate_geo_wait_seconds(pending_count: int, queries_per_minute: int) -> int:
    if pending_count <= 0:
        return 0
    return int((pending_count * 60 + max(1, queries_per_minute) - 1) // max(1, queries_per_minute))


def format_geo_pending_text(pending_count: int, queries_per_minute: int) -> str:
    if pending_count <= 0:
        return "待补全 0 条"
    wait_seconds = estimate_geo_wait_seconds(pending_count, queries_per_minute)
    return f"待补全 {pending_count} 条，预计约 {format_duration(wait_seconds)}"


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} 小时 {minutes} 分钟"
    if minutes:
        return f"{minutes} 分钟 {sec} 秒" if sec else f"{minutes} 分钟"
    return f"{sec} 秒"


def format_bytes(value: int | float | None) -> str:
    size = float(value or 0)
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    for unit in units:
        if abs(size) < 1024 or unit == units[-1]:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"


def query_ip_api_sync(ip: str) -> dict[str, Any]:
    fields = "status,message,country,countryCode,regionName,city,district,isp,as,asname,org,query"
    url = "http://ip-api.com/json/" + urllib.parse.quote(ip, safe="") + "?" + urllib.parse.urlencode({
        "lang": "zh-CN",
        "fields": fields,
    })
    req = urllib.request.Request(url, headers={"User-Agent": "xbot"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        raw = resp.read(8192).decode("utf-8", errors="replace")
    data = json.loads(raw)
    if data.get("status") != "success":
        raise RuntimeError(str(data.get("message") or "ip-api 查询失败"))
    return data


def normalize_geo_name(value: Any) -> str:
    return str(value or "").strip().replace("臺", "台")


def geo_text_contains(values: list[str], patterns: list[str]) -> bool:
    joined = " ".join(values)
    return any(re.search(pattern, joined, re.IGNORECASE) for pattern in patterns)


def normalize_taiwan_city(region: str, city: str, district: str) -> str:
    county_cities = [
        "台北市", "新北市", "桃园市", "台中市", "台南市", "高雄市",
        "基隆市", "新竹市", "嘉义市",
        "新竹县", "苗栗县", "彰化县", "南投县", "云林县", "嘉义县",
        "屏东县", "宜兰县", "花莲县", "台东县", "澎湖县", "金门县", "连江县",
    ]
    aliases = {
        "台北": "台北市",
        "新北": "新北市",
        "桃园": "桃园市",
        "台中": "台中市",
        "台南": "台南市",
        "高雄": "高雄市",
        "基隆": "基隆市",
        "新竹": "新竹市",
        "嘉义": "嘉义市",
    }
    for item in [region, city, district]:
        name = normalize_geo_name(item)
        if not name:
            continue
        if name in county_cities:
            return name
        if name in aliases:
            return aliases[name]
    return normalize_geo_name(region or city or district) or "台湾未知城市"


def build_geo_stat_area(data: dict[str, Any]) -> dict[str, str]:
    """Build the normalized city-level area used only for active-area statistics."""
    country_code = normalize_geo_name(data.get("countryCode")).upper()
    country = normalize_geo_name(data.get("country"))
    region = normalize_geo_name(data.get("regionName"))
    city = normalize_geo_name(data.get("city"))
    district = normalize_geo_name(data.get("district"))
    values = [country, region, city, district]

    if country_code == "HK" or geo_text_contains(values, [r"香港", r"Hong\s*Kong"]):
        return {"key": "HK:香港", "name": "香港", "level": "sar_city"}
    if country_code == "MO" or geo_text_contains(values, [r"澳门", r"澳門", r"Macau", r"Macao"]):
        return {"key": "MO:澳门", "name": "澳门", "level": "sar_city"}
    if country_code == "TW" or geo_text_contains(values, [r"台湾", r"Taiwan"]):
        stat_name = normalize_taiwan_city(region, city, district)
        return {"key": f"TW:{stat_name}", "name": stat_name, "level": "tw_city"}

    if country_code == "CN" or country == "中国":
        municipalities = {"北京市", "上海市", "天津市", "重庆市"}
        if region in municipalities:
            return {"key": f"CN:{region}", "name": region, "level": "municipality"}
        stat_name = city or region or "未知城市"
        return {"key": f"CN:{region or '未知省份'}:{stat_name}", "name": stat_name, "level": "city"}

    stat_name = city or region or country or "未知地区"
    return {"key": f"{country_code or country or 'UNKNOWN'}:{region}:{stat_name}", "name": stat_name, "level": "city"}


def update_geo_cache_success_sync(cache_path: Path, ip: str, data: dict[str, Any]) -> None:
    now_ts = int(datetime.now().timestamp())
    stat_area = build_geo_stat_area(data)
    with cache_connect(cache_path) as conn:
        conn.execute(
            """
            INSERT INTO ip_geo_cache(ip, country, region, city, district, isp, stat_area_key, stat_area_name, stat_area_level, raw, queried_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ip) DO UPDATE SET
                country=excluded.country,
                region=excluded.region,
                city=excluded.city,
                district=excluded.district,
                isp=excluded.isp,
                stat_area_key=excluded.stat_area_key,
                stat_area_name=excluded.stat_area_name,
                stat_area_level=excluded.stat_area_level,
                raw=excluded.raw,
                queried_at=excluded.queried_at
            """,
 (
                ip,
                str(data.get("country") or ""),
                str(data.get("regionName") or ""),
                str(data.get("city") or ""),
                str(data.get("district") or ""),
                str(data.get("isp") or ""),
                stat_area["key"],
                stat_area["name"],
                stat_area["level"],
                json.dumps(data, ensure_ascii=False),
                now_ts,
            ),
        )
        country = str(data.get("country") or "").strip()
        region = str(data.get("regionName") or "").strip()
        city = str(data.get("city") or "").strip()
        area_keys = []
        if stat_area.get("key"):
            area_keys.append(stat_area["key"])
        if city:
            area_keys.append("|".join(part for part in (country, region, city) if part))
        if region:
            area_keys.append("|".join(part for part in (country, region) if part))
        if country:
            area_keys.append(country)
        area_keys = [key for key in dict.fromkeys(area_keys) if key]
        if area_keys:
            placeholders = ",".join("?" for _ in area_keys)
            conn.execute(
                f"""
                UPDATE active_ip_records
                SET ignored_at = ?, ignore_reason = 'manual_area', ignore_note = '忽略列表：地区'
                WHERE ip = ? AND ignored_at IS NULL AND EXISTS (
                    SELECT 1 FROM ignored_ip_rules AS r
                    WHERE r.dimension = 'area' AND r.value IN ({placeholders})
                )
                """,
                [now_ts, ip, *area_keys],
            )
        apply_ignored_rules_conn(conn, now_ts)


def ipv4_24_cidr(value: str) -> str | None:
    try:
        ip_obj = ipaddress.ip_address(str(value).strip())
    except ValueError:
        return None
    if ip_obj.version != 4:
        return None
    return str(ipaddress.ip_network(f"{ip_obj}/24", strict=False))


def raw_geo_data(row: sqlite3.Row) -> dict[str, Any]:
    try:
        raw = row["raw"]
    except (KeyError, IndexError):
        raw = None
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def row_value(row: sqlite3.Row, name: str) -> Any:
    try:
        return row[name]
    except (KeyError, IndexError):
        return None


def asn_key_from_raw(raw: dict[str, Any]) -> str | None:
    as_text = str(raw.get("as") or "").strip()
    match = re.search(r"\bAS\s*(\d+)\b", as_text, re.IGNORECASE)
    if match:
        return f"AS{match.group(1)}"
    return None


def asn_label_from_raw(raw: dict[str, Any]) -> str | None:
    key = asn_key_from_raw(raw)
    if not key:
        return None
    as_text = str(raw.get("as") or "").strip()
    asname = str(raw.get("asname") or "").strip()
    org = str(raw.get("org") or "").strip()
    suffix = as_text
    if suffix.upper().startswith(key.upper()):
        suffix = suffix[len(key):].strip()
    suffix = suffix or asname or org
    return f"{key} {suffix}".strip()


def asn_key_for_geo_row(row: sqlite3.Row) -> str | None:
    return asn_key_from_raw(raw_geo_data(row))


def ignored_rule_count_sync(cache_path: Path) -> int:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM ignored_ip_rules").fetchone()[0] or 0)


def ignored_rule_counts_by_dimension_sync(cache_path: Path) -> dict[str, int]:
    init_cache(cache_path)
    counts = {"area": 0, "asn": 0, "cidr": 0}
    with cache_connect(cache_path) as conn:
        rows = conn.execute("SELECT dimension, COUNT(*) AS c FROM ignored_ip_rules WHERE dimension IN ('area', 'asn', 'cidr') GROUP BY dimension").fetchall()
    for row in rows:
        counts[str(row["dimension"])] = int(row["c"] or 0)
    return counts


def geo_area_rule_label(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return value
    if "|" in value:
        return " / ".join(part for part in value.split("|") if part)
    if ":" in value:
        return value.split(":")[-1] or value
    return value


def ignored_rules_text_sync(cache_path: Path) -> str:
    counts = ignored_rule_counts_by_dimension_sync(cache_path)
    return "\n".join([
        "📎 <b>当前忽略</b>",
        "────────────",
        "当前已忽略：",
        f"📍 地区：{counts['area']}",
        f"🏷 ASN：{counts['asn']}",
        f"🌐 IP ：{counts['cidr']}",
    ])


def ignored_rule_values_sync(cache_path: Path, dimension: str) -> set[str]:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        rows = conn.execute("SELECT value FROM ignored_ip_rules WHERE dimension = ?", (dimension,)).fetchall()
    return {str(row["value"] or "") for row in rows}


def ignored_rule_items_sync(cache_path: Path) -> list[dict[str, Any]]:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        rows = conn.execute(
            """
            SELECT dimension, value, updated_at, created_at
            FROM ignored_ip_rules
            WHERE dimension IN ('area', 'asn', 'cidr')
            ORDER BY updated_at DESC, created_at DESC, dimension ASC, value ASC
            """
        ).fetchall()
        asn_labels: dict[str, str] = {}
        if any(str(row["dimension"] or "") == "asn" for row in rows):
            geo_rows = conn.execute("SELECT raw FROM ip_geo_cache WHERE raw IS NOT NULL AND raw != ''").fetchall()
            for geo_row in geo_rows:
                raw = raw_geo_data(geo_row)
                key = asn_key_from_raw(raw)
                if key and key not in asn_labels:
                    asn_labels[key] = asn_label_from_raw(raw) or key
    items: list[dict[str, Any]] = []
    for row in rows:
        dimension = str(row["dimension"] or "")
        value = str(row["value"] or "")
        if dimension == "area":
            label = geo_area_rule_label(value)
            dim_label = "📍"
        elif dimension == "asn":
            label = asn_labels.get(value, value)
            dim_label = "🏷️"
        elif dimension == "cidr":
            label = value
            dim_label = "🌐"
        else:
            continue
        items.append({"dimension": dimension, "value": value, "label": label, "sub": dim_label, "updated_at": int(row["updated_at"] or row["created_at"] or 0)})
    return items


def ignored_list_items_sync(cache_path: Path, dimension: str) -> list[dict[str, Any]]:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        if dimension == "area":
            rows = conn.execute(
                """
                SELECT COALESCE(NULLIF(g.stat_area_name, ''), NULLIF(g.city, ''), NULLIF(g.region, ''), NULLIF(g.country, '')) AS display,
                       g.country, g.region, g.city, g.district, g.stat_area_key, g.stat_area_name, g.stat_area_level, MAX(a.last_seen_at) AS last_seen_at,
                       COUNT(DISTINCT a.ip) AS ip_count, COUNT(DISTINCT a.user_id) AS user_count
                FROM active_ip_records AS a
                JOIN ip_geo_cache AS g ON g.ip = a.ip
                WHERE COALESCE(NULLIF(g.stat_area_key, ''), NULLIF(g.city, ''), NULLIF(g.region, ''), NULLIF(g.country, '')) IS NOT NULL
                GROUP BY COALESCE(NULLIF(g.stat_area_key, ''), NULLIF(g.city, ''), NULLIF(g.region, ''), NULLIF(g.country, '')), g.country, g.region, g.city, g.district, g.stat_area_key, g.stat_area_name, g.stat_area_level
                ORDER BY last_seen_at DESC, display ASC
                """
            ).fetchall()
            items = []
            for row in rows:
                key = geo_area_key(row)
                if not key:
                    continue
                label = str(row["display"] or "").strip() or geo_area_rule_label(key)
                items.append({"value": key, "label": label, "sub": f"{int(row['ip_count'] or 0)} IP / {int(row['user_count'] or 0)} 用户", "last_seen_at": int(row["last_seen_at"] or 0)})
            return items
        if dimension == "asn":
            rows = conn.execute(
                """
                SELECT a.ip, a.user_id, a.last_seen_at, g.raw
                FROM active_ip_records AS a
                JOIN ip_geo_cache AS g ON g.ip = a.ip
                WHERE g.raw IS NOT NULL AND g.raw != ''
                ORDER BY a.last_seen_at DESC
                """
            ).fetchall()
            buckets: dict[str, dict[str, Any]] = {}
            for row in rows:
                raw = raw_geo_data(row)
                key = asn_key_from_raw(raw)
                if not key:
                    continue
                bucket = buckets.setdefault(key, {"value": key, "label": asn_label_from_raw(raw) or key, "ips": set(), "users": set(), "last_seen_at": 0})
                bucket["ips"].add(str(row["ip"]))
                bucket["users"].add(int(row["user_id"]))
                bucket["last_seen_at"] = max(int(bucket["last_seen_at"]), int(row["last_seen_at"] or 0))
            return [
                {"value": key, "label": str(bucket["label"]), "sub": f"{len(bucket['ips'])} IP / {len(bucket['users'])} 用户", "last_seen_at": int(bucket["last_seen_at"])}
                for key, bucket in sorted(buckets.items(), key=lambda item: (-int(item[1]["last_seen_at"]), item[0]))
            ]
        if dimension == "cidr":
            rows = conn.execute(
                """
                SELECT ip, user_id, last_seen_at
                FROM active_ip_records
                ORDER BY last_seen_at DESC
                """
            ).fetchall()
            buckets: dict[str, dict[str, Any]] = {}
            for row in rows:
                cidr = ipv4_24_cidr(str(row["ip"] or ""))
                if not cidr:
                    continue
                bucket = buckets.setdefault(cidr, {"value": cidr, "label": cidr, "ips": set(), "users": set(), "last_seen_at": 0})
                bucket["ips"].add(str(row["ip"]))
                bucket["users"].add(int(row["user_id"]))
                bucket["last_seen_at"] = max(int(bucket["last_seen_at"]), int(row["last_seen_at"] or 0))
            return [
                {"value": cidr, "label": cidr, "sub": f"{len(bucket['ips'])} IP / {len(bucket['users'])} 用户", "last_seen_at": int(bucket["last_seen_at"])}
                for cidr, bucket in sorted(buckets.items(), key=lambda item: (-int(item[1]["last_seen_at"]), item[0]))
            ]
    return []


def ignored_rule_toggle_sync(cache_path: Path, dimension: str, value: str) -> bool:
    init_cache(cache_path)
    if dimension not in {"area", "asn", "cidr"}:
        raise ValueError("unsupported ignore dimension")
    now_ts = int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        exists = conn.execute("SELECT 1 FROM ignored_ip_rules WHERE dimension = ? AND value = ?", (dimension, value)).fetchone()
        if exists:
            conn.execute("DELETE FROM ignored_ip_rules WHERE dimension = ? AND value = ?", (dimension, value))
            reason = {"area": "manual_area", "asn": "manual_asn", "cidr": "manual_cidr"}.get(dimension, "")
            if reason:
                conn.execute("UPDATE active_ip_records SET ignored_at = NULL, ignore_reason = NULL, ignore_note = NULL WHERE ignore_reason = ?", (reason,))
            apply_ignored_rules_conn(conn, now_ts)
            return False
        conn.execute(
            """
            INSERT INTO ignored_ip_rules(dimension, value, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(dimension, value) DO UPDATE SET updated_at = excluded.updated_at
            """,
 (dimension, value, now_ts, now_ts),
        )
        apply_ignored_rules_conn(conn, now_ts)
        return True


def apply_ignored_rules_conn(conn: sqlite3.Connection, now_ts: int) -> None:
    conn.execute("DELETE FROM ignored_ip_rules WHERE dimension NOT IN ('area', 'asn', 'cidr')")
    area_rules = [str(row["value"] or "") for row in conn.execute("SELECT value FROM ignored_ip_rules WHERE dimension = 'area'").fetchall()]
    if area_rules:
        placeholders = ",".join("?" for _ in area_rules)
        conn.execute(
            f"""
            UPDATE active_ip_records
            SET ignored_at = ?, ignore_reason = 'manual_area', ignore_note = '忽略列表：地区'
            WHERE ignored_at IS NULL AND ip IN (
                SELECT g.ip FROM ip_geo_cache AS g
                WHERE COALESCE(NULLIF(g.stat_area_key, ''), NULLIF(g.city, ''), NULLIF(g.region, ''), NULLIF(g.country, '')) IS NOT NULL
                  AND (
                    NULLIF(g.stat_area_key, '') IN ({placeholders})
                    OR
                    CASE
                      WHEN NULLIF(g.city, '') IS NOT NULL THEN TRIM(COALESCE(g.country, '') || CASE WHEN COALESCE(g.country, '') != '' THEN '|' ELSE '' END || COALESCE(g.region, '') || CASE WHEN COALESCE(g.region, '') != '' THEN '|' ELSE '' END || COALESCE(g.city, ''))
                      WHEN NULLIF(g.region, '') IS NOT NULL THEN TRIM(COALESCE(g.country, '') || CASE WHEN COALESCE(g.country, '') != '' THEN '|' ELSE '' END || COALESCE(g.region, ''))
                      ELSE g.country
                    END
                  ) IN ({placeholders})
            )
            """,
            [now_ts, *area_rules, *area_rules],
        )
    asn_rules = {str(row["value"] or "") for row in conn.execute("SELECT value FROM ignored_ip_rules WHERE dimension = 'asn'").fetchall()}
    if asn_rules:
        rows = conn.execute(
            """
            SELECT a.user_id, a.ip, g.raw
            FROM active_ip_records AS a
            JOIN ip_geo_cache AS g ON g.ip = a.ip
            WHERE a.ignored_at IS NULL AND g.raw IS NOT NULL AND g.raw != ''
            """
        ).fetchall()
        targets = [(int(row["user_id"]), str(row["ip"])) for row in rows if (asn_key_for_geo_row(row) in asn_rules)]
        if targets:
            conn.executemany(
                """
                UPDATE active_ip_records
                SET ignored_at = ?, ignore_reason = 'manual_asn', ignore_note = '忽略列表：ASN'
                WHERE user_id = ? AND ip = ? AND ignored_at IS NULL
                """,
                [(now_ts, user_id, ip) for user_id, ip in targets],
            )
    cidr_rules = [str(row["value"] or "") for row in conn.execute("SELECT value FROM ignored_ip_rules WHERE dimension = 'cidr'").fetchall()]
    if cidr_rules:
        networks = []
        for rule in cidr_rules:
            try:
                net = ipaddress.ip_network(rule, strict=False)
            except ValueError:
                continue
            if net.version == 4:
                networks.append(net)
        if networks:
            rows = conn.execute("SELECT user_id, ip FROM active_ip_records WHERE ignored_at IS NULL").fetchall()
            targets = []
            for row in rows:
                try:
                    ip_obj = ipaddress.ip_address(str(row["ip"] or ""))
                except ValueError:
                    continue
                if ip_obj.version == 4 and any(ip_obj in net for net in networks):
                    targets.append((int(row["user_id"]), str(row["ip"])))
            if targets:
                conn.executemany(
                    """
                    UPDATE active_ip_records
                    SET ignored_at = ?, ignore_reason = 'manual_cidr', ignore_note = '忽略列表：IP 段'
                    WHERE user_id = ? AND ip = ? AND ignored_at IS NULL
                    """,
                    [(now_ts, user_id, ip) for user_id, ip in targets],
                )

def update_geo_cache_failure_sync(cache_path: Path, ip: str, error: str) -> None:
    # 失败也写 queried_at，避免坏 IP 在一次初始化里反复阻塞；后续可通过清空 queried_at 重试。
    now_ts = int(datetime.now().timestamp())
    raw = json.dumps({"error": error}, ensure_ascii=False)
    with cache_connect(cache_path) as conn:
        conn.execute(
            """
            INSERT INTO ip_geo_cache(ip, raw, queried_at)
            VALUES (?, ?, ?)
            ON CONFLICT(ip) DO UPDATE SET raw=excluded.raw, queried_at=excluded.queried_at
            """,
 (ip, raw, now_ts),
        )


def backfill_geo_pending_once(cache_path: Path, limit: int = 5) -> tuple[int, int, int]:
    """Best-effort background IP geo backfill. Returns (total, success, failed)."""
    ips = pending_geo_ips_sync(cache_path, limit=max(1, limit))
    success = 0
    failed = 0
    for ip in ips:
        try:
            data = query_ip_api_sync(ip)
            update_geo_cache_success_sync(cache_path, ip, data)
            success += 1
        except urllib.error.HTTPError as exc:
            failed += 1
            # Stop immediately on rate limit; leave this IP pending for a later run.
            if exc.code == 429:
                log.warning("后台 IP 归属地补全触发 ip-api 限流，暂停本轮")
                break
            update_geo_cache_failure_sync(cache_path, ip, f"HTTP {exc.code}")
        except Exception as exc:
            failed += 1
            update_geo_cache_failure_sync(cache_path, ip, type(exc).__name__)
    return len(ips), success, failed


def backfill_geo_pending_rate_limited(
    cache_path: Path,
    limit: int,
    queries_per_minute: int,
    retry_wait_seconds: float = 65.0,
    stop_when_rate_limited: bool = True,
) -> tuple[int, int, int, bool]:
    """Backfill pending IP geo records at a steady, API-friendly pace.

    Returns (total_selected, success, failed, rate_limited). The sleep happens
    between requests, not in a burst, so startup will not intentionally drive the
    free ip-api endpoint into 429 just to finish faster.
    """
    ips = pending_geo_ips_sync(cache_path, limit=max(1, int(limit)))
    if not ips:
        return 0, 0, 0, False
    interval = 60.0 / max(1, int(queries_per_minute))
    success = 0
    failed = 0
    rate_limited = False
    for index, ip in enumerate(ips):
        started = time.monotonic()
        try:
            data = query_ip_api_sync(ip)
            update_geo_cache_success_sync(cache_path, ip, data)
            success += 1
        except urllib.error.HTTPError as exc:
            failed += 1
            if exc.code == 429:
                rate_limited = True
                log.warning("IP 归属地补全触发 ip-api 限流，等待 %.0f 秒后重试", retry_wait_seconds)
                time.sleep(max(5.0, retry_wait_seconds))
                if stop_when_rate_limited:
                    break
            else:
                update_geo_cache_failure_sync(cache_path, ip, f"HTTP {exc.code}")
        except Exception as exc:
            failed += 1
            update_geo_cache_failure_sync(cache_path, ip, type(exc).__name__)
        if index < len(ips) - 1:
            elapsed = time.monotonic() - started
            sleep_for = interval - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)
    return len(ips), success, failed, rate_limited


def backfill_geo_pending_until_complete(cache_path: Path, queries_per_minute: int = 30) -> tuple[int, int, int]:
    """Backfill all pending geo records before startup decisions/notifications run.

    This is intentionally stricter than the periodic collector: startup with a fresh
    SQLite database should not evaluate IP alerts or render initial active-region
    counts while many IPs are still shown as “待查询”. On ip-api rate limiting we
    wait for the next free-window and continue, instead of starting notification
    loops with incomplete geo data. It still respects the configured per-minute
    query pace instead of bursting to the limit.
    """
    total = 0
    success = 0
    failed = 0
    queries_per_minute = max(1, int(queries_per_minute))
    while True:
        pending = cache_geo_status_sync(cache_path)["geo_pending"]
        if pending <= 0:
            break
        current_total, current_success, current_failed, rate_limited = backfill_geo_pending_rate_limited(
            cache_path,
            limit=pending,
            queries_per_minute=queries_per_minute,
            stop_when_rate_limited=True,
        )
        total += current_total
        success += current_success
        failed += current_failed
        pending_after = cache_geo_status_sync(cache_path)["geo_pending"]
        log.info(
            "启动初始化 IP 归属地补全：本轮待处理 %s 个，成功 %s 个，失败 %s 个，剩余 %s 个",
            current_total, current_success, current_failed, pending_after,
        )
        if pending_after <= 0:
            break
        if rate_limited:
            continue
        if pending_after >= pending or current_success <= 0:
            log.warning("启动初始化 IP 归属地补全未取得进展，等待 60 秒后重试")
            time.sleep(60.0)
    return total, success, failed


def initialize_cache_before_notifications_sync(cfg: AppConfig, cache_path: Path) -> tuple[bool, str, bool, str, int, int, int]:
    """Collect active IPs and finish geo lookup before starting judgment/notification loops."""
    init_cache(cache_path)
    records = collect_redis_ip_records_sync(cfg.redis)
    if isinstance(records, str):
        log.warning("启动初始化缓存采集 Redis 失败：%s", records)
        return False, records, True, "", 0, 0, 0
    user_ids = upsert_cache_records(cache_path, records, cache_retention_days_sync(cache_path))
    mysql_ok = True
    mysql_detail = ""
    try:
        upsert_cache_users(cache_path, cfg.mysql, user_ids)
    except MySQLError as exc:
        log.warning("启动初始化缓存采集 MySQL 用户信息失败：%s", exc)
        mysql_ok = False
        mysql_detail = f"{type(exc).__name__}: {exc}"
    geo_total, geo_success, geo_failed = backfill_geo_pending_until_complete(
        cache_path,
        queries_per_minute=max(1, int(cfg.ip_geo_queries_per_minute)),
    )
    log.info(
        "启动初始化缓存采集完成：Redis IP 记录 %s 条，用户 %s 个，归属地待处理 %s 个，成功 %s 个，失败 %s 个",
        len(records), len(user_ids), geo_total, geo_success, geo_failed,
    )
    return True, "", mysql_ok, mysql_detail, geo_total, geo_success, geo_failed

def mysql_connect(cfg: MySQLConfig):
    """Create a MySQL connection used only for SELECT queries in this app."""
    return pymysql.connect(
        host=cfg.host.strip(),
        port=int(cfg.port),
        user=cfg.username.strip(),
        password=cfg.password,
        database=cfg.database.strip(),
        charset="utf8mb4",
        connect_timeout=3,
        read_timeout=5,
        write_timeout=5,
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )


def pinned_dashboard_set_sync(cache_path: Path, kind: str, chat_id: str, message_id: int, is_pinned: bool) -> None:
    init_cache(cache_path)
    now_ts = int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        conn.execute(
            """
            INSERT INTO pinned_dashboard_messages(kind, chat_id, message_id, is_pinned, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(kind, chat_id) DO UPDATE SET
                message_id=excluded.message_id,
                is_pinned=excluded.is_pinned,
                updated_at=excluded.updated_at
            """,
 (kind, chat_id, message_id, 1 if is_pinned else 0, now_ts),
        )


def pinned_dashboard_delete_sync(cache_path: Path, kind: str, chat_id: str) -> None:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        conn.execute("DELETE FROM pinned_dashboard_messages WHERE kind = ? AND chat_id = ?", (kind, chat_id))


def pinned_dashboard_all_sync(cache_path: Path) -> list[dict[str, Any]]:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        rows = conn.execute(
            """
            SELECT kind, chat_id, message_id, is_pinned, updated_at
            FROM pinned_dashboard_messages
            ORDER BY chat_id ASC, kind ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def pinned_dashboard_delete_message_sync(cache_path: Path, chat_id: str, message_id: int) -> None:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        conn.execute("DELETE FROM pinned_dashboard_messages WHERE chat_id = ? AND message_id = ?", (chat_id, message_id))


def auto_delete_message_set_sync(cache_path: Path, chat_id: str, message_id: int, is_pinned: bool) -> None:
    init_cache(cache_path)
    now_ts = int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        conn.execute(
            """
            INSERT INTO dashboard_auto_delete_messages(chat_id, message_id, is_pinned, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id, message_id) DO UPDATE SET
                is_pinned=excluded.is_pinned,
                updated_at=excluded.updated_at
            """,
 (chat_id, message_id, 1 if is_pinned else 0, now_ts),
        )


def auto_delete_message_is_pinned_sync(cache_path: Path, chat_id: str, message_id: int) -> bool:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        row = conn.execute(
            "SELECT is_pinned FROM dashboard_auto_delete_messages WHERE chat_id = ? AND message_id = ?",
 (chat_id, message_id),
        ).fetchone()
    return bool(row and int(row["is_pinned"] or 0))


def auto_delete_message_delete_sync(cache_path: Path, chat_id: str, message_id: int) -> None:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        conn.execute("DELETE FROM dashboard_auto_delete_messages WHERE chat_id = ? AND message_id = ?", (chat_id, message_id))


def clear_message_tracking_for_chat_sync(cache_path: Path, chat_id: str) -> None:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        conn.execute("DELETE FROM pinned_dashboard_messages WHERE chat_id = ?", (chat_id,))
        conn.execute("DELETE FROM dashboard_auto_delete_messages WHERE chat_id = ?", (chat_id,))


def auto_delete_due_messages_sync(cache_path: Path, older_than_ts: int) -> list[dict[str, Any]]:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        rows = conn.execute(
            """
            SELECT chat_id, message_id, is_pinned, updated_at
            FROM dashboard_auto_delete_messages
            WHERE is_pinned = 0 AND updated_at <= ?
            ORDER BY updated_at ASC
            """,
 (older_than_ts,),
        ).fetchall()
    return [dict(row) for row in rows]


def collect_traffic_counters_sync(cfg: MySQLConfig) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read current Xboard daily cumulative counters for users and nodes using SELECT only."""
    if mysql_config_missing(cfg):
        return [], []
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    record_at = int(today_start.timestamp())
    conn = mysql_connect(cfg)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT su.user_id AS entity_id,
                       COALESCE(NULLIF(MAX(u.remarks), ''), MAX(u.email), CONCAT('用户', su.user_id)) AS name,
                       SUM(su.u + su.d) AS total
                FROM v2_stat_user AS su
                LEFT JOIN v2_user AS u ON u.id = su.user_id
                WHERE su.record_type = 'd' AND su.record_at = %s
                GROUP BY su.user_id
                """,
 (record_at,),
            )
            user_rows = cursor.fetchall()
            cursor.execute(
                """
                SELECT ss.server_id AS entity_id,
                       COALESCE(MAX(s.name), CONCAT(MAX(ss.server_type), '#', ss.server_id)) AS name,
                       SUM(ss.u + ss.d) AS total
                FROM v2_stat_server AS ss
                LEFT JOIN v2_server AS s ON s.id = ss.server_id
                WHERE ss.record_type = 'd' AND ss.record_at = %s
                GROUP BY ss.server_id
                """,
 (record_at,),
            )
            node_rows = cursor.fetchall()
    finally:
        conn.close()
    return list(user_rows), list(node_rows)


def sample_traffic_deltas_sync(cache_path: Path, cfg: MySQLConfig) -> tuple[int, int, int, int, int, int]:
    """Store per-minute traffic deltas. Returns (users, nodes, deltas, gap_seconds, previous_ts, current_ts)."""
    init_cache(cache_path)
    now_ts = int(datetime.now().timestamp())
    user_rows, node_rows = collect_traffic_counters_sync(cfg)
    delta_rows = 0
    gap_seconds = 0
    previous_ts = 0
    # 保留周期由 Bot 参数配置管理；同时尊重人工设置的统计起始点。
    retention_days = cache_retention_days_sync(cache_path)
    retention_cutoff_ts = cache_retention_cutoff_ts(retention_days)
    stats_floor_ts = get_stats_floor_ts_sync(cache_path)
    cutoff_ts = max(retention_cutoff_ts, stats_floor_ts or 0)
    with cache_connect(cache_path) as conn:
        previous_sample = conn.execute(
            "SELECT value FROM collector_state WHERE key = ?",
 ("last_traffic_sample_at",),
        ).fetchone()
        if previous_sample:
            previous_ts = int(previous_sample["value"] or 0)
            gap_seconds = max(0, now_ts - previous_ts)
            if gap_seconds > TRAFFIC_SAMPLE_GAP_TOLERANCE_SECONDS:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO traffic_sample_gaps(gap_start_at, gap_end_at, gap_seconds, detected_at)
                    VALUES (?, ?, ?, ?)
                    """,
 (previous_ts, now_ts, gap_seconds, now_ts),
                )
        for kind, rows in (("user", user_rows), ("node", node_rows)):
            for row in rows:
                entity_id = int(row.get("entity_id") or 0)
                if entity_id <= 0:
                    continue
                name = str(row.get("name") or f"{kind}{entity_id}").strip().replace("\n", " ")[:160]
                total = max(0, int(row.get("total") or 0))
                previous = conn.execute(
                    "SELECT total FROM traffic_counter_snapshots WHERE kind = ? AND entity_id = ?",
 (kind, entity_id),
                ).fetchone()
                if previous is None:
                    delta = 0
                else:
                    previous_total = int(previous["total"] or 0)
                    delta = total - previous_total if total >= previous_total else total
                if delta > 0:
                    conn.execute(
                        """
                        INSERT INTO traffic_delta_samples(sampled_at, kind, entity_id, name, delta)
                        VALUES (?, ?, ?, ?, ?)
                        """,
 (now_ts, kind, entity_id, name, delta),
                    )
                    delta_rows += 1
                conn.execute(
                    """
                    INSERT INTO traffic_counter_snapshots(kind, entity_id, name, total, sampled_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(kind, entity_id) DO UPDATE SET
                        name=excluded.name,
                        total=excluded.total,
                        sampled_at=excluded.sampled_at
                    """,
 (kind, entity_id, name, total, now_ts),
                )
        conn.execute("DELETE FROM traffic_delta_samples WHERE sampled_at < ?", (cutoff_ts,))
        conn.execute("DELETE FROM traffic_sample_gaps WHERE gap_end_at < ?", (cutoff_ts,))
        set_collector_state(conn, "last_traffic_sample_at", str(now_ts), now_ts)
    return len(user_rows), len(node_rows), delta_rows, gap_seconds, previous_ts, now_ts


def earliest_traffic_sample_at_sync(cache_path: Path) -> int | None:
    init_cache(cache_path)
    floor_ts = get_stats_floor_ts_sync(cache_path) or 0
    with cache_connect(cache_path) as conn:
        row = conn.execute(
            """
            SELECT MIN(sampled_at) AS first_sample FROM (
                SELECT sampled_at FROM traffic_delta_samples
                UNION ALL
                SELECT sampled_at FROM traffic_counter_snapshots
            )
            WHERE sampled_at >= ?
            """
            , (floor_ts,)
        ).fetchone() or {}
    return int(row["first_sample"]) if row and row["first_sample"] is not None else None


def query_traffic_deltas_range_from_cache_sync(
    cache_path: Path,
    start_ts: int,
    end_ts: int,
    limit: int = 10,
    dimension: str = "combined",
) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]], int | None]:
    init_cache(cache_path)
    safe_limit = max(1, min(limit, 50))
    dimension = dimension if dimension in {"combined", "users", "nodes"} else "combined"
    total_kind = "node" if dimension == "nodes" else "user"
    with cache_connect(cache_path) as conn:
        total_row = conn.execute(
            """
            SELECT COALESCE(SUM(delta), 0) AS total
            FROM traffic_delta_samples
            WHERE sampled_at BETWEEN ? AND ? AND kind = ?
            """,
 (start_ts, end_ts, total_kind),
        ).fetchone() or {}
        user_rows = []
        node_rows = []
        if dimension in {"combined", "users"}:
            user_rows = conn.execute(
                """
                SELECT entity_id, COALESCE(MAX(name), CONCAT('用户', entity_id)) AS name, SUM(delta) AS total
                FROM traffic_delta_samples
                WHERE sampled_at BETWEEN ? AND ? AND kind = 'user'
                GROUP BY entity_id
                ORDER BY total DESC
                LIMIT ?
                """.replace("CONCAT('用户', entity_id)", "'用户' || entity_id"),
 (start_ts, end_ts, safe_limit),
            ).fetchall()
        if dimension in {"combined", "nodes"}:
            node_rows = conn.execute(
                """
                SELECT entity_id, COALESCE(MAX(name), '节点' || entity_id) AS name, SUM(delta) AS total
                FROM traffic_delta_samples
                WHERE sampled_at BETWEEN ? AND ? AND kind = 'node'
                GROUP BY entity_id
                ORDER BY total DESC
                LIMIT ?
                """,
 (start_ts, end_ts, safe_limit),
            ).fetchall()
    return int(total_row["total"] or 0), [dict(r) for r in user_rows], [dict(r) for r in node_rows], earliest_traffic_sample_at_sync(cache_path)


def traffic_sample_gap_warning_for_range_sync(cache_path: Path, start_ts: int, end_ts: int, period_label: str) -> str | None:
    """Return a warning only when a sampling gap crosses a stats boundary.

    Traffic totals are calculated from cumulative counter deltas. A gap fully
    inside the selected period is normally captured by the next successful
    sample, so warning on every overlap is noisy and misleading. Boundary-crossing
    gaps can shift traffic into or out of the selected window, so only those are
    surfaced.
    """
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        rows = conn.execute(
            """
            SELECT gap_start_at, gap_end_at, gap_seconds
            FROM traffic_sample_gaps
            WHERE gap_start_at < ? AND gap_end_at > ?
            ORDER BY gap_seconds DESC, gap_end_at DESC
            """,
 (end_ts, start_ts),
        ).fetchall()
    boundary_rows = [
        row for row in rows
        if int(row["gap_start_at"] or 0) < start_ts < int(row["gap_end_at"] or 0)
        or int(row["gap_start_at"] or 0) < end_ts < int(row["gap_end_at"] or 0)
    ]
    if not boundary_rows:
        return None

    longest = max(int(row["gap_seconds"] or 0) for row in boundary_rows)
    first_start = min(int(row["gap_start_at"] or 0) for row in boundary_rows)
    last_end = max(int(row["gap_end_at"] or 0) for row in boundary_rows)
    if len(boundary_rows) == 1:
        gap_text = format_duration(longest)
    else:
        gap_text = f"共 {len(boundary_rows)} 次，最长 {format_duration(longest)}"
    return (
        f"⚠️ 统计边界附近存在采样中断 ({gap_text})，"
        f"时段约 {format_timestamp(first_start)} - {format_timestamp(last_end)}；"
        "由于累计值可能被记入相邻窗口，本周期流量可能存在边界偏差。"
    )


def render_traffic_dashboard_text(
    title: str,
    period_label: str,
    start_ts: int,
    end_ts: int,
    cache_path: Path,
    limit: int = 10,
    dimension: str = "combined",
) -> str:
    safe_limit = max(1, min(limit, 50))
    dimension = dimension if dimension in {"combined", "users", "nodes"} else "combined"
    grand_total, user_rows, node_rows, first_sample = query_traffic_deltas_range_from_cache_sync(cache_path, start_ts, end_ts, safe_limit, dimension)
    gap_warning = traffic_sample_gap_warning_for_range_sync(cache_path, start_ts, end_ts, period_label)
    lines = [
        f"<b>{title}</b>",
        "",
        f"🌊 总流量：{format_bytes(grand_total)}",
        f"🕒 最后更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "────────────",
        "",
    ]
    if first_sample:
        covered_start = max(start_ts, first_sample)
        covered_end = min(end_ts, int(datetime.now().timestamp()))
        covered_seconds = max(0, covered_end - covered_start)
        period_seconds = max(1, end_ts - start_ts)
        if covered_seconds < period_seconds:
            lines.extend([
                f"📡 采样覆盖：{format_duration(covered_seconds)}",
                "⚠️ 当前统计周期内采样未覆盖完整窗口，统计可能存在偏差。",
                "",
            ])
    if gap_warning:
        lines.extend([gap_warning, ""])

    if dimension in {"combined", "users"}:
        lines.append(f"🏅 <b>用户流量 Top {len(user_rows)}</b>")
        if user_rows:
            for index, row in enumerate(user_rows, start=1):
                entity_id = row.get("entity_id")
                name = html.escape(str(row.get("name") or f"用户{entity_id}"))
                user_id = html.escape(str(entity_id or ""))
                lines.append(f"{index}. {name} (user_id: {user_id})：{format_bytes(row.get('total'))}")
        else:
            lines.append("暂无用户流量记录。")

    if dimension in {"combined", "nodes"}:
        if dimension == "combined":
            lines.extend(["", "────────────", ""])
        lines.append(f"🏅 <b>节点流量 Top {len(node_rows)}</b>")
        if node_rows:
            for index, row in enumerate(node_rows, start=1):
                name = html.escape(safe_autolink_text(str(row.get("name") or f"节点{row.get('entity_id')}")))
                lines.append(f"{index}. {name}：{format_bytes(row.get('total'))}")
        else:
            lines.append("暂无节点流量记录。")
    result = "\n".join(lines).strip()
    if len(result) > 3900:
        result = result[:3850].rstrip() + "\n\n……内容过长，已截断。"
    return result


def ip_alert_row_for_user_sync(cache_path: Path, xboard_user_id: int) -> dict[str, Any] | None:
    for row in ip_alert_rows_sync(cache_path):
        if int(row.get("user_id") or 0) == int(xboard_user_id):
            return row
    return None


def traffic_range_kind_from_cache_sync(cache_path: Path, kind: str) -> dict[str, Any] | None:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        row = conn.execute(
            "SELECT kind, start_ts, end_ts, label, created_at FROM traffic_ranges WHERE kind = ?",
 (kind,),
        ).fetchone()
    return dict(row) if row else None


def save_traffic_range_sync(cache_path: Path, kind: str, start_ts: int, end_ts: int, label: str) -> None:
    init_cache(cache_path)
    now_ts = int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        conn.execute(
            """
            INSERT INTO traffic_ranges(kind, start_ts, end_ts, label, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(kind) DO UPDATE SET
                start_ts=excluded.start_ts,
                end_ts=excluded.end_ts,
                label=excluded.label,
                created_at=excluded.created_at
            """,
 (kind, start_ts, end_ts, label, now_ts),
        )


def make_range_kind(start_ts: int, end_ts: int, label: str) -> str:
    digest = hashlib.sha1(f"{start_ts}:{end_ts}:{label}".encode("utf-8")).hexdigest()[:12]
    return f"range_{digest}"


def traffic_dimension_from_kind(kind: str) -> str:
    if kind.startswith("users_"):
        return "users"
    if kind.startswith("nodes_"):
        return "nodes"
    return "combined"


def traffic_base_kind(kind: str) -> str:
    if kind.startswith("users_"):
        base = kind.removeprefix("users_")
    elif kind.startswith("nodes_"):
        base = kind.removeprefix("nodes_")
    else:
        base = kind
    legacy_periods = {"1h": "preset_1h", "24h": "preset_24h", "7d": "preset_7d", "30d": "preset_30d"}
    return legacy_periods.get(base, base)


def traffic_kind_for_dimension(dimension: str, base_kind: str) -> str:
    dimension = dimension if dimension in {"combined", "users", "nodes"} else "combined"
    if dimension == "users":
        return f"users_{base_kind}"
    if dimension == "nodes":
        return f"nodes_{base_kind}"
    return base_kind


def traffic_title_for_dimension(label: str, dimension: str) -> str:
    if dimension == "users":
        return f"📈 {label} 用户流量统计"
    if dimension == "nodes":
        return f"📈 {label} 节点流量统计"
    return f"📈 {label} 流量统计"


def traffic_dashboard_text_from_kind_sync(cache_path: Path, kind: str) -> str:
    if kind.startswith("ip_") or kind.startswith("iprange_"):
        return ip_monitor_text_from_kind_sync(cache_path, kind)
    now_ts = int(datetime.now().timestamp())
    dimension = traffic_dimension_from_kind(kind)
    base_kind = traffic_base_kind(kind)
    presets = {
        "combined": ("近 24 小时", now_ts - 24 * 3600, now_ts),
        "preset_1h": ("近 1 小时", now_ts - 3600, now_ts),
        "preset_24h": ("近 24 小时", now_ts - 24 * 3600, now_ts),
        "preset_7d": ("近 7 天", now_ts - 7 * 24 * 3600, now_ts),
        "preset_30d": ("近 30 天", now_ts - 30 * 24 * 3600, now_ts),
    }
    if base_kind in presets:
        label, start_ts, end_ts = presets[base_kind]
        title = traffic_title_for_dimension(label, dimension)
        return render_traffic_dashboard_text(title, label, start_ts, end_ts, cache_path, dimension=dimension)
    if base_kind.startswith("range_"):
        range_kind = traffic_range_kind_from_cache_sync(cache_path, base_kind)
        if not range_kind:
            return "请求无效，请重新进入。"
        label = str(range_kind["label"])
        return render_traffic_dashboard_text(
            traffic_title_for_dimension(label, dimension),
            label,
            int(range_kind["start_ts"]),
            int(range_kind["end_ts"]),
            cache_path,
            dimension=dimension,
        )
    return "请求无效，请重新进入。"


def fetch_user_display_details_sync(cfg: MySQLConfig, user_ids: set[int]) -> dict[int, dict[str, str]]:
    """Fetch Xboard user display fields using read-only SELECT."""
    if not user_ids or mysql_config_missing(cfg):
        return {}

    placeholders = ", ".join(["%s"] * len(user_ids))
    sql = f"SELECT id, remarks, email FROM v2_user WHERE id IN ({placeholders})"
    conn = mysql_connect(cfg)
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, tuple(sorted(user_ids)))
            rows = cursor.fetchall()
    finally:
        conn.close()

    details: dict[int, dict[str, str]] = {}
    for row in rows:
        xboard_user_id = int(row.get("id") or 0)
        remarks = str(row.get("remarks") or "").strip().replace("\n", " ")[:80]
        email = str(row.get("email") or "").strip().replace("\n", " ")[:120]
        display_name = (remarks or email)[:80]
        if xboard_user_id:
            details[xboard_user_id] = {"display_name": display_name, "remarks": remarks, "email": email}
    return details


def fetch_all_user_display_details_sync(cfg: MySQLConfig) -> dict[int, dict[str, str]]:
    """Fetch all Xboard users for configuration lists using read-only SELECT."""
    if mysql_config_missing(cfg):
        return {}

    sql = "SELECT id, remarks, email FROM v2_user ORDER BY id ASC"
    conn = mysql_connect(cfg)
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()
    finally:
        conn.close()

    details: dict[int, dict[str, str]] = {}
    for row in rows:
        xboard_user_id = int(row.get("id") or 0)
        remarks = str(row.get("remarks") or "").strip().replace("\n", " ")[:80]
        email = str(row.get("email") or "").strip().replace("\n", " ")[:120]
        display_name = (remarks or email)[:80]
        if xboard_user_id:
            details[xboard_user_id] = {"display_name": display_name, "remarks": remarks, "email": email}
    return details


def upsert_all_cache_users(cache_path: Path, mysql_cfg: MySQLConfig) -> None:
    names = fetch_all_user_display_details_sync(mysql_cfg)
    if not names:
        return
    now_ts = int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        for user_id, row in names.items():
            conn.execute(
                """
                INSERT INTO users(user_id, display_name, remarks, email, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    remarks=excluded.remarks,
                    email=excluded.email,
                    updated_at=excluded.updated_at
                """,
 (user_id, row["display_name"], row["remarks"], row["email"], now_ts),
            )


def format_timestamp(ts: int | None) -> str:
    if not ts:
        return "未知"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def format_age(ts: int | None) -> str:
    if not ts:
        return "未知"
    seconds = int(datetime.now().timestamp()) - int(ts)
    if seconds <= 5:
        return "刚刚"
    return f"{format_duration(seconds)}前"


def format_health_age(ts: int | None) -> str:
    if not ts:
        return "未知"
    seconds = max(0, int(datetime.now().timestamp()) - int(ts))
    if seconds >= 86400:
        return f"{max(1, seconds // 86400)}天前"
    return format_age(ts)


def format_age_with_time(ts: int | None) -> str:
    if not ts:
        return "未知"
    return f"{format_health_age(ts)} ({format_timestamp(ts)})"


def earliest_cache_collect_at_sync(cache_path: Path) -> int | None:
    init_cache(cache_path)
    floor_ts = get_stats_floor_ts_sync(cache_path) or 0
    with cache_connect(cache_path) as conn:
        row = conn.execute(
            """
            SELECT MIN(ts) AS first_ts FROM (
                SELECT first_seen_at AS ts FROM active_ip_records WHERE first_seen_at > 0
                UNION ALL
                SELECT updated_at AS ts FROM users WHERE updated_at > 0
                UNION ALL
                SELECT queried_at AS ts FROM ip_geo_cache WHERE queried_at > 0
            )
            WHERE ts >= ?
            """,
 (floor_ts,),
        ).fetchone() or {}
    return int(row["first_ts"]) if row and row["first_ts"] is not None else None


def compact_connection_error_lines(result: str) -> list[str]:
    raw_lines = [line.strip() for line in result.splitlines() if line.strip()]
    summary_candidates = [line for line in raw_lines[1:] if line.startswith("❌") and "错误类型" not in line and "错误代码" not in line]
    lines: list[str] = []
    if summary_candidates:
        lines.append(f"　{summary_candidates[0]}")
    for line in raw_lines:
        if "错误类型" in line or "错误代码" in line:
            lines.append(f"　{line}")
    reason_candidates = [line for line in summary_candidates[1:] if "可能" not in line and "常见原因" not in line]
    if reason_candidates:
        lines.append(f"　{reason_candidates[-1]}")
    return lines


def connection_check_lines_sync(cfg: AppConfig, cache_path: Path) -> tuple[list[str], bool, bool, bool]:
    mysql_result = test_mysql_connection_sync(cfg.mysql)
    redis_result = test_redis_connection_sync(cfg.redis)
    mysql_ok = mysql_result.startswith("✅")
    redis_ok = redis_result.startswith("✅")
    sqlite_ok = True
    sqlite_detail = "可读写"
    try:
        init_cache(cache_path)
        with cache_connect(cache_path) as conn:
            conn.execute("SELECT 1").fetchone()
            quick = conn.execute("PRAGMA quick_check").fetchone()
            if quick and str(quick[0]).lower() != "ok":
                sqlite_ok = False
                sqlite_detail = html_code(str(quick[0]))
    except Exception as exc:
        sqlite_ok = False
        sqlite_detail = html_code(type(exc).__name__)

    mysql_summary = re.sub(r"^[✅❌]\s*", "", mysql_result.splitlines()[0])
    redis_summary = re.sub(r"^[✅❌]\s*", "", redis_result.splitlines()[0])
    lines = [
        f"{'🟢' if mysql_ok else '🔴'} {mysql_summary}",
    ]
    if not mysql_ok:
        lines.extend(compact_connection_error_lines(mysql_result))
    lines.append(f"{'🟢' if redis_ok else '🔴'} {redis_summary}")
    if not redis_ok:
        lines.extend(compact_connection_error_lines(redis_result))
    lines.append(f"{'🟢' if sqlite_ok else '🔴'} SQLite {sqlite_detail}")
    return (lines, mysql_ok, redis_ok, sqlite_ok)


def bot_health_overview_text_sync(cfg: AppConfig, cache_path: Path, admin_view: bool = True) -> str:
    counts = get_cache_counts_sync(cache_path)
    geo_status = cache_geo_status_sync(cache_path)
    geo_pending_text = format_geo_pending_text(geo_status["geo_pending"], cfg.ip_geo_queries_per_minute)
    cache_size = cache_path.stat().st_size if cache_path.exists() else 0
    uptime_seconds = int((datetime.now() - PROCESS_STARTED_AT).total_seconds())
    collect_state = get_collector_state_sync(cache_path, "last_collect_at")
    traffic_state = get_collector_state_sync(cache_path, "last_traffic_sample_at")
    first_collect_state = get_collector_state_sync(cache_path, "first_collect_at")
    first_collect_at = first_collect_state[1] if first_collect_state else earliest_cache_collect_at_sync(cache_path)
    first_traffic_at = earliest_traffic_sample_at_sync(cache_path)
    connection_lines, _, _, sqlite_ok = connection_check_lines_sync(cfg, cache_path)
    if not admin_view:
        connection_lines = [redact_sensitive_text_for_non_admin(line).replace("端口", "服务") for line in connection_lines]

    now_ts = int(datetime.now().timestamp())
    collect_ts = collect_state[1] if collect_state else None
    traffic_ts = traffic_state[1] if traffic_state else None
    collect_lag = now_ts - collect_ts if collect_ts else None
    traffic_lag = now_ts - traffic_ts if traffic_ts else None
    collect_ok = collect_lag is not None and collect_lag <= max(180, int(cfg.collector_interval_seconds * 3))
    traffic_ok = traffic_lag is not None and traffic_lag <= 180

    lines = [
        "🩺 <b>健康检查</b>",
        "────────────",
        "🤖 <b>服务启动状态</b>",
        f"启动时间：{PROCESS_STARTED_AT.strftime('%Y-%m-%d %H:%M:%S')}",
        f"运行时长：{format_duration(uptime_seconds)}",
        "",
        "🔗 <b>连接检查</b>",
        *connection_lines,
        "",
        "📦 <b>缓存采集</b>",
        f"缓存文件：{format_bytes(cache_size)}",
        "",
        f"首次缓存采集：{format_age_with_time(first_collect_at)}",
        f"最后缓存采集：{format_age_with_time(collect_ts)}",
        "",
        f"IP 缓存：{counts['active_ips']} 条",
        f"IP 归属地缓存：{geo_status['geo_total']} 条 ({geo_pending_text})",
        f"用户信息缓存：{counts['users']} 个",
        "",
        f"首次流量采样：{format_age_with_time(first_traffic_at)}",
        f"最后流量采样：{format_age_with_time(traffic_ts)}",
    ]
    if not sqlite_ok:
        lines.append("\n⚠️ SQLite 异常时，缓存统计可能不完整。")
    text = "\n".join(lines)
    if not admin_view:
        text += "\n\n敏感连接信息已隐藏，仅管理员可查看完整详情。"
    return text


def cached_user_display_name(row: sqlite3.Row | None, xboard_user_id: int) -> str:
    display_name = str(row["display_name"] or "").strip() if row else ""
    return display_name or f"用户{xboard_user_id}"


def cached_user_button_label(row: sqlite3.Row | None, xboard_user_id: int) -> str:
    display_name = str(row["display_name"] or "").strip() if row else ""
    if display_name:
        return f"{display_name} (user_id: {xboard_user_id})"
    return f"用户 {xboard_user_id}"


def render_cached_user_label(row: sqlite3.Row | None, xboard_user_id: int) -> str:
    display_name = str(row["display_name"] or "").strip() if row else ""
    if display_name:
        return f"{html.escape(display_name)} (user_id: {html.escape(str(xboard_user_id))})"
    return f"用户 {html.escape(str(xboard_user_id))}"



def cached_user_name_by_id(cache_path: Path, xboard_user_id: int) -> str:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        row = conn.execute("SELECT display_name FROM users WHERE user_id = ?", (xboard_user_id,)).fetchone()
    return cached_user_display_name(row, xboard_user_id)


def render_user_label(user_id_value: Any, display_name_value: Any = None) -> str:
    xboard_user_id = int(user_id_value or 0)
    display_name = str(display_name_value or "").strip()
    if display_name and display_name != f"用户{xboard_user_id}":
        return f"{html.escape(display_name)} (user_id: {html.escape(str(xboard_user_id))})"
    return f"用户 {html.escape(str(xboard_user_id))}"

def bot_status_text_sync(cfg: AppConfig, cache_path: Path) -> str:
    counts = get_cache_counts_sync(cache_path)
    collect_state = get_collector_state_sync(cache_path, "last_collect_at")
    traffic_state = get_collector_state_sync(cache_path, "last_traffic_sample_at")
    cleared_state = get_collector_state_sync(cache_path, "last_active_ip_records_cleared_at")
    stats_floor = get_stats_floor_ts_sync(cache_path)
    geo_status = cache_geo_status_sync(cache_path)
    cache_size = cache_path.stat().st_size if cache_path.exists() else 0
    uptime_seconds = int((datetime.now() - PROCESS_STARTED_AT).total_seconds())

    lines = [
        "🟢 <b>Bot 运行状态</b>",
        "────────────",
        f"运行时长：{format_duration(uptime_seconds)}",
        f"进程启动：{PROCESS_STARTED_AT.strftime('%Y-%m-%d %H:%M:%S')}",
        f"缓存文件：{format_bytes(cache_size)}",
        "",
        f"最后 Redis 缓存采集：{format_age(collect_state[1] if collect_state else None)}",
        f"最后流量采样：{format_age(traffic_state[1] if traffic_state else None)}",
        f"最近清空用户 IP 记录：{format_timestamp(cleared_state[1]) if cleared_state else '未清空'}",
        f"统计起始点：{format_timestamp(stats_floor) if stats_floor else '未手动重置'}",
        f"采集间隔：{format_duration(int(cfg.collector_interval_seconds))}",
        "",
        f"活跃 IP 缓存：{counts['active_ips']} 条",
        f"用户缓存：{counts['users']} 个",
        f"IP 归属地缓存：{geo_status['geo_total']} 条 (待补全 {geo_status['geo_pending']} 条)",
        f"流量增量样本：{counts['traffic_samples']} 条",
        f"流量面板消息：{counts['pinned_dashboards']} 条",
    ]
    return "\n".join(lines)



def geo_text(row: sqlite3.Row) -> str:
    raw_parts = [str(row_value(row, name) or "").strip() for name in ("country", "region", "city", "district", "isp")]
    parts: list[str] = []
    for part in raw_parts:
        if part and part not in parts:
            parts.append(part)
    return "，".join(parts)


def geo_location_text(row: sqlite3.Row) -> str:
    raw_parts = [str(row_value(row, name) or "").strip() for name in ("country", "region", "city", "district")]
    parts: list[str] = []
    for part in raw_parts:
        if part and part not in parts:
            parts.append(part)
    return "，".join(parts)


def asn_text(row: sqlite3.Row) -> str:
    raw = raw_geo_data(row)
    return asn_label_from_raw(raw) or str(row["isp"] or "").strip() or "待查询"


def safe_autolink_text(value: str) -> str:
    """Prevent Telegram from auto-linking domain-like fragments in plain text."""
    # Telegram may auto-link strings such as Alibaba.com even inside HTML messages.
    # A zero-width space after dots keeps the text readable but breaks autolink detection.
    return value.replace(".", ".\u200b")


def geo_area_key(row: sqlite3.Row) -> str | None:
    """Return a city-level area key for de-duplicated active area counting."""
    stat_area_key = str(row_value(row, "stat_area_key") or "").strip()
    if stat_area_key:
        return stat_area_key
    raw = raw_geo_data(row)
    if raw:
        stat_area = build_geo_stat_area(raw)
        if stat_area.get("key"):
            return stat_area["key"]
    country = str(row_value(row, "country") or "").strip()
    region = str(row_value(row, "region") or "").strip()
    city = str(row_value(row, "city") or "").strip()
    if city:
        return "|".join(part for part in (country, region, city) if part)
    if region:
        return "|".join(part for part in (country, region) if part)
    if country:
        return country
    return None


def count_geo_areas(rows: list[sqlite3.Row]) -> int:
    return len({key for row in rows if (key := geo_area_key(row))})


IP_PERIODS: dict[str, tuple[str, int]] = {
    "1h": ("近 1 小时", 3600),
    "24h": ("近 24 小时", 24 * 3600),
    "7d": ("近 7 天", 7 * 24 * 3600),
    "30d": ("近 30 天", 30 * 24 * 3600),
}


def ip_range_kind(start_ts: int, end_ts: int) -> str:
    return f"iprange_{start_ts}_{end_ts}"


def parse_ip_kind(kind: str) -> tuple[str, int | None, int | None] | None:
    now_ts = int(datetime.now().timestamp())
    if kind.startswith("ip_"):
        key = kind.removeprefix("ip_")
        if key not in IP_PERIODS:
            return None
        label, seconds = IP_PERIODS[key]
        return label, now_ts - seconds, now_ts
    match = re.fullmatch(r"iprange_(\d+)_(\d+)", kind)
    if match:
        start_ts = int(match.group(1))
        end_ts = int(match.group(2))
        return "自定区间", start_ts, end_ts
    return None


def cached_active_user_rows_between(
    cache_path: Path,
    start_ts: int,
    end_ts: int | None = None,
) -> tuple[list[int], dict[int, list[sqlite3.Row]], dict[int, sqlite3.Row]]:
    init_cache(cache_path)
    end_ts = end_ts or int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        rows = conn.execute(
            """
            SELECT a.user_id, a.ip, a.last_seen_at, u.display_name,
                   g.country, g.region, g.city, g.district, g.isp, g.stat_area_key, g.stat_area_name, g.stat_area_level, g.raw
            FROM active_ip_records AS a
            LEFT JOIN users AS u ON u.user_id = a.user_id
            LEFT JOIN ip_geo_cache AS g ON g.ip = a.ip
            WHERE a.ignored_at IS NULL AND a.last_seen_at BETWEEN ? AND ?
            ORDER BY a.user_id ASC, a.last_seen_at DESC, a.ip ASC
            """,
 (start_ts, end_ts),
        ).fetchall()

    grouped: dict[int, list[sqlite3.Row]] = {}
    user_rows: dict[int, sqlite3.Row] = {}
    for row in rows:
        xboard_user_id = int(row["user_id"])
        grouped.setdefault(xboard_user_id, []).append(row)
        user_rows.setdefault(xboard_user_id, row)
    ordered_user_ids = sorted(grouped, key=lambda uid: (-len(grouped[uid]), uid))
    return ordered_user_ids, grouped, user_rows


def cached_active_user_rows(cache_path: Path, window: timedelta) -> tuple[list[int], dict[int, list[sqlite3.Row]], dict[int, sqlite3.Row]]:
    cutoff_ts = int((datetime.now() - window).timestamp())
    return cached_active_user_rows_between(cache_path, cutoff_ts)


def active_user_button_items_from_cache_sync(
    cache_path: Path,
    window: timedelta | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> list[tuple[int, str]]:
    if start_ts is not None:
        ordered_user_ids, _grouped, user_rows = cached_active_user_rows_between(cache_path, start_ts, end_ts)
    elif window is not None:
        ordered_user_ids, _grouped, user_rows = cached_active_user_rows(cache_path, window)
    else:
        ordered_user_ids, _grouped, user_rows = [], {}, {}
    return [(user_id, cached_user_button_label(user_rows.get(user_id), user_id)[:48]) for user_id in ordered_user_ids]


def list_user_ips_from_cache_sync(
    cache_path: Path,
    label: str,
    window: timedelta | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> str:
    if start_ts is not None:
        ordered_user_ids, grouped, user_rows = cached_active_user_rows_between(cache_path, start_ts, end_ts)
    elif window is not None:
        ordered_user_ids, grouped, user_rows = cached_active_user_rows(cache_path, window)
    else:
        ordered_user_ids, grouped, user_rows = [], {}, {}

    if start_ts is not None and end_ts is not None and label == "自定区间":
        lines = [
            "🗺 <b>自定区间用户活跃度概览</b>",
            f"时间区间：{datetime.fromtimestamp(start_ts).strftime('%Y-%m-%d %H:%M')} - {datetime.fromtimestamp(end_ts).strftime('%Y-%m-%d %H:%M')}",
            "────────────",
            PROXY_PROTOCOL_NOTICE,
            "",
        ]
    else:
        lines = [
            f"🌐 <b>{label} 用户活跃度概览</b>",
            "────────────",
            PROXY_PROTOCOL_NOTICE,
            "",
        ]
    if not ordered_user_ids:
        lines.extend([
            f"暂无 {label} 在线 IP 记录。",
            "",
            "缓存可能尚未完成首次采集，请稍后再试。",
        ])
    else:
        all_rows = [row for user_id in ordered_user_ids for row in grouped[user_id]]
        lines.extend([
            f"👥 活跃用户：{len(grouped)} 个",
            f"🌐 活跃 IP：{len(all_rows)} 个",
            f"📍 活跃地区：{count_geo_areas(all_rows)} 个",
            "",
            f"🗺 活跃用户<b> Top {len(grouped)}</b>",
        ])
        for xboard_user_id in ordered_user_ids:
            user_ip_rows = grouped[xboard_user_id]
            lines.append(
                f"• {render_cached_user_label(user_rows[xboard_user_id], xboard_user_id)}："
                f"活跃 IP {len(user_ip_rows)} 个，活跃地区 {count_geo_areas(user_ip_rows)} 个；"
            )
    result = "\n".join(lines).strip()
    if len(result) > 3900:
        result = result[:3850].rstrip() + "\n\n……内容过长，已截断。"
    return result


def ip_monitor_text_from_kind_sync(cache_path: Path, kind: str) -> str:
    parsed = parse_ip_kind(kind)
    if not parsed:
        return "请求无效，请重新进入。"
    label, start_ts, end_ts = parsed
    return list_user_ips_from_cache_sync(cache_path, label, None, start_ts, end_ts)


def render_cached_ip_bucket(title: str, rows: list[sqlite3.Row], shown_ips: set[str], cutoff_ts: int) -> list[str]:
    bucket_rows: list[sqlite3.Row] = []
    for row in rows:
        ip = str(row["ip"])
        if ip in shown_ips:
            continue
        if int(row["last_seen_at"]) < cutoff_ts:
            continue
        bucket_rows.append(row)
        shown_ips.add(ip)

    lines = [f"🌐 <b>{title}活跃 IP {len(bucket_rows)} 个，活跃地区 {count_geo_areas(bucket_rows)} 个</b>", ""]
    if not bucket_rows:
        return lines[:-1]
    for index, row in enumerate(bucket_rows, start=1):
        ip = str(row["ip"])
        location = geo_location_text(row) or "待查询"
        safe_location = html.escape(safe_autolink_text(location))
        safe_asn = html.escape(safe_autolink_text(asn_text(row)))
        lines.extend([
            f"{index}. <code>{html.escape(ip)}/24</code>",
            f"📍地区：{safe_location}",
            f"🏷️ ASN：{safe_asn}",
            f"🕒最后活跃时间：{html.escape(format_timestamp(int(row['last_seen_at'])))}",
            "────────────",
        ])
    if lines[-1] == "────────────":
        lines.pop()
    return lines


def render_user_ip_rows_page(
    user_label: str,
    label: str,
    rows: list[sqlite3.Row],
    page: int = 0,
    page_size: int = 10,
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> str:
    safe_page_size = max(1, min(page_size, 50))
    total = len(rows)
    total_pages = max(1, (total + safe_page_size - 1) // safe_page_size)
    page = min(max(page, 0), total_pages - 1)
    start = page * safe_page_size
    page_rows = rows[start:start + safe_page_size]
    if start_ts is not None and end_ts is not None and label == "自定区间":
        lines = [
            f"{user_label}",
            f"时间区间：{datetime.fromtimestamp(start_ts).strftime('%Y-%m-%d %H:%M')} - {datetime.fromtimestamp(end_ts).strftime('%Y-%m-%d %H:%M')}",
            f"活跃 IP {total} 个，活跃地区 {count_geo_areas(rows)} 个",
            "────────────",
            "",
        ]
    else:
        lines = [
            f"{user_label} {label}活跃 IP {total} 个，活跃地区 {count_geo_areas(rows)} 个",
            "────────────",
            "",
        ]
    if not page_rows:
        lines.append("暂无符合条件的活跃 IP。")
        return "\n".join(lines).strip()
    for index, row in enumerate(page_rows, start=start + 1):
        ip = str(row["ip"])
        location = geo_location_text(row) or "待查询"
        safe_location = html.escape(safe_autolink_text(location))
        safe_asn = html.escape(safe_autolink_text(asn_text(row)))
        lines.extend([
            f"{index}. <code>{html.escape(ip)}/24</code>",
            f"📍地区：{safe_location}",
            f"🏷️ ASN：{safe_asn}",
            f"🕒最后活跃时间：{html.escape(format_timestamp(int(row['last_seen_at'])))}",
            "────────────",
        ])
    if lines[-1] == "────────────":
        lines.pop()
    return "\n".join(lines).strip()


def count_user_ips_from_cache_sync(
    cache_path: Path,
    xboard_user_id: int,
    window: timedelta | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> int:
    init_cache(cache_path)
    if start_ts is None and window is not None:
        start_ts = int((datetime.now() - window).timestamp())
    end_ts = end_ts or int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        if start_ts is not None:
            row = conn.execute(
                """
                SELECT COUNT(DISTINCT ip) AS total
                FROM active_ip_records
                WHERE user_id = ? AND ignored_at IS NULL AND last_seen_at BETWEEN ? AND ?
                """,
 (xboard_user_id, start_ts, end_ts),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(DISTINCT ip) AS total FROM active_ip_records WHERE user_id = ? AND ignored_at IS NULL",
 (xboard_user_id,),
            ).fetchone()
    return int(row["total"] if row and row["total"] is not None else 0)


def query_user_ips_from_cache_sync(
    cache_path: Path,
    xboard_user_id: int,
    label: str | None = None,
    window: timedelta | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
    page: int = 0,
    page_size: int = 10,
) -> str:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        user_row = conn.execute("SELECT display_name FROM users WHERE user_id = ?", (xboard_user_id,)).fetchone()
        rows = conn.execute(
            """
            SELECT a.ip, a.last_seen_at, g.country, g.region, g.city, g.district, g.isp, g.stat_area_key, g.stat_area_name, g.stat_area_level, g.raw
            FROM active_ip_records AS a
            LEFT JOIN ip_geo_cache AS g ON g.ip = a.ip
            WHERE a.user_id = ? AND a.ignored_at IS NULL
            ORDER BY a.last_seen_at DESC, a.ip ASC
            """,
 (xboard_user_id,),
        ).fetchall()

    if label and (window or start_ts is not None):
        if start_ts is None:
            start_ts = int((datetime.now() - window).timestamp()) if window else 0
        end_ts = end_ts or int(datetime.now().timestamp())
        filtered_rows = [row for row in rows if start_ts <= int(row["last_seen_at"]) <= end_ts]
        return render_user_ip_rows_page(
            render_cached_user_label(user_row, xboard_user_id),
            label,
            filtered_rows,
            page,
            page_size,
            start_ts,
            end_ts,
        )
    else:
        now = datetime.now()
        lines = [f"👤 {render_cached_user_label(user_row, xboard_user_id)}", "────────────", ""]
        shown_ips: set[str] = set()
        lines.extend(render_cached_ip_bucket("近 1 小时", rows, shown_ips, int((now - timedelta(hours=1)).timestamp())))
        lines.append("")
        lines.extend(render_cached_ip_bucket("近 24 小时", rows, shown_ips, int((now - timedelta(hours=24)).timestamp())))
        lines.append("")
        lines.extend(render_cached_ip_bucket("近 7 天", rows, shown_ips, int((now - timedelta(days=7)).timestamp())))

    result = "\n".join(lines).strip()
    if len(result) > 3900:
        result = result[:3850].rstrip() + "\n\n……内容过长，已截断。"
    return result


def user_ip_page_rows_sync(
    cache_path: Path,
    xboard_user_id: int,
    kind: str,
    page: int = 0,
    page_size: int = 10,
) -> list[sqlite3.Row]:
    parsed = parse_ip_kind(kind)
    if not parsed:
        return []
    _label, start_ts, end_ts = parsed
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        rows = conn.execute(
            """
            SELECT a.ip, a.last_seen_at, g.country, g.region, g.city, g.district, g.isp, g.stat_area_key, g.stat_area_name, g.stat_area_level, g.raw
            FROM active_ip_records AS a
            LEFT JOIN ip_geo_cache AS g ON g.ip = a.ip
            WHERE a.user_id = ? AND a.ignored_at IS NULL AND a.last_seen_at BETWEEN ? AND ?
            ORDER BY a.last_seen_at DESC, a.ip ASC
            """,
 (xboard_user_id, start_ts, end_ts or int(datetime.now().timestamp())),
        ).fetchall()
    safe_page_size = max(1, min(page_size, 50))
    safe_page = max(0, page)
    return rows[safe_page * safe_page_size:(safe_page + 1) * safe_page_size]


def ignore_items_from_ip_rows(rows: list[sqlite3.Row], dimension: str) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        if dimension == "area":
            value = geo_area_key(row)
            if not value:
                continue
            label = " / ".join(value.split("|"))
        elif dimension == "asn":
            value = asn_key_for_geo_row(row)
            if not value:
                continue
            label = asn_text(row)
        elif dimension == "cidr":
            value = ipv4_24_cidr(str(row["ip"] or ""))
            if not value:
                continue
            label = value
        else:
            continue
        bucket = buckets.setdefault(value, {"value": value, "label": label, "ips": set(), "last_seen_at": 0})
        bucket["ips"].add(str(row["ip"]))
        bucket["last_seen_at"] = max(int(bucket["last_seen_at"]), int(row["last_seen_at"] or 0))
    return [
        {"value": value, "label": str(bucket["label"]), "sub": f"{len(bucket['ips'])} IP", "last_seen_at": int(bucket["last_seen_at"])}
        for value, bucket in sorted(buckets.items(), key=lambda item: (-int(item[1]["last_seen_at"]), item[0]))
    ]


def user_ip_ignore_items_sync(cache_path: Path, xboard_user_id: int, kind: str, page: int, dimension: str) -> list[dict[str, Any]]:
    return ignore_items_from_ip_rows(user_ip_page_rows_sync(cache_path, xboard_user_id, kind, page), dimension)


def test_mysql_connection_sync(cfg: MySQLConfig) -> str:
    """Return a user-facing MySQL diagnosis message.

    MySQL access is intentionally read-only at application level: every SQL here
    is SELECT against metadata/current database. No INSERT/UPDATE/DELETE/DDL path
    exists in the bot.
    """
    if mysql_config_missing(cfg):
        return "⚠️ MySQL 连接失败\n\n❌ MySQL 连接信息未输入完整。"

    host = cfg.host.strip()
    port = int(cfg.port)
    ok, tcp_lines = tcp_check(host, port, "MySQL")
    if not ok:
        return "\n".join(["⚠️ MySQL 连接失败", "", *tcp_lines])

    try:
        conn = mysql_connect(cfg)
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT VERSION() AS version, DATABASE() AS database_name")
                row = cursor.fetchone() or {}
                cursor.execute(
                    "SELECT COUNT(*) AS table_count FROM information_schema.tables WHERE table_schema = %s",
 (cfg.database.strip(),),
                )
                table_row = cursor.fetchone() or {}
        finally:
            conn.close()
    except MySQLError as exc:
        errno = exc.args[0] if exc.args else "unknown"
        if errno in (1044, 1045):
            reason = "❌ MySQL 账号、密码或数据库权限不正确。"
        elif errno == 1049:
            reason = "❌ MySQL 数据库不存在或当前账号无权访问。"
        elif errno in (2003, 2006, 2013):
            reason = "❌ MySQL 服务连接中断或无法建立连接。"
        else:
            reason = "❌ MySQL 返回错误，请根据错误代码继续分析。"
        return "\n".join([
            "⚠️ MySQL 连接失败",
            "",
            "✅ MySQL 端口可访问。",
            "❌ MySQL 登录或查询失败。",
            f"❌ 错误类型：{html_code(type(exc).__name__)}",
            f"❌ 错误代码：{html_code(errno)}",
            reason,
        ])

    version = str(row.get("version") or "unknown")
    table_count = int(table_row.get("table_count") or 0)
    return "\n".join([
        "✅ MySQL 连接成功",
        "",
        "✅ MySQL 端口可访问。",
        "✅ MySQL 登录成功。",
        "✅ MySQL 数据库可访问。",
        "✅ MySQL 只读查询测试成功。",
        f"✅ 数据表数量：{table_count}",
        f"✅ MySQL 版本：{version}",
    ])


async def reply_connection_status(update: Update, cfg: AppConfig) -> None:
    if not update.effective_message:
        return

    uid = user_id(update)
    if not is_allowed(update, cfg):
        if is_bot_self_update(update, cfg):
            log.info("忽略 Bot 自身更新：%s", user_display(update))
            return
        log.warning("拒绝未授权 Telegram 用户：%s", user_display(update))
        await update.effective_message.reply_html(
            "❌ 连接失败：你的 Telegram 用户 ID 不在白名单中。\n"
            f"你的 ID：<code>{uid or 'unknown'}</code>\n"
            "请联系管理员授权后再重试。",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    log.info("授权 Telegram 用户连接成功：%s", user_display(update))
    await update.effective_message.reply_text(
        "✅ 连接成功。\n"
        "你的 Telegram 用户 ID 已通过白名单校验，Bot 当前在线。\n\n"
        "可点击左下角菜单使用功能。",
        reply_markup=ReplyKeyboardRemove(),
    )


async def edit_or_replace_status(
    status_message,
    result: str,
    update: Update,
    parse_mode: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Prefer editing the waiting message; fall back to replace if Telegram refuses."""
    try:
        await status_message.edit_text(result, parse_mode=parse_mode, reply_markup=reply_markup)
    except BadRequest as exc:
        log.warning("编辑测试消息失败，改为删除后重新发送：%s", exc)
        try:
            await status_message.delete()
        except BadRequest:
            pass
        if update.effective_message:
            await update.effective_message.reply_text(result, parse_mode=parse_mode, reply_markup=reply_markup)


async def edit_or_replace_status_any(
    status_message,
    result: str,
    update: Update,
    parse_mode: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Edit either a text status message or a photo-caption status card."""
    try:
        if getattr(status_message, "caption", None) is not None:
            await status_message.edit_caption(caption=result, parse_mode=parse_mode, reply_markup=reply_markup)
        else:
            await status_message.edit_text(result, parse_mode=parse_mode, reply_markup=reply_markup)
    except BadRequest as exc:
        log.warning("编辑状态消息失败，改为删除后重新发送：%s", exc)
        try:
            await status_message.delete()
        except BadRequest:
            pass
        if update.effective_message:
            await update.effective_message.reply_text(result, parse_mode=parse_mode, reply_markup=reply_markup)


def build_application(cfg: AppConfig, cache_path: Path) -> Application:
    app = Application.builder().token(cfg.telegram.bot_token).build()

    def back_close_row(back_callback: str = "main_menu", back_text: str = "⬅️ 返回主菜单") -> list[InlineKeyboardButton]:
        return [
            InlineKeyboardButton(back_text, callback_data=back_callback),
            InlineKeyboardButton("❌ 关闭", callback_data="close_message"),
        ]

    def telegram_user_label_sync(uid: int) -> str:
        cached = ui_pref_get_sync(cache_path, uid, "telegram_label")
        return str(cached or f"用户 {uid}")

    def telegram_authorization_list_text_sync() -> str:
        lines = ["🔑 <b>授权管理</b>", "────────────", "当前用户列表："]
        admin_id = cfg.telegram.admin_user_id
        if admin_id is not None:
            lines.append(f"👑 超级管理员：<b>{html.escape(telegram_user_label_sync(admin_id))}</b> (<code>{admin_id}</code>)")
        for uid in sorted(cfg.telegram.manager_user_ids):
            lines.append(f"👑 普通管理员：{html.escape(telegram_user_label_sync(uid))} (<code>{uid}</code>)")
        for uid in sorted(cfg.telegram.authorized_user_ids):
            lines.append(f"🎩 普通用户：{html.escape(telegram_user_label_sync(uid))} (<code>{uid}</code>)")
        if admin_id is None and not cfg.telegram.manager_user_ids and not cfg.telegram.authorized_user_ids:
            lines.append("暂无授权用户。")
        lines.extend(["", "说明：超级管理员只能通过环境变量修改；普通管理员由超级管理员在 Bot 内管理。"])
        return "\n".join(lines)

    async def resolve_telegram_user_label(uid: int) -> str:
        try:
            chat = await app.bot.get_chat(uid)
            name = getattr(chat, "full_name", None) or getattr(chat, "username", None) or str(uid)
            username = getattr(chat, "username", None)
            if username and username not in str(name):
                name = f"{name} (@{username})"
            await asyncio.to_thread(ui_pref_set_sync, cache_path, uid, "telegram_label", str(name))
            return str(name)
        except Exception:
            cached = await asyncio.to_thread(ui_pref_get_sync, cache_path, uid, "telegram_label")
            return str(cached or f"用户 {uid}")

    OPERATION_LOG_CATEGORIES = {
        "traffic_alert": "流量告警规则调整",
        "ip_alert": "IP 监控规则调整",
        "ip_ignore": "IP 忽略调整",
        "reset_cache": "重置缓存",
        "reset_ip": "重置 IP 记录",
        "parameter_config": "参数配置",
        "auth": "授权管理",
    }

    def operation_logs_menu_keyboard(viewer_user_id: int) -> InlineKeyboardMarkup:
        counts = operation_log_counts_sync(cache_path, viewer_user_id, list(OPERATION_LOG_CATEGORIES.keys()))

        def button(text: str, category: str) -> InlineKeyboardButton:
            unread, total = counts.get(category, (0, 0))
            return InlineKeyboardButton(f"{text} ({unread}/{total})", callback_data=f"main_menu:op_logs:{category}")

        rows = [
            [button("🌊 流量告警规则", "traffic_alert")],
            [button("🌐 IP 监控规则", "ip_alert")],
            [button("🚧 IP 忽略调整", "ip_ignore")],
            [button("🧹 重置缓存", "reset_cache")],
            [button("👤 重置 IP 记录", "reset_ip")],
            [button("🎨 参数配置", "parameter_config")],
            [button("🔑 授权管理", "auth")],
            [InlineKeyboardButton("⬅️ 返回主菜单", callback_data="main_menu"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")],
        ]
        return InlineKeyboardMarkup(rows)

    OPERATION_LOG_ACTION_ICONS = {
        "auth": {
            "增加授权": "🔐",
            "删除授权": "🔓",
            "权限变更": "🎭",
        },
        "traffic_alert": {
            "调整默认周期": "💫",
            "调整默认规则": "🎚️",
            "调整独立周期": "💫",
            "调整独立规则": "🌟",
            "切换白名单": "🛡️",
            "恢复默认规则": "♻️",
        },
        "ip_alert": {
            "调整默认周期": "💫",
            "调整默认规则": "🎚️",
            "调整独立周期": "💫",
            "调整独立规则": "🌟",
            "切换白名单": "🛡️",
            "恢复默认规则": "♻️",
        },
        "ip_ignore": {
            "切换忽略": "🚧",
            "解除忽略": "🚧",
        },
        "reset_cache": {
            "调整统计起始点": "⚙️",
            "全部重置缓存": "🗑",
        },
        "reset_ip": {
            "重置特定用户 IP 记录": "👤",
        },
        "parameter_config": {
            "调整缓存保留时间": "🗄",
        },
    }

    def operation_log_action_label(category: str, action: str) -> str:
        icon = OPERATION_LOG_ACTION_ICONS.get(category, {}).get(action)
        return f"{icon} {action}" if icon and not action.startswith(icon) else action

    def auth_user_ids_to_labels(value: str) -> str:
        raw = value.strip()
        if not raw or raw == "空":
            return raw or "空"
        labels: list[str] = []
        for item in raw.split(","):
            item = item.strip()
            if not item:
                continue
            if item.isdigit():
                labels.append(telegram_user_label_sync(int(item)))
            else:
                labels.append(item)
        return ", ".join(labels) or "空"

    def xboard_user_label_sync(uid: int) -> str:
        return render_user_label(uid, cached_user_name_by_id(cache_path, uid))

    def xboard_user_ids_to_labels(value: str) -> str:
        def repl(match: re.Match[str]) -> str:
            return xboard_user_label_sync(int(match.group(1)))

        raw = value.strip()
        if not raw:
            return raw
        return re.sub(r"(?<![\w@])(?:用户|XBoard 用户)\s*(\d+)(?![\w@])", repl, raw)

    def operation_log_detail_display_text(category: str, detail: str) -> str:
        if not detail:
            return detail
        converted: list[str] = []
        auth_user_list_fields = {
            "修改前管理员",
            "修改后管理员",
            "修改前普通用户",
            "修改后普通用户",
            "修改前",
            "修改后",
            "新增",
            "删除",
        }
        xboard_user_fields = {"对象", "XBoard 用户", "用户"}
        for line in detail.splitlines():
            if "：" not in line:
                converted.append(line)
                continue
            key, value = line.split("：", 1)
            if category == "auth" and key in auth_user_list_fields:
                converted.append(f"{key}：{auth_user_ids_to_labels(value)}")
            elif key in xboard_user_fields:
                converted.append(f"{key}：{xboard_user_ids_to_labels(value)}")
            else:
                converted.append(line)
        return "\n".join(converted)

    def operation_logs_summary_keyboard(category: str, viewer_user_id: int) -> InlineKeyboardMarkup:
        rows = []
        logs = operation_logs_list_sync(cache_path, category, 20, viewer_user_id)
        for row in logs:
            log_id = int(row.get("id") or 0)
            mark = "" if int(row.get("is_read") or 0) else "🆕 "
            ts = datetime.fromtimestamp(int(row.get("created_at") or 0), BEIJING_TZ).strftime("%m-%d")
            action = operation_log_action_label(category, str(row.get("action") or ""))
            label = f"{mark}{ts} {action}"
            rows.append([InlineKeyboardButton(label[:64], callback_data=f"main_menu:op_logs:{category}:{log_id}")])
        if not rows:
            rows.append([InlineKeyboardButton("暂无记录", callback_data="main_menu:noop")])
        rows.append([InlineKeyboardButton("⬅️ 返回操作日志", callback_data="main_menu:op_logs"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")])
        return InlineKeyboardMarkup(rows)

    def operation_log_summary_text_sync(category: str, viewer_user_id: int) -> str:
        label = OPERATION_LOG_CATEGORIES.get(category or "", "操作日志")
        unread, total = operation_log_counts_sync(cache_path, viewer_user_id, [category]).get(category, (0, 0))
        return "\n".join([
            "📜 <b>操作日志</b>",
            "────────────",
            f"类型：{html.escape(label)}",
            f"未读/全部：<b>{unread}/{total}</b>",
            "",
            "请选择一条记录查看详情。",
        ])

    def operation_log_detail_text_sync(log_id: int) -> str:
        row = operation_log_get_sync(cache_path, log_id)
        if not row:
            return "📜 <b>操作日志详情</b>\n────────────\n记录不存在或已被删除。"
        category = str(row.get("category") or "")
        label = OPERATION_LOG_CATEGORIES.get(category, category or "操作日志")
        ts = datetime.fromtimestamp(int(row.get("created_at") or 0), BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
        actor_name = html.escape(str(row.get("actor_name") or "未知用户"))
        actor = actor_name
        raw_action = str(row.get("action") or "")
        action = html.escape(operation_log_action_label(category, raw_action))
        detail = html.escape(operation_log_detail_display_text(category, str(row.get("detail") or "")))
        lines = [
            "📜 <b>操作日志详情</b>",
            "────────────",
            f"类型：{html.escape(label)}",
            f"执行时间：<code>{ts}</code>",
            f"Telegram 用户：{actor}",
            f"执行操作：{action}",
        ]
        if detail:
            lines.extend(["", "详情：", detail])
        return "\n".join(lines)

    def operation_log_detail_keyboard(category: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ 返回记录列表", callback_data=f"main_menu:op_logs:{category}")],
            [InlineKeyboardButton("⬅️ 返回操作日志", callback_data="main_menu:op_logs"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")],
        ])

    def log_operation_from_query(query: Any, category: str, action: str, detail: str = "") -> None:
        user = getattr(query, "from_user", None)
        operation_log_add_sync(cache_path, getattr(user, "id", None), actor_name_from_user(user), category, action, detail)

    def log_operation_from_update(update: Update, category: str, action: str, detail: str = "") -> None:
        user = update.effective_user
        operation_log_add_sync(cache_path, getattr(user, "id", None), actor_name_from_user(user), category, action, detail)

    def alert_category(alert_type: str) -> str:
        return "traffic_alert" if alert_type == "traffic" else "ip_alert"

    def alert_type_label(alert_type: str) -> str:
        return "流量告警" if alert_type == "traffic" else "IP 监控"

    def alert_setting_before_after_detail(alert_type: str, scope: str, before: str, after: str, xboard_user_id: int | None = None) -> str:
        target = f"XBoard 用户 {xboard_user_id}" if xboard_user_id is not None else "默认规则"
        return f"对象：{target}\n类型：{alert_type_label(alert_type)}\n修改前：{before}\n修改后：{after}"

    def authorization_manage_keyboard(super_admin: bool = False) -> InlineKeyboardMarkup:
        rows = [[
            InlineKeyboardButton("🔐 增加授权", callback_data="main_menu:auth:add"),
            InlineKeyboardButton("🔓 删除授权", callback_data="main_menu:auth:delete"),
        ]]
        if super_admin:
            rows.append([InlineKeyboardButton("🎭 权限变更", callback_data="main_menu:auth:roles")])
        rows.append([InlineKeyboardButton("⬅️ 返回主菜单", callback_data="main_menu"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")])
        return InlineKeyboardMarkup(rows)

    def authorization_delete_keyboard(context: ContextTypes.DEFAULT_TYPE, super_admin: bool = False) -> InlineKeyboardMarkup:
        selected = context.user_data.get("auth_delete_selected") or set()
        if not isinstance(selected, set):
            selected = set(selected or [])
        rows = []
        deletable: list[tuple[int, str]] = [(uid, "🎩") for uid in sorted(cfg.telegram.authorized_user_ids)]
        if super_admin:
            deletable = [(uid, "👑") for uid in sorted(cfg.telegram.manager_user_ids)] + deletable
        for uid, emoji in deletable:
            mark = "✅ " if uid in selected else ""
            label = f"{mark}{emoji} {telegram_user_label_sync(uid)} ({uid})"
            rows.append([InlineKeyboardButton(label[:64], callback_data=f"main_menu:auth:del_toggle:{uid}")])
        if rows:
            rows.append([InlineKeyboardButton("✅ 完成选择", callback_data="main_menu:auth:del_done")])
        else:
            rows.append([InlineKeyboardButton("暂无可删除授权用户", callback_data="main_menu:noop")])
        rows.append([InlineKeyboardButton("⬅️ 返回授权管理", callback_data="main_menu:auth"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")])
        return InlineKeyboardMarkup(rows)

    def authorization_delete_confirm_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ 确认删除", callback_data="main_menu:auth:del_confirm")],
            [InlineKeyboardButton("⬅️ 返回选择", callback_data="main_menu:auth:delete"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")],
        ])

    def authorization_role_change_text(context: ContextTypes.DEFAULT_TYPE) -> str:
        role_changes = context.user_data.get("auth_role_changes") or {}
        if not isinstance(role_changes, dict):
            role_changes = {}
        lines = [
            "🎭 <b>权限变更</b>",
            "────────────",
            "点击用户即可在 🎩 普通用户 / 👑 普通管理员之间切换。",
            "未确认的变更会在下方显示；点击保存后才生效。"
        ]
        pending_lines = []
        for uid, role in sorted(role_changes.items(), key=lambda item: int(item[0])):
            target_role = str(role)
            current_role = "manager" if int(uid) in cfg.telegram.manager_user_ids else "user"
            if target_role == current_role:
                continue
            emoji = "👑" if target_role == "manager" else "🎩"
            role_label = "普通管理员" if target_role == "manager" else "普通用户"
            pending_lines.append(f"{emoji} {html.escape(telegram_user_label_sync(int(uid)))} (<code>{int(uid)}</code>) → {role_label}")
        if pending_lines:
            lines.extend(["", "待保存变更：", *pending_lines])
        return "\n".join(lines)

    def authorization_role_change_keyboard(context: ContextTypes.DEFAULT_TYPE) -> InlineKeyboardMarkup:
        role_changes = context.user_data.get("auth_role_changes") or {}
        if not isinstance(role_changes, dict):
            role_changes = {}
        rows: list[list[InlineKeyboardButton]] = []
        candidates = [(uid, "manager") for uid in sorted(cfg.telegram.manager_user_ids)] + [(uid, "user") for uid in sorted(cfg.telegram.authorized_user_ids)]
        for uid, current_role in candidates:
            target_role = str(role_changes.get(uid, current_role))
            emoji = "👑" if target_role == "manager" else "🎩"
            rows.append([InlineKeyboardButton(f"{emoji} {telegram_user_label_sync(uid)} ({uid})"[:64], callback_data=f"main_menu:auth:role_toggle:{uid}")])
        if candidates:
            rows.append([InlineKeyboardButton("💾 保存变更", callback_data="main_menu:auth:role_save")])
        else:
            rows.append([InlineKeyboardButton("暂无可变更权限的用户", callback_data="main_menu:noop")])
        rows.append([InlineKeyboardButton("⬅️ 返回授权管理", callback_data="main_menu:auth"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")])
        return InlineKeyboardMarkup(rows)

    def main_menu_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
        rows = [
            [
                InlineKeyboardButton("🩺 健康检查", callback_data="main_menu:system_check"),
                InlineKeyboardButton("💬 通知推送", callback_data="main_menu:notifications"),
            ],
            [
                InlineKeyboardButton("🌊 流量统计", callback_data="main_menu:traffic_management"),
                InlineKeyboardButton("🌐 IP 监控", callback_data="main_menu:ip_monitor"),
            ],
            [
                InlineKeyboardButton("🎨 参数配置", callback_data="main_menu:parameter_config"),
                InlineKeyboardButton("🧪 调试功能", callback_data="main_menu:debug_tools"),
            ],
        ]
        if is_admin:
            rows.append([InlineKeyboardButton("📜 操作日志", callback_data="main_menu:op_logs")])
            rows.append([InlineKeyboardButton("🔑 授权管理", callback_data="main_menu:auth")])
        rows.append([InlineKeyboardButton("❌ 关闭", callback_data="close_message")])
        return InlineKeyboardMarkup(rows)

    def clear_history_confirm_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("✅确认", callback_data="main_menu:clear_history_confirm")],
            [InlineKeyboardButton("❎ 取消", callback_data="main_menu")],
        ])

    def empty_section_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([back_close_row()])

    def health_check_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 刷新", callback_data="main_menu:system_check_refresh")],
            back_close_row(),
        ])

    def traffic_management_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 用户用量", callback_data="main_menu:traffic_users")],
            [InlineKeyboardButton("🖥 节点用量", callback_data="main_menu:traffic_nodes")],
            [InlineKeyboardButton("🚨 异常告警", callback_data="alert_menu:traffic")],
            back_close_row(),
        ])

    def ip_monitor_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 周期统计", callback_data="main_menu:ip_monitor:period")],
            [InlineKeyboardButton("🚨 异地登录", callback_data="alert_menu:ip")],
            [InlineKeyboardButton(f"🚧 忽略列表", callback_data="main_menu:ip_monitor:ignore")],
            back_close_row(),
        ])

    def ip_ignore_menu_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📍 地区", callback_data="main_menu:ip_monitor:ignore:area:0")],
            [InlineKeyboardButton("🏷 ASN", callback_data="main_menu:ip_monitor:ignore:asn:0")],
            [InlineKeyboardButton("🌐 IP", callback_data="main_menu:ip_monitor:ignore:cidr:0")],
            [InlineKeyboardButton("📎 当前忽略", callback_data="main_menu:ip_monitor:ignored_rules:0")],
            back_close_row("main_menu:ip_monitor", "⬅️ 返回 IP 监控"),
        ])

    def ignored_rules_keyboard(context: ContextTypes.DEFAULT_TYPE, page: int = 0) -> InlineKeyboardMarkup:
        items = ignored_rule_items_sync(cache_path)
        token_map = context.user_data.setdefault("ip_ignore_tokens", {})
        if not isinstance(token_map, dict):
            token_map = {}
            context.user_data["ip_ignore_tokens"] = token_map
        page_size = 10
        total_pages = max(1, (len(items) + page_size - 1) // page_size)
        page = min(max(page, 0), total_pages - 1)
        rows: list[list[InlineKeyboardButton]] = []
        for item in items[page * page_size:(page + 1) * page_size]:
            token = hashlib.sha1(f"rule:{item['dimension']}:{item['value']}".encode("utf-8")).hexdigest()[:12]
            token_map[token] = {"dimension": str(item["dimension"]), "value": str(item["value"])}
            label = f"✅ {item['sub']} {item['label']}"
            rows.append([InlineKeyboardButton(label[:64], callback_data=f"main_menu:ip_monitor:ignored_rule_toggle:{page}:{token}")])
        if len(items) > page_size:
            nav_row: list[InlineKeyboardButton] = []
            if page > 0:
                nav_row.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"main_menu:ip_monitor:ignored_rules:{page - 1}"))
            nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="main_menu:noop"))
            if page < total_pages - 1:
                nav_row.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"main_menu:ip_monitor:ignored_rules:{page + 1}"))
            rows.append(nav_row)
        if not rows:
            rows.append([InlineKeyboardButton("当前暂无忽略内容", callback_data="main_menu:noop")])
        rows.append(back_close_row("main_menu:ip_monitor:ignore", "⬅️ 返回忽略列表"))
        return InlineKeyboardMarkup(rows)

    def ip_ignore_list_keyboard(context: ContextTypes.DEFAULT_TYPE, dimension: str, page: int = 0) -> InlineKeyboardMarkup:
        items = ignored_list_items_sync(cache_path, dimension)
        selected = ignored_rule_values_sync(cache_path, dimension)
        token_map = context.user_data.setdefault("ip_ignore_tokens", {})
        if not isinstance(token_map, dict):
            token_map = {}
            context.user_data["ip_ignore_tokens"] = token_map
        page_size = 10
        total_pages = max(1, (len(items) + page_size - 1) // page_size)
        page = min(max(page, 0), total_pages - 1)
        rows: list[list[InlineKeyboardButton]] = []
        for item in items[page * page_size:(page + 1) * page_size]:
            token = hashlib.sha1(f"{dimension}:{item['value']}".encode("utf-8")).hexdigest()[:12]
            token_map[token] = {"dimension": dimension, "value": str(item["value"])}
            prefix = "✅ " if str(item["value"]) in selected else ""
            label = f"{prefix}{item['label']}"
            sub = str(item.get("sub") or "")
            if sub:
                label = f"{label} · {sub}"
            rows.append([InlineKeyboardButton(label[:64], callback_data=f"main_menu:ip_monitor:ignore_toggle:{dimension}:{page}:{token}")])
        if len(items) > page_size:
            nav_row: list[InlineKeyboardButton] = []
            if page > 0:
                nav_row.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"main_menu:ip_monitor:ignore:{dimension}:{page - 1}"))
            nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
            if page < total_pages - 1:
                nav_row.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"main_menu:ip_monitor:ignore:{dimension}:{page + 1}"))
            rows.append(nav_row)
        if not rows:
            rows.append([InlineKeyboardButton("暂无已采集信息", callback_data="main_menu:noop")])
        rows.append(back_close_row("main_menu:ip_monitor:ignore", "⬅️ 返回忽略列表"))
        return InlineKeyboardMarkup(rows)

    def user_ip_ignore_dimension_keyboard(kind: str, xboard_user_id: int, page: int = 0, source: str | None = None) -> InlineKeyboardMarkup:
        suffix = f":{source}" if source else ""
        back_button = InlineKeyboardButton("⬅️ 返回通知", callback_data=f"ip_alert_notice:{xboard_user_id}") if source == "alert" else InlineKeyboardButton("⬅️ 返回详情", callback_data=f"ip_active_user_detail:{kind}:{xboard_user_id}:{page}{suffix}")
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("忽略地区", callback_data=f"ip_ignore_page:area:{kind}:{xboard_user_id}:{page}:0{suffix}"),
                InlineKeyboardButton("忽略 ASN", callback_data=f"ip_ignore_page:asn:{kind}:{xboard_user_id}:{page}:0{suffix}"),
                InlineKeyboardButton("忽略 IP", callback_data=f"ip_ignore_page:cidr:{kind}:{xboard_user_id}:{page}:0{suffix}"),
            ],
            [back_button, InlineKeyboardButton("❌ 关闭", callback_data="close_message")],
        ])

    def user_ip_ignore_list_keyboard(context: ContextTypes.DEFAULT_TYPE, dimension: str, kind: str, xboard_user_id: int, detail_page: int, list_page: int = 0, source: str | None = None) -> InlineKeyboardMarkup:
        items = user_ip_ignore_items_sync(cache_path, xboard_user_id, kind, detail_page, dimension)
        selected = ignored_rule_values_sync(cache_path, dimension)
        token_map = context.user_data.setdefault("ip_ignore_tokens", {})
        if not isinstance(token_map, dict):
            token_map = {}
            context.user_data["ip_ignore_tokens"] = token_map
        page_size = 10
        total_pages = max(1, (len(items) + page_size - 1) // page_size)
        list_page = min(max(list_page, 0), total_pages - 1)
        suffix = f":{source}" if source else ""
        rows: list[list[InlineKeyboardButton]] = []
        for item in items[list_page * page_size:(list_page + 1) * page_size]:
            token = hashlib.sha1(f"{dimension}:{item['value']}".encode("utf-8")).hexdigest()[:12]
            token_map[token] = {"dimension": dimension, "value": str(item["value"])}
            prefix = "✅ " if str(item["value"]) in selected else ""
            label = f"{prefix}{item['label']}"
            if item.get("sub"):
                label = f"{label} · {item['sub']}"
            route_token = hashlib.sha1(f"route:{dimension}:{kind}:{xboard_user_id}:{detail_page}:{list_page}:{token}:{source or ''}".encode("utf-8")).hexdigest()[:12]
            token_map[route_token] = {
                "dimension": dimension,
                "value": str(item["value"]),
                "kind": kind,
                "user_id": int(xboard_user_id),
                "detail_page": int(detail_page),
                "list_page": int(list_page),
                "source": source or "",
            }
            rows.append([InlineKeyboardButton(label[:64], callback_data=f"ip_ig_t:{route_token}")])
        if len(items) > page_size:
            nav_row: list[InlineKeyboardButton] = []
            if list_page > 0:
                nav_row.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"ip_ignore_page:{dimension}:{kind}:{xboard_user_id}:{detail_page}:{list_page - 1}{suffix}"))
            nav_row.append(InlineKeyboardButton(f"{list_page + 1}/{total_pages}", callback_data="noop"))
            if list_page < total_pages - 1:
                nav_row.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"ip_ignore_page:{dimension}:{kind}:{xboard_user_id}:{detail_page}:{list_page + 1}{suffix}"))
            rows.append(nav_row)
        if not rows:
            rows.append([InlineKeyboardButton("当前列表暂无可忽略项", callback_data="noop")])
        back_button = InlineKeyboardButton("⬅️ 返回通知", callback_data=f"ip_alert_notice:{xboard_user_id}") if source == "alert" else InlineKeyboardButton("⬅️ 返回忽略类型", callback_data=f"ip_ignore_menu:{kind}:{xboard_user_id}:{detail_page}{suffix}")
        rows.append([back_button, InlineKeyboardButton("❌ 关闭", callback_data="close_message")])
        return InlineKeyboardMarkup(rows)

    def ip_monitor_period_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("近 1 小时", callback_data="active_users:1h"), InlineKeyboardButton("近 24 小时", callback_data="active_users:24h")],
            [InlineKeyboardButton("近 7 天", callback_data="active_users:7d"), InlineKeyboardButton("近 30 天", callback_data="active_users:30d")],
            [InlineKeyboardButton("自选区间", callback_data="ip_custom:start")],
            back_close_row("main_menu:ip_monitor", "⬅️ 返回 IP 监控"),
        ])

    def ip_monitor_period_result_keyboard(selected_period: str = "1h") -> InlineKeyboardMarkup:
        period_labels = {
            "1h": "近 1 小时",
            "24h": "近 24 小时",
            "7d": "近 7 天",
            "30d": "近 30 天",
        }
        switch_row = [
            InlineKeyboardButton(label, callback_data=f"active_users:{key}")
            for key, label in period_labels.items()
            if key != selected_period
        ]
        return InlineKeyboardMarkup([
            switch_row,
            [InlineKeyboardButton("自选区间", callback_data="ip_custom:start")],
            back_close_row("main_menu:ip_monitor", "⬅️ 返回 IP 监控"),
        ])

    def parameter_config_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🖼 自定题图", callback_data="main_menu:parameter_config:cover")],
            [InlineKeyboardButton("🏷 自定昵称", callback_data="main_menu:parameter_config:nickname")],
            [InlineKeyboardButton("🗄 缓存保留时间", callback_data="main_menu:parameter_config:cache_retention")],
            back_close_row(),
        ])

    def cache_retention_keyboard(selected_days: int | None = None) -> InlineKeyboardMarkup:
        selected_days = cache_retention_days_sync(cache_path) if selected_days is None else selected_days
        rows = []
        for option_key, (days, label) in CACHE_RETENTION_OPTIONS.items():
            mark = "✅ " if int(days) == int(selected_days) else ""
            rows.append([InlineKeyboardButton(f"{mark}{label}", callback_data=f"main_menu:parameter_config:cache_retention_select:{option_key}")])
        rows.append(back_close_row("main_menu:parameter_config", "⬅️ 返回参数配置"))
        return InlineKeyboardMarkup(rows)

    def cache_retention_confirm_keyboard(option_key: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ 确认并清理", callback_data=f"main_menu:parameter_config:cache_retention_confirm:{option_key}")],
            [InlineKeyboardButton("⬅️ 返回选择", callback_data="main_menu:parameter_config:cache_retention"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")],
        ])

    def cache_retention_text_sync() -> str:
        days = cache_retention_days_sync(cache_path)
        return "\n".join([
            "🗄 <b>缓存保留时间</b>",
            "────────────",
            f"当前设置：<b>{html.escape(cache_retention_label(days))}</b>",
            "",
            "说明：超过保留时间的 Bot 本地缓存会自动清理；选择新周期并确认后，会立即删除超出期限的老缓存记录。",
            "不会修改 XBoard / MySQL / Redis。",
        ])

    def cache_retention_preview_text(option_key: str, preview: dict[str, int]) -> str:
        days, label = CACHE_RETENTION_OPTIONS[option_key]
        cutoff = int(preview.get("cutoff_ts") or 0)
        cutoff_text = "不限制，保留全部历史" if cutoff <= 0 else format_timestamp(cutoff)
        return "\n".join([
            "⚠️ <b>确认缓存保留时间</b>",
            "────────────",
            f"新设置：<b>{html.escape(label)}</b>",
            f"清理边界：<code>{html.escape(cutoff_text)}</code>",
            "",
            "将删除以下超期本地缓存：",
            f"• 活跃 IP 记录：<b>{int(preview.get('active_ip_records') or 0)}</b> 条",
            f"• IP 归属地缓存：<b>{int(preview.get('ip_geo_cache') or 0)}</b> 条",
            f"• 流量分钟样本：<b>{int(preview.get('traffic_delta_samples') or 0)}</b> 条",
            f"• 采样中断记录：<b>{int(preview.get('traffic_sample_gaps') or 0)}</b> 条",
            f"• 自定义范围：<b>{int(preview.get('traffic_ranges') or 0)}</b> 条",
            "",
            "确认后立即生效。",
        ])

    def notification_push_keyboard(chat_id: str, is_admin: bool = False) -> InlineKeyboardMarkup:
        status = notification_status_sync(cache_path, chat_id, DEFAULT_ALLOWLIST_NOTIFICATION_KINDS)
        def label(kind: str) -> str:
            if kind == "ip_alert":
                mode = notification_ip_alert_mode_sync(cache_path, chat_id)
                if mode == "advanced":
                    return "🔔 异地登录+"
                if mode == "basic":
                    return "🔔 异地登录"
                return "🔕 异地登录"
            return f"{'🔔' if status.get(kind) else '🔕'} {NOTIFICATION_KINDS[kind]}"
        rows = [
            [InlineKeyboardButton(label("collector"), callback_data="main_menu:notifications:collector")],
            [InlineKeyboardButton(label("daily"), callback_data="main_menu:notifications:daily")],
            [InlineKeyboardButton(label("weekly"), callback_data="main_menu:notifications:weekly")],
            [InlineKeyboardButton(label("monthly"), callback_data="main_menu:notifications:monthly")],
            [InlineKeyboardButton(label("traffic_alert"), callback_data="main_menu:notifications:traffic_alert")],
            [InlineKeyboardButton(label("ip_alert"), callback_data="main_menu:notifications:ip_alert")],
        ]
        if is_admin:
            rows.append([InlineKeyboardButton(label("version_update"), callback_data="main_menu:notifications:version_update")])
        rows.append(back_close_row())
        return InlineKeyboardMarkup(rows)


    def alert_menu_keyboard(alert_type: str) -> InlineKeyboardMarkup:
        back_target = "main_menu:traffic_management" if alert_type == "traffic" else "main_menu:ip_monitor"
        back_text = "⬅️ 返回流量统计" if alert_type == "traffic" else "⬅️ 返回 IP 监控"
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🎚️ 默认规则", callback_data=f"alert_global:{alert_type}")],
            [InlineKeyboardButton("🌟 独立规则", callback_data=f"alert_users:{alert_type}:0")],
            back_close_row(back_target, back_text),
        ])

    def alert_user_list_keyboard(alert_type: str, users: list[dict[str, Any]], page: int = 0) -> InlineKeyboardMarkup:
        per_page = 10
        page = max(0, page)
        start = page * per_page
        page_users = users[start:start + per_page]
        rows = []
        for user in page_users:
            xboard_user_id = int(user["user_id"])
            name = str(user.get("name") or f"用户{xboard_user_id}")
            setting_label = str(user.get("setting_label") or "默认")
            label_text = f"{name} (user_id: {xboard_user_id}) ({setting_label})"
            rows.append([InlineKeyboardButton(label_text, callback_data=f"alert_user:{alert_type}:{xboard_user_id}")])
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"alert_users:{alert_type}:{page - 1}"))
        if start + per_page < len(users):
            nav.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"alert_users:{alert_type}:{page + 1}"))
        if nav:
            rows.append(nav)
        rows.append([InlineKeyboardButton("⬅️ 返回", callback_data=f"alert_menu:{alert_type}"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")])
        return InlineKeyboardMarkup(rows)

    def alert_period_keyboard(prefix: str, alert_type: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("近 1 小时", callback_data=f"{prefix}:{alert_type}:period:1h"), InlineKeyboardButton("近 24 小时", callback_data=f"{prefix}:{alert_type}:period:24h"), InlineKeyboardButton("近 7 天", callback_data=f"{prefix}:{alert_type}:period:7d")],
            [InlineKeyboardButton("今天", callback_data=f"{prefix}:{alert_type}:period:today"), InlineKeyboardButton("本周", callback_data=f"{prefix}:{alert_type}:period:week")],
        ])

    def alert_user_current_period_and_threshold(alert_type: str, xboard_user_id: int) -> tuple[str, str]:
        setting = alert_user_setting_sync(cache_path, xboard_user_id)
        if alert_type == "traffic":
            period = setting.get("traffic_period") or alert_global_period_sync(cache_path, "traffic")
            threshold = int(setting.get("traffic_threshold_bytes") or alert_global_threshold_sync(cache_path, "traffic"))
            return alert_period_label(period), format_bytes(threshold)
        period = setting.get("ip_period") or alert_global_period_sync(cache_path, "ip")
        threshold = int(setting.get("ip_city_threshold") or alert_global_threshold_sync(cache_path, "ip"))
        return alert_period_label(period), f"{threshold} 个城市"

    def alert_user_setting_keyboard(alert_type: str, xboard_user_id: int) -> InlineKeyboardMarkup:
        setting = alert_user_setting_sync(cache_path, xboard_user_id)
        whitelist_key = "traffic_whitelist" if alert_type == "traffic" else "ip_whitelist"
        is_whitelisted = bool(int(setting.get(whitelist_key) or 0))
        whitelist_text = "🌑 取消白名单" if is_whitelisted else "🌕 设为白名单"
        period_text, threshold_text = alert_user_current_period_and_threshold(alert_type, xboard_user_id)
        if is_whitelisted:
            period_text = "♾️"
            threshold_text = "♾️"
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(whitelist_text, callback_data=f"alert_set:{alert_type}:whitelist:{xboard_user_id}")],
            [InlineKeyboardButton(period_text, callback_data=f"alert_period_page:{alert_type}:{xboard_user_id}"), InlineKeyboardButton(threshold_text, callback_data=f"alert_set:{alert_type}:custom:{xboard_user_id}")],
            [InlineKeyboardButton("♻️ 恢复默认", callback_data=f"alert_set:{alert_type}:reset:{xboard_user_id}")],
            [InlineKeyboardButton("⬅️ 用户列表", callback_data=f"alert_users:{alert_type}:0"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")],
        ])

    def alert_user_period_select_keyboard(alert_type: str, xboard_user_id: int) -> InlineKeyboardMarkup:
        rows = alert_period_keyboard("alert_set", alert_type).inline_keyboard
        return InlineKeyboardMarkup([
            *[[InlineKeyboardButton(button.text, callback_data=f"{button.callback_data}:{xboard_user_id}") for button in row] for row in rows],
            [InlineKeyboardButton("⬅️ 返回", callback_data=f"alert_user:{alert_type}:{xboard_user_id}"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")],
        ])

    def alert_global_current_period_and_threshold(alert_type: str) -> tuple[str, str]:
        period = alert_global_period_sync(cache_path, alert_type)
        threshold = alert_global_threshold_sync(cache_path, alert_type)
        if alert_type == "traffic":
            return alert_period_label(period), format_bytes(threshold)
        return alert_period_label(period), f"{threshold} 个城市"

    def alert_global_keyboard(alert_type: str) -> InlineKeyboardMarkup:
        period_text, threshold_text = alert_global_current_period_and_threshold(alert_type)
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(period_text, callback_data=f"alert_global_period_page:{alert_type}"), InlineKeyboardButton(threshold_text, callback_data=f"alert_global:{alert_type}:custom")],
            [InlineKeyboardButton("⬅️ 返回", callback_data=f"alert_menu:{alert_type}"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")],
        ])

    def alert_global_period_select_keyboard(alert_type: str) -> InlineKeyboardMarkup:
        rows = alert_period_keyboard("alert_global", alert_type).inline_keyboard
        return InlineKeyboardMarkup([
            *rows,
            [InlineKeyboardButton("⬅️ 返回", callback_data=f"alert_global:{alert_type}"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")],
        ])
    def debug_tools_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
        rows = []
        if is_admin:
            rows.append([InlineKeyboardButton("🧹 重置缓存", callback_data="main_menu:debug:reset_cache")])
        rows.append([InlineKeyboardButton("👤 重置特定用户 IP 记录", callback_data="main_menu:debug:reset_user_ip")])
        rows.append(back_close_row())
        return InlineKeyboardMarkup(rows)

    def reset_cache_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑 全部重置", callback_data="main_menu:debug:reset_cache_now")],
            [InlineKeyboardButton("⚙️ 调整起始点", callback_data="main_menu:debug:reset_cache_floor")],
            back_close_row("main_menu:debug_tools", "⬅️ 返回调试功能"),
        ])

    def reset_cache_confirm_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ 确认全部重置", callback_data="main_menu:debug:reset_cache_now_confirm")],
            back_close_row("main_menu:debug:reset_cache", "❎ 取消"),
        ])

    def reset_user_ip_select_keyboard(users: list[tuple[int, str]], selected: set[int], page: int = 0) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        page_size = 10
        total_pages = max(1, (len(users) + page_size - 1) // page_size)
        page = min(max(page, 0), total_pages - 1)
        start = page * page_size
        for user_id_value, label in users[start:start + page_size]:
            prefix = "✅ " if user_id_value in selected else ""
            rows.append([InlineKeyboardButton(f"{prefix}{label}", callback_data=f"main_menu:debug:reset_user_ip_toggle:{page}:{user_id_value}")])
        if len(users) > page_size:
            nav_row: list[InlineKeyboardButton] = []
            if page > 0:
                nav_row.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"main_menu:debug:reset_user_ip_page:{page - 1}"))
            nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
            if page < total_pages - 1:
                nav_row.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"main_menu:debug:reset_user_ip_page:{page + 1}"))
            rows.append(nav_row)
        rows.append([InlineKeyboardButton(f"✅ 完成选择 ({len(selected)})", callback_data="main_menu:debug:reset_user_ip_done")])
        rows.append(back_close_row("main_menu:debug_tools", "⬅️ 返回调试功能"))
        return InlineKeyboardMarkup(rows)

    def reset_user_ip_multi_confirm_keyboard(user_ids: list[int]) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ 确认清理所选用户", callback_data="main_menu:debug:reset_user_ip_multi_confirm")],
            [InlineKeyboardButton("⬅️ 返回选择", callback_data="main_menu:debug:reset_user_ip_page:0"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")],
        ])

    def cover_config_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("♻️ 重置为 Bot 头像", callback_data="main_menu:parameter_config:cover_reset")],
            back_close_row("main_menu:parameter_config", "⬅️ 返回参数配置"),
        ])

    def nickname_config_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("♻️ 重置为 Telegram 名称", callback_data="main_menu:parameter_config:nickname_reset")],
            back_close_row("main_menu:parameter_config", "⬅️ 返回参数配置"),
        ])

    def start_menu_text(update: Update, custom_name: str | None = None) -> str:
        user = update.effective_user
        tg_name = (user.full_name or user.username or str(user.id)) if user else "用户"
        display_name = html.escape(str(custom_name or tg_name))
        uid = user.id if user else None
        role_emoji = "👑" if is_admin_user_id(uid, cfg) else "🎩"
        return f"{role_emoji} {display_name}，<b>请选择功能</b>"

    no_auto_delete_message_keys: set[tuple[str, int]] = set()

    def mark_no_auto_delete_message(message: Any | None) -> None:
        if not message:
            return
        try:
            no_auto_delete_message_keys.add((str(message.chat_id), int(message.message_id)))
        except Exception:
            pass

    async def track_auto_delete_message(message: Any | None, is_pinned: bool = False) -> None:
        if not message:
            return
        try:
            chat_id = str(message.chat_id)
            await asyncio.to_thread(auto_delete_message_set_sync, cache_path, chat_id, message.message_id, is_pinned)
        except Exception as exc:
            log.debug("登记自动删除消息失败：%s", exc)

    def split_telegram_text(text: str, limit: int = 3900) -> list[str]:
        if len(text) <= limit:
            return [text]
        chunks: list[str] = []
        current = ""
        for line in text.splitlines(keepends=True):
            if len(line) > limit:
                if current:
                    chunks.append(current.rstrip("\n"))
                    current = ""
                for start in range(0, len(line), limit):
                    chunks.append(line[start:start + limit].rstrip("\n"))
                continue
            if len(current) + len(line) > limit:
                chunks.append(current.rstrip("\n"))
                current = line
            else:
                current += line
        if current:
            chunks.append(current.rstrip("\n"))
        return chunks

    async def reply_long_text(message: Any, text: str, parse_mode: str | None = None, reply_markup: InlineKeyboardMarkup | None = None) -> None:
        chunks = split_telegram_text(text)
        for index, chunk in enumerate(chunks):
            markup = reply_markup if index == len(chunks) - 1 else None
            sent = await message.reply_text(chunk, parse_mode=parse_mode, reply_markup=markup)
            await track_auto_delete_message(sent)

    async def show_callback_page(
        query,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
        parse_mode: str | None = None,
        auto_delete: bool = True,
    ) -> None:
        if not query.message:
            return
        try:
            if (str(query.message.chat_id), int(query.message.message_id)) in no_auto_delete_message_keys:
                auto_delete = False
        except Exception:
            pass
        try:
            if query.message.text:
                await query.message.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
            elif query.message.caption:
                await query.message.edit_caption(caption=text, parse_mode=parse_mode, reply_markup=reply_markup)
            else:
                sent = await query.message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
                if auto_delete:
                    await track_auto_delete_message(sent)
                return
            if auto_delete:
                is_pinned = await asyncio.to_thread(auto_delete_message_is_pinned_sync, cache_path, str(query.message.chat_id), query.message.message_id)
                await track_auto_delete_message(query.message, is_pinned=is_pinned)
        except Exception as exc:
            log.warning("编辑菜单消息失败，改为发送新消息：%s", exc)
            sent = await query.message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
            if auto_delete:
                await track_auto_delete_message(sent)

    async def purge_chat_history(chat_id: int | str, from_message_id: int) -> tuple[int, int]:
        deleted = 0
        failed = 0
        start_id = max(1, int(from_message_id))
        batch_size = 25
        for batch_start in range(start_id, 0, -batch_size):
            tasks = []
            for message_id in range(batch_start, max(0, batch_start - batch_size), -1):
                tasks.append(context_bot_delete_message(chat_id, message_id))
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for ok in results:
                if ok is True:
                    deleted += 1
                else:
                    failed += 1
            await asyncio.sleep(0.08)
        await asyncio.to_thread(clear_message_tracking_for_chat_sync, cache_path, str(chat_id))
        return deleted, failed

    async def context_bot_delete_message(chat_id: int | str, message_id: int) -> bool:
        try:
            await app.bot.delete_message(chat_id=chat_id, message_id=message_id)
            return True
        except Exception:
            return False

    async def send_start_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_message:
            return
        uid = user_id(update)
        custom_name = await asyncio.to_thread(ui_pref_get_sync, cache_path, uid, "nickname") if uid is not None else None
        custom_cover = await asyncio.to_thread(ui_pref_get_sync, cache_path, uid, "cover_file_id") if uid is not None else None
        text = start_menu_text(update, custom_name)
        is_admin = is_admin_user_id(uid, cfg)
        if custom_cover:
            try:
                sent = await update.effective_message.reply_photo(
                    photo=custom_cover,
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=main_menu_keyboard(is_admin),
                )
                await track_auto_delete_message(sent)
                return
            except Exception as exc:
                log.warning("发送自定义题图失败，改为使用 Bot 头像：%s", exc)
        try:
            me = await context.bot.get_me()
            photos = await context.bot.get_user_profile_photos(me.id, limit=1)
            if photos.total_count > 0 and photos.photos:
                sent = await update.effective_message.reply_photo(
                    photo=photos.photos[0][-1].file_id,
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=main_menu_keyboard(is_admin),
                )
                await track_auto_delete_message(sent)
                return
        except Exception as exc:
            log.warning("读取 Bot 头像失败，改为发送文本菜单：%s", exc)
        sent = await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=main_menu_keyboard(is_admin))
        await track_auto_delete_message(sent)

    async def reply_cover_card(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup: InlineKeyboardMarkup | None = None):
        """Send a card with the same cover image policy as /start; fallback to text."""
        if not update.effective_message:
            return None
        uid = user_id(update)
        custom_cover = await asyncio.to_thread(ui_pref_get_sync, cache_path, uid, "cover_file_id") if uid is not None else None
        if custom_cover:
            try:
                sent = await update.effective_message.reply_photo(
                    photo=custom_cover,
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                )
                await track_auto_delete_message(sent)
                return sent
            except Exception as exc:
                log.warning("发送自定义题图状态卡片失败，改为使用 Bot 头像：%s", exc)
        try:
            me = await context.bot.get_me()
            photos = await context.bot.get_user_profile_photos(me.id, limit=1)
            if photos.total_count > 0 and photos.photos:
                sent = await update.effective_message.reply_photo(
                    photo=photos.photos[0][-1].file_id,
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                )
                await track_auto_delete_message(sent)
                return sent
        except Exception as exc:
            log.warning("读取 Bot 头像失败，改为发送文本状态卡片：%s", exc)
        sent = await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
        await track_auto_delete_message(sent)
        return sent

    async def reply_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, cfg: AppConfig) -> None:
        if not update.effective_message:
            return
        uid = user_id(update)
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                log.info("忽略 Bot 自身更新：%s", user_display(update))
                return
            log.warning("拒绝未授权 Telegram 用户：%s", user_display(update))
            await update.effective_message.reply_html(
                f"Telegram 用户 <code>{uid or 'unknown'}</code> 不在授权名单中。\n"
                "请联系管理员授权后再使用。",
                reply_markup=ReplyKeyboardRemove(),
            )
            return
        if uid is not None and update.effective_user:
            label_parts = [update.effective_user.full_name or update.effective_user.username or str(uid)]
            if update.effective_user.username:
                label_parts.append(f"@{update.effective_user.username}")
            await asyncio.to_thread(ui_pref_set_sync, cache_path, uid, "telegram_label", " ".join(dict.fromkeys(label_parts)))
        await send_start_menu(update, context)

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await reply_main_menu(update, context, cfg)

    async def clear_history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_message:
            return
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                return
            await reply_connection_status(update, cfg)
            return
        sent = await update.effective_message.reply_text(
            "👋🏻 <b>清除对话记录</b>\n────────────\n将尝试清空当前对话记录。\n此操作不可恢复。\n\n⚠️ 确认要继续吗？",
            parse_mode="HTML",
            reply_markup=clear_history_confirm_keyboard(),
        )
        await track_auto_delete_message(sent)

    async def version_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_message:
            return
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                return
            await reply_connection_status(update, cfg)
            return
        is_admin = is_admin_user_id(user_id(update), cfg)
        if is_admin:
            status_message = await reply_cover_card(update, context, "正在检查版本更新，请稍候...")
            check = await asyncio.to_thread(version_check_sync)
        else:
            status_message = await reply_cover_card(update, context, "正在读取当前版本，请稍候...")
            check = {"current": read_app_version()}
        await edit_or_replace_status_any(status_message, version_text(check, admin_view=is_admin), update, parse_mode="HTML", reply_markup=version_keyboard(check, admin_view=is_admin))

    async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_message:
            return
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                return
            await reply_connection_status(update, cfg)
            return
        sent = await update.effective_message.reply_text(
            proxy_protocol_help_text(),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ 关闭", callback_data="close_message")]]),
        )
        await track_auto_delete_message(sent)

    async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_message:
            return
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                return
            await reply_connection_status(update, cfg)
            return
        text = await asyncio.to_thread(bot_status_text_sync, cfg, cache_path)
        sent = await update.effective_message.reply_text(text, parse_mode="HTML")
        await track_auto_delete_message(sent)

    async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_message:
            return
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                return
            await reply_connection_status(update, cfg)
            return
        status_message = await update.effective_message.reply_text("正在执行健康检查，请稍候...")
        await track_auto_delete_message(status_message)
        admin_view = is_admin_user_id(update.effective_user.id if update.effective_user else None, cfg)
        text = await asyncio.to_thread(bot_health_overview_text_sync, cfg, cache_path, admin_view)
        if len(text) <= 3900:
            await edit_or_replace_status(status_message, text, update, parse_mode="HTML")
            await track_auto_delete_message(status_message)
        else:
            try:
                await status_message.delete()
            except BadRequest:
                pass
            await reply_long_text(update.effective_message, text, parse_mode="HTML")

    def active_users_keyboard(
        selected_period: str | None = None,
        user_buttons: list[tuple[int, str]] | None = None,
        page: int = 0,
    ) -> InlineKeyboardMarkup:
        rows = [[
            InlineKeyboardButton("近 1 小时", callback_data="active_users:1h"),
            InlineKeyboardButton("近 24 小时", callback_data="active_users:24h"),
            InlineKeyboardButton("近 7 天", callback_data="active_users:7d"),
        ]]
        if selected_period:
            rows.append([InlineKeyboardButton("🔎 按用户 ID 查询", callback_data=f"ip_user_query:{selected_period}"), InlineKeyboardButton("🔍 用户列表", callback_data=f"active_users_query:{selected_period}:0")])

        if selected_period and user_buttons is not None:
            page_size = 5
            total_pages = max(1, (len(user_buttons) + page_size - 1) // page_size)
            page = min(max(page, 0), total_pages - 1)
            start = page * page_size
            for user_id, name in user_buttons[start:start + page_size]:
                rows.append([
                    InlineKeyboardButton(
                        name,
                        callback_data=f"active_user_detail:{selected_period}:{user_id}",
                    )
                ])
            if len(user_buttons) > page_size:
                nav_row = []
                if page > 0:
                    nav_row.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"active_users_query:{selected_period}:{page - 1}"))
                nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
                if page < total_pages - 1:
                    nav_row.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"active_users_query:{selected_period}:{page + 1}"))
                rows.append(nav_row)
            rows.append([InlineKeyboardButton("❎ 取消", callback_data=f"active_users_cancel:{selected_period}")])
        if not selected_period:
            rows.append(back_close_row("main_menu:ip_monitor", "⬅️ 返回 IP 监控"))
        return InlineKeyboardMarkup(rows)

    def ip_detail_list_keyboard(kind: str, user_buttons: list[tuple[int, str]], page: int = 0) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        page_size = 5
        total_pages = max(1, (len(user_buttons) + page_size - 1) // page_size)
        page = min(max(page, 0), total_pages - 1)
        start = page * page_size
        for user_id, name in user_buttons[start:start + page_size]:
            rows.append([InlineKeyboardButton(name, callback_data=f"ip_active_user_detail:{kind}:{user_id}")])
        if len(user_buttons) > page_size:
            nav_row: list[InlineKeyboardButton] = []
            if page > 0:
                nav_row.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"ip_detail_list:{kind}:{page - 1}"))
            nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
            if page < total_pages - 1:
                nav_row.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"ip_detail_list:{kind}:{page + 1}"))
            rows.append(nav_row)
        rows.append([InlineKeyboardButton("💫 切换查询周期", callback_data="main_menu:ip_monitor:period"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")])
        return InlineKeyboardMarkup(rows)

    def user_ip_detail_keyboard(kind: str, xboard_user_id: int, total_ips: int, page: int = 0, source: str | None = None) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        page_size = 10
        total_pages = max(1, (total_ips + page_size - 1) // page_size)
        page = min(max(page, 0), total_pages - 1)
        if total_ips > page_size:
            nav_row: list[InlineKeyboardButton] = []
            if page > 0:
                suffix = f":{source}" if source else ""
                nav_row.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"ip_active_user_detail:{kind}:{xboard_user_id}:{page - 1}{suffix}"))
            nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
            if page < total_pages - 1:
                suffix = f":{source}" if source else ""
                nav_row.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"ip_active_user_detail:{kind}:{xboard_user_id}:{page + 1}{suffix}"))
            rows.append(nav_row)
        suffix = f":{source}" if source else ""
        rows.append([
            InlineKeyboardButton("忽略地区", callback_data=f"ip_ignore_page:area:{kind}:{xboard_user_id}:{page}:0{suffix}"),
            InlineKeyboardButton("忽略 ASN", callback_data=f"ip_ignore_page:asn:{kind}:{xboard_user_id}:{page}:0{suffix}"),
            InlineKeyboardButton("忽略 IP", callback_data=f"ip_ignore_page:cidr:{kind}:{xboard_user_id}:{page}:0{suffix}"),
        ])
        back_button = InlineKeyboardButton("⬅️ 返回用户列表", callback_data=f"ip_detail_list:{kind}:0")
        if source == "alert":
            back_button = InlineKeyboardButton("⬅️ 返回告警", callback_data=f"ip_alert_notice:{xboard_user_id}")
        rows.append([
            back_button,
            InlineKeyboardButton("❌ 关闭", callback_data="close_message"),
        ])
        return InlineKeyboardMarkup(rows)

    def alert_user_setting_keyboard_for_source(alert_type: str, xboard_user_id: int, source: str | None = None) -> InlineKeyboardMarkup:
        keyboard = alert_user_setting_keyboard(alert_type, xboard_user_id)
        if source != "alert" or alert_type != "ip":
            return keyboard
        rows = [list(row) for row in keyboard.inline_keyboard]
        rows[-1] = [InlineKeyboardButton("⬅️ 返回告警", callback_data=f"ip_alert_notice:{xboard_user_id}"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")]
        return InlineKeyboardMarkup(rows)

    def detail_keyboard(period_key: str | None = None) -> InlineKeyboardMarkup:
        back_target = period_key if period_key in {"1h", "24h", "7d", "30d"} else "menu"
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("⬅️ 返回", callback_data=f"detail_back:{back_target}"),
            InlineKeyboardButton("❌ 关闭", callback_data="close_message"),
        ]])

    def user_ip_query_page_keyboard(period_key: str | None, xboard_user_id: int, total_ips: int, page: int = 0) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        page_size = 10
        total_pages = max(1, (total_ips + page_size - 1) // page_size)
        page = min(max(page, 0), total_pages - 1)
        period_spec = period_key or "all"
        if total_ips > page_size:
            nav_row: list[InlineKeyboardButton] = []
            if page > 0:
                nav_row.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"user_ip_page:{xboard_user_id}:{page - 1}:{period_spec}"))
            nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
            if page < total_pages - 1:
                nav_row.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"user_ip_page:{xboard_user_id}:{page + 1}:{period_spec}"))
            rows.append(nav_row)
        back_target = period_key if period_key in {"1h", "24h", "7d", "30d"} else "menu"
        rows.append([
            InlineKeyboardButton("⬅️ 返回", callback_data=f"detail_back:{back_target}"),
            InlineKeyboardButton("❌ 关闭", callback_data="close_message"),
        ])
        return InlineKeyboardMarkup(rows)

    def traffic_period_keyboard(dimension: str = "combined", source_kind: str | None = None) -> InlineKeyboardMarkup:
        suffix = f":{dimension}" if dimension in {"users", "nodes"} else ""
        prefix = "traffic_switch" if source_kind else "traffic_period"
        rows = [
            [
                InlineKeyboardButton("近 1 小时", callback_data=f"{prefix}:preset_1h{suffix}"),
                InlineKeyboardButton("近 24 小时", callback_data=f"{prefix}:preset_24h{suffix}"),
            ],
            [
                InlineKeyboardButton("近 7 天", callback_data=f"{prefix}:preset_7d{suffix}"),
                InlineKeyboardButton("近 30 天", callback_data=f"{prefix}:preset_30d{suffix}"),
            ],
            [
                InlineKeyboardButton("今天", callback_data=f"{prefix}:today{suffix}"),
                InlineKeyboardButton("昨天", callback_data=f"{prefix}:yesterday{suffix}"),
            ],
            [
                InlineKeyboardButton("本周", callback_data=f"{prefix}:this_week{suffix}"),
                InlineKeyboardButton("本月", callback_data=f"{prefix}:this_month{suffix}"),
            ],
        ]
        custom_callback = f"traffic_custom:start:{dimension}"
        if source_kind:
            rows.append([InlineKeyboardButton("自选周期", callback_data=custom_callback)])
            rows.append([InlineKeyboardButton("⬅️ 返回结果", callback_data=f"traffic_back:{source_kind}")])
        else:
            rows.append([InlineKeyboardButton("自选周期", callback_data=custom_callback)])
        rows.append([InlineKeyboardButton("❌ 关闭", callback_data="close_message")])
        return InlineKeyboardMarkup(rows)

    def traffic_dashboard_keyboard(kind: str, is_pinned: bool = False) -> InlineKeyboardMarkup:
        return traffic_dashboard_keyboard_static(kind, is_pinned)



    def traffic_dashboard_text(kind: str) -> str:
        return traffic_dashboard_text_from_kind_sync(cache_path, kind)

    async def auto_delete_unpinned_dashboard(chat_id: str, message_id: int, kind: str) -> None:
        await asyncio.sleep(180)
        is_pinned = await asyncio.to_thread(auto_delete_message_is_pinned_sync, cache_path, chat_id, message_id)
        if is_pinned:
            return
        try:
            await app.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except BadRequest:
            pass
        await asyncio.to_thread(pinned_dashboard_delete_message_sync, cache_path, chat_id, message_id)
        await asyncio.to_thread(auto_delete_message_delete_sync, cache_path, chat_id, message_id)

    async def send_dashboard_card(message: Any, kind: str, user_pref_id: int | None = None) -> None:
        chat_id = str(message.chat_id)
        text = await asyncio.to_thread(traffic_dashboard_text, kind)
        reply_markup = traffic_dashboard_keyboard(kind, is_pinned=False)
        custom_cover = None
        try:
            sender = getattr(message, "from_user", None)
            pref_id = user_pref_id or (sender.id if sender else None)
            if pref_id:
                custom_cover = await asyncio.to_thread(ui_pref_get_sync, cache_path, pref_id, "cover_file_id")
        except Exception:
            custom_cover = None
        sent = None
        if custom_cover:
            try:
                sent = await message.reply_photo(photo=custom_cover, caption=text, parse_mode="HTML", reply_markup=reply_markup)
            except Exception as exc:
                log.warning("发送自定义题图结果失败，改为文本消息：%s", exc)
        if sent is None:
            try:
                me = await app.bot.get_me()
                photos = await app.bot.get_user_profile_photos(me.id, limit=1)
                if photos.total_count > 0 and photos.photos:
                    sent = await message.reply_photo(photo=photos.photos[0][-1].file_id, caption=text, parse_mode="HTML", reply_markup=reply_markup)
            except Exception as exc:
                log.warning("发送 Bot 头像结果失败，改为文本消息：%s", exc)
        if sent is None:
            sent = await message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
        await asyncio.to_thread(pinned_dashboard_set_sync, cache_path, kind, chat_id, sent.message_id, False)
        await asyncio.to_thread(auto_delete_message_set_sync, cache_path, chat_id, sent.message_id, False)
        asyncio.create_task(auto_delete_unpinned_dashboard(chat_id, sent.message_id, kind))

    async def edit_dashboard_card(query: Any, kind: str) -> None:
        chat_id = str(query.message.chat_id)
        text = await asyncio.to_thread(traffic_dashboard_text, kind)
        is_pinned = await asyncio.to_thread(auto_delete_message_is_pinned_sync, cache_path, chat_id, query.message.message_id)
        reply_markup = traffic_dashboard_keyboard(kind, is_pinned=is_pinned)
        await show_callback_page(query, text, reply_markup, parse_mode="HTML")
        await asyncio.to_thread(pinned_dashboard_set_sync, cache_path, kind, chat_id, query.message.message_id, is_pinned)
        await asyncio.to_thread(auto_delete_message_set_sync, cache_path, chat_id, query.message.message_id, is_pinned)

    def traffic_custom_state(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
        state = context.user_data.setdefault("traffic_custom", {})
        return state if isinstance(state, dict) else {}

    def traffic_custom_available_bounds() -> tuple[int, int]:
        first = earliest_traffic_sample_at_sync(cache_path)
        now_ts = int(datetime.now().timestamp())
        return (first or now_ts, now_ts)

    def traffic_custom_single_year() -> bool:
        first_ts, now_ts = traffic_custom_available_bounds()
        return datetime.fromtimestamp(first_ts).year == datetime.fromtimestamp(now_ts).year

    def traffic_custom_enter_initial_step(state: dict[str, Any]) -> None:
        if state.get("mode") in {"custom", "ip_custom"} and traffic_custom_single_year():
            _, now_ts = traffic_custom_available_bounds()
            state["year"] = datetime.fromtimestamp(now_ts).year
            state["step"] = "month"
        else:
            state.pop("year", None)
            state["step"] = "year"

    def traffic_custom_prompt_text(state: dict[str, Any]) -> str:
        first_ts, _ = traffic_custom_available_bounds()
        step = str(state.get("step") or "year")
        step_label = {"year": "年份", "month": "月份", "day": "日期", "hour": "小时", "minute": "分钟"}.get(step, "时间")

        def selected_combo_text() -> str:
            parts: list[str] = []
            if state.get("year"):
                parts.append(f"{int(state['year'])} 年")
            if state.get("month"):
                parts.append(f"{int(state['month']):02d} 月")
            if state.get("day"):
                parts.append(f"{int(state['day']):02d} 日")
            if state.get("hour") is not None:
                parts.append(f"{int(state['hour']):02d} 时")
            if state.get("minute") is not None:
                parts.append(f"{int(state['minute']):02d} 分")
            return " ".join(parts) if parts else "尚未选择"

        if state.get("mode") == "floor":
            lines = [
                "⚙️ 调整起始点",
                f"请选择新的统计起始点的{step_label}。",
                f"（当前可选择的最早时间：{format_timestamp(first_ts)}）",
                f"已选起始点：{selected_combo_text()}",
                "确认后会删除该时间之前的本地统计缓存，后续周期统计也不会再使用这些旧数据。",
            ]
            return "\n".join(lines)
        phase = state.get("phase", "start")
        target_text = "开始时间" if phase == "start" else "结束时间"
        lines = [
            f"请选择{target_text}的{step_label}。",
            f"（可选择的最早时间：{format_timestamp(first_ts)}）",
        ]
        if phase == "start":
            lines.append(f"已选开始：{selected_combo_text()}")
            if state.get("end_ts"):
                lines.append(f"已选结束：{format_timestamp(int(state['end_ts']))}")
        else:
            if state.get("start_ts"):
                lines.append(f"已选开始：{format_timestamp(int(state['start_ts']))}")
            lines.append(f"已选结束：{selected_combo_text()}")
        return "\n".join(lines)

    def traffic_custom_year_keyboard(mode: str | None = None, include_now: bool = False, dimension: str = "combined") -> InlineKeyboardMarkup:
        first_ts, now_ts = traffic_custom_available_bounds()
        first_year = datetime.fromtimestamp(first_ts).year
        now_year = datetime.fromtimestamp(now_ts).year
        rows = []
        row = []
        for year in range(first_year, now_year + 1):
            row.append(InlineKeyboardButton(str(year), callback_data=f"traffic_custom:year:{year}"))
            if len(row) == 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        if include_now:
            rows.append([InlineKeyboardButton("⏰ 至今", callback_data="traffic_custom:now")])
        if mode == "ip_custom":
            back_callback = "main_menu:ip_monitor:period"
        elif mode == "floor":
            back_callback = "main_menu:debug:reset_cache"
        else:
            back_callback = "traffic_menu"
        rows.append([InlineKeyboardButton("⬅️ 返回", callback_data=back_callback), InlineKeyboardButton("❌ 关闭", callback_data="close_message")])
        return InlineKeyboardMarkup(rows)

    def traffic_custom_month_keyboard(year: int, include_now: bool = False, mode: str | None = None, dimension: str = "combined") -> InlineKeyboardMarkup:
        first_ts, now_ts = traffic_custom_available_bounds()
        first_dt = datetime.fromtimestamp(first_ts)
        now_dt = datetime.fromtimestamp(now_ts)
        start_month = first_dt.month if year == first_dt.year else 1
        end_month = now_dt.month if year == now_dt.year else 12
        rows = []
        row = []
        for month in range(start_month, end_month + 1):
            row.append(InlineKeyboardButton(f"{month}月", callback_data=f"traffic_custom:month:{month}"))
            if len(row) == 4:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        if include_now:
            rows.append([InlineKeyboardButton("⏰ 至今", callback_data="traffic_custom:now")])
        if traffic_custom_single_year():
            back_callback = "main_menu:ip_monitor:period" if mode == "ip_custom" else "traffic_menu"
            rows.append([InlineKeyboardButton("⬅️ 返回", callback_data=back_callback), InlineKeyboardButton("❌ 关闭", callback_data="close_message")])
        else:
            rows.append([InlineKeyboardButton("⬅️ 返回年份", callback_data="traffic_custom:back:year"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")])
        return InlineKeyboardMarkup(rows)

    def traffic_custom_day_keyboard(year: int, month: int, mode: str | None = None, dimension: str = "combined") -> InlineKeyboardMarkup:
        first_ts, now_ts = traffic_custom_available_bounds()
        first_dt = datetime.fromtimestamp(first_ts)
        now_dt = datetime.fromtimestamp(now_ts)
        _, days_in_month = calendar.monthrange(year, month)
        start_day = first_dt.day if year == first_dt.year and month == first_dt.month else 1
        end_day = now_dt.day if year == now_dt.year and month == now_dt.month else days_in_month
        rows = []
        row = []
        for day in range(start_day, end_day + 1):
            row.append(InlineKeyboardButton(str(day), callback_data=f"traffic_custom:day:{day}"))
            if len(row) == 7:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([InlineKeyboardButton("⬅️ 返回月份", callback_data="traffic_custom:back:month"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")])
        return InlineKeyboardMarkup(rows)

    def traffic_custom_hour_keyboard(year: int, month: int, day: int, mode: str | None = None, dimension: str = "combined") -> InlineKeyboardMarkup:
        first_ts, now_ts = traffic_custom_available_bounds()
        first_dt = datetime.fromtimestamp(first_ts)
        now_dt = datetime.fromtimestamp(now_ts)
        start_hour = first_dt.hour if (year, month, day) == (first_dt.year, first_dt.month, first_dt.day) else 0
        end_hour = now_dt.hour if (year, month, day) == (now_dt.year, now_dt.month, now_dt.day) else 23
        rows = []
        row = []
        for hour in range(start_hour, end_hour + 1):
            row.append(InlineKeyboardButton(f"{hour:02d}", callback_data=f"traffic_custom:hour:{hour}"))
            if len(row) == 6:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([InlineKeyboardButton("⬅️ 返回日期", callback_data="traffic_custom:back:day"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")])
        return InlineKeyboardMarkup(rows)

    def traffic_custom_minute_keyboard(year: int, month: int, day: int, hour: int, mode: str | None = None, dimension: str = "combined") -> InlineKeyboardMarkup:
        first_ts, now_ts = traffic_custom_available_bounds()
        first_dt = datetime.fromtimestamp(first_ts)
        now_dt = datetime.fromtimestamp(now_ts)
        start_minute = first_dt.minute if (year, month, day, hour) == (first_dt.year, first_dt.month, first_dt.day, first_dt.hour) else 0
        end_minute = now_dt.minute if (year, month, day, hour) == (now_dt.year, now_dt.month, now_dt.day, now_dt.hour) else 59
        rows = []
        row = []
        for minute in range(start_minute, end_minute + 1):
            row.append(InlineKeyboardButton(f"{minute:02d}", callback_data=f"traffic_custom:minute:{minute}"))
            if len(row) == 6:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([InlineKeyboardButton("⬅️ 返回小时", callback_data="traffic_custom:back:hour"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")])
        return InlineKeyboardMarkup(rows)

    def traffic_floor_confirm_keyboard(floor_ts: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ 确认调整起始点", callback_data=f"traffic_floor:confirm:{floor_ts}")],
            [InlineKeyboardButton("🔄 重新选择", callback_data="traffic_floor:start"), InlineKeyboardButton("❎ 取消", callback_data="main_menu")],
        ])

    def traffic_custom_keyboard_for_state(state: dict[str, Any]) -> InlineKeyboardMarkup:
        step = state.get("step", "year")
        year = int(state.get("year") or 0)
        month = int(state.get("month") or 0)
        day = int(state.get("day") or 0)
        hour = int(state.get("hour") or 0)
        mode = str(state.get("mode") or "")
        dimension = str(state.get("dimension") or "combined")
        if step == "month" and year:
            include_now = state.get("phase") == "end" and state.get("mode") in {"custom", "ip_custom"} and traffic_custom_single_year()
            return traffic_custom_month_keyboard(year, include_now=include_now, mode=mode, dimension=dimension)
        if step == "day" and year and month:
            return traffic_custom_day_keyboard(year, month, mode=mode, dimension=dimension)
        if step == "hour" and year and month and day:
            return traffic_custom_hour_keyboard(year, month, day, mode=mode, dimension=dimension)
        if step == "minute" and year and month and day:
            return traffic_custom_minute_keyboard(year, month, day, hour, mode=mode, dimension=dimension)
        include_now = state.get("phase") == "end" and state.get("step") == "year" and state.get("mode") in {"custom", "ip_custom"}
        return traffic_custom_year_keyboard(mode, include_now=include_now, dimension=dimension)

    def traffic_fixed_range(kind: str) -> tuple[int, int, str] | None:
        now = datetime.now()
        if kind == "today":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = now
            return int(start.timestamp()), int(end.timestamp()), "今天"
        if kind == "yesterday":
            start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            end = start.replace(hour=23, minute=59, second=59)
            return int(start.timestamp()), int(end.timestamp()), "昨天"
        if kind == "this_week":
            start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
            return int(start.timestamp()), int(now.timestamp()), "本周"
        if kind == "this_month":
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            return int(start.timestamp()), int(now.timestamp()), "本月"
        return None


    async def traffic_daily(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_message:
            return
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                return
            await reply_connection_status(update, cfg)
            return
        sent = await update.effective_message.reply_text("🌊 请选择统计周期：", reply_markup=traffic_period_keyboard())
        await track_auto_delete_message(sent)

    async def send_or_jump_traffic_dashboard(message: Any, kind: str) -> None:
        sender = getattr(message, "from_user", None)
        await send_dashboard_card(message, kind, sender.id if sender else None)


    async def traffic_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_message:
            return
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                return
            await reply_connection_status(update, cfg)
            return
        await send_or_jump_traffic_dashboard(update.effective_message, "users_preset_24h")

    async def traffic_nodes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_message:
            return
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                return
            await reply_connection_status(update, cfg)
            return
        await send_or_jump_traffic_dashboard(update.effective_message, "nodes_preset_24h")

    async def open_traffic_dashboard_message(query: Any, kind: str) -> None:
        if not query.message:
            return
        await query.answer("正在生成查询...")
        await send_or_jump_traffic_dashboard(query.message, kind)

    async def switch_traffic_dashboard_message(query: Any, kind: str) -> None:
        if not query.message:
            return
        await query.answer("正在切换周期...")
        await edit_dashboard_card(query, kind)

    async def traffic_daily_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.message:
            return
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                return
            await query.answer("未授权", show_alert=True)
            return
        data = query.data or ""

        menu_match = re.fullmatch(r"traffic_menu(?::([A-Za-z0-9_]+))?", data)
        if menu_match:
            source_kind = menu_match.group(1)
            dimension = traffic_dimension_from_kind(source_kind or "combined")
            await query.answer()
            await show_callback_page(query, "🌊 请选择统计周期：", traffic_period_keyboard(dimension, source_kind))
            return

        back_match = re.fullmatch(r"traffic_back:([A-Za-z0-9_]+)", data)
        if back_match:
            await query.answer()
            await edit_dashboard_card(query, back_match.group(1))
            return

        period_match = re.fullmatch(r"traffic_period:(preset_1h|preset_24h|preset_7d|preset_30d|today|yesterday|this_week|this_month)(?::(users|nodes))?", data)
        if period_match:
            selected = period_match.group(1)
            dimension = period_match.group(2) or "combined"
            if selected.startswith("preset_"):
                await open_traffic_dashboard_message(query, traffic_kind_for_dimension(dimension, selected))
                return
            fixed = traffic_fixed_range(selected)
            if not fixed:
                await query.answer("请求无效，请重新进入。", show_alert=True)
                return
            start_ts, end_ts, label = fixed
            base_kind = make_range_kind(start_ts, end_ts, label)
            await asyncio.to_thread(save_traffic_range_sync, cache_path, base_kind, start_ts, end_ts, label)
            await open_traffic_dashboard_message(query, traffic_kind_for_dimension(dimension, base_kind))
            return

        switch_match = re.fullmatch(r"traffic_switch:(preset_1h|preset_24h|preset_7d|preset_30d|today|yesterday|this_week|this_month):(users|nodes)", data)
        if switch_match:
            selected = switch_match.group(1)
            dimension = switch_match.group(2)
            if selected.startswith("preset_"):
                await switch_traffic_dashboard_message(query, traffic_kind_for_dimension(dimension, selected))
                return
            fixed = traffic_fixed_range(selected)
            if not fixed:
                await query.answer("请求无效，请重新进入。", show_alert=True)
                return
            start_ts, end_ts, label = fixed
            base_kind = make_range_kind(start_ts, end_ts, label)
            await asyncio.to_thread(save_traffic_range_sync, cache_path, base_kind, start_ts, end_ts, label)
            await switch_traffic_dashboard_message(query, traffic_kind_for_dimension(dimension, base_kind))
            return

        if data == "ip_custom:start":
            state = traffic_custom_state(context)
            state.clear()
            state.update({"mode": "ip_custom", "phase": "start"})
            traffic_custom_enter_initial_step(state)
            await query.answer()
            await show_callback_page(query, traffic_custom_prompt_text(state), traffic_custom_keyboard_for_state(state))
            return

        traffic_custom_start_match = re.fullmatch(r"traffic_custom:start(?::(combined|users|nodes))?", data)
        if traffic_custom_start_match:
            dimension = traffic_custom_start_match.group(1) or "combined"
            state = traffic_custom_state(context)
            state.clear()
            state.update({"mode": "custom", "dimension": dimension, "phase": "start"})
            traffic_custom_enter_initial_step(state)
            await query.answer()
            await show_callback_page(query, traffic_custom_prompt_text(state), traffic_custom_keyboard_for_state(state))
            return

        if data == "traffic_floor:start":
            state = traffic_custom_state(context)
            state.clear()
            state.update({"mode": "floor", "phase": "floor", "step": "year"})
            await query.answer()
            await show_callback_page(query, traffic_custom_prompt_text(state), traffic_custom_year_keyboard(str(state.get("mode") or "")))
            return

        floor_confirm_match = re.fullmatch(r"traffic_floor:confirm:(\d+)", data)
        if floor_confirm_match:
            floor_ts = int(floor_confirm_match.group(1))
            was_debug_reset = bool(context.user_data.get("traffic_custom", {}).get("debug"))
            counts = await asyncio.to_thread(prune_stats_before_sync, cache_path, floor_ts)
            await asyncio.to_thread(log_operation_from_query, query, "reset_cache", "调整统计起始点", f"{format_timestamp(floor_ts)}，流量样本 {counts['traffic_delta_samples']} 条")
            context.user_data.pop("traffic_custom", None)
            await query.answer("统计起始点已重置")
            text = (
                "✅ 起始点调整完成\n\n"
                f"新的统计起始点：{format_timestamp(floor_ts)}\n"
                f"已删除流量样本：{counts['traffic_delta_samples']} 条\n"
                f"已删除采样中断记录：{counts['traffic_sample_gaps']} 条\n"
                f"已删除历史自定义范围：{counts['traffic_ranges']} 条\n"
                f"已删除活跃 IP 记录：{counts['active_ip_records']} 条\n\n"
                "后续缓存采集、流量采样和周期统计会基于新的本地缓存起点。"
            )
            if was_debug_reset:
                await show_callback_page(
                    query,
                    text + "\n\n请进入健康检查页，观察重新采集与采样情况。",
                    InlineKeyboardMarkup([
                        [InlineKeyboardButton("🩺 前往健康检查", callback_data="main_menu:system_check")],
                        back_close_row("main_menu:debug_tools", "⬅️ 返回调试功能"),
                    ]),
                )
            else:
                await show_callback_page(query, text, traffic_period_keyboard())
            return

        if data == "traffic_custom:now":
            state = traffic_custom_state(context)
            if state.get("phase") != "end" or not state.get("start_ts"):
                await query.answer("请求无效，请重新进入。", show_alert=True)
                return
            start_ts = int(state.get("start_ts") or 0)
            end_ts = int(datetime.now().timestamp())
            if end_ts <= start_ts:
                await query.answer("结束时间必须晚于开始时间", show_alert=True)
                return
            label = f"自定义 {datetime.fromtimestamp(start_ts).strftime('%Y-%m-%d %H:%M')} - {datetime.fromtimestamp(end_ts).strftime('%Y-%m-%d %H:%M')}"
            mode = state.get("mode")
            dimension = str(state.get("dimension") or "combined")
            state.clear()
            if mode == "ip_custom":
                await query.answer("正在生成查询...")
                await send_dashboard_card(query.message, ip_range_kind(start_ts, end_ts), query.from_user.id)
                return
            base_kind = make_range_kind(start_ts, end_ts, label)
            await asyncio.to_thread(save_traffic_range_sync, cache_path, base_kind, start_ts, end_ts, label)
            await open_traffic_dashboard_message(query, traffic_kind_for_dimension(dimension, base_kind))
            return

        custom_match = re.fullmatch(r"traffic_custom:(year|month|day|hour|minute):(\d+)", data)
        if custom_match:
            field = custom_match.group(1)
            value = int(custom_match.group(2))
            state = traffic_custom_state(context)
            state[field] = value
            next_step = {"year": "month", "month": "day", "day": "hour", "hour": "minute"}.get(field)
            if next_step:
                state["step"] = next_step
                await query.answer()
                await show_callback_page(query, traffic_custom_prompt_text(state), traffic_custom_keyboard_for_state(state))
                return

            year = int(state.get("year") or 0)
            month = int(state.get("month") or 0)
            day = int(state.get("day") or 0)
            hour = int(state.get("hour") or 0)
            minute = int(state.get("minute") or 0)
            phase = state.get("phase", "start")
            second = 0 if phase in {"start", "floor"} else 59
            selected_ts = int(datetime(year, month, day, hour, minute, second).timestamp())
            if state.get("mode") == "floor":
                preview = await asyncio.to_thread(preview_prune_stats_before_sync, cache_path, selected_ts)
                await query.answer()
                await show_callback_page(
                    query,
                    "⚠️ 请确认调整起始点\n\n"
                    f"新的统计起始点：{format_timestamp(selected_ts)}\n\n"
                    "确认后会删除该时间之前的本地缓存与采样：\n"
                    f"流量样本：{preview['traffic_delta_samples']} 条\n"
                    f"采样中断记录：{preview['traffic_sample_gaps']} 条\n"
                    f"历史自定义范围：{preview['traffic_ranges']} 条\n"
                    f"活跃 IP 记录：{preview['active_ip_records']} 条\n\n"
                    "这个操作不会修改 XBoard / MySQL，只影响 Bot 本地 SQLite。",
                    traffic_floor_confirm_keyboard(selected_ts),
                )
                return
            if phase == "start":
                state["start_ts"] = selected_ts
                for k in ("year", "month", "day", "hour", "minute"):
                    state.pop(k, None)
                state.update({"phase": "end"})
                traffic_custom_enter_initial_step(state)
                await query.answer("开始时间已选择")
                await show_callback_page(query, traffic_custom_prompt_text(state), traffic_custom_keyboard_for_state(state))
                return
            state["end_ts"] = selected_ts
            start_ts = int(state.get("start_ts") or 0)
            end_ts = int(state.get("end_ts") or 0)
            if end_ts <= start_ts:
                await query.answer("结束时间必须晚于开始时间", show_alert=True)
                return
            label = f"自定义 {datetime.fromtimestamp(start_ts).strftime('%Y-%m-%d %H:%M')} - {datetime.fromtimestamp(end_ts).strftime('%Y-%m-%d %H:%M')}"
            mode = state.get("mode")
            dimension = str(state.get("dimension") or "combined")
            state.clear()
            if mode == "ip_custom":
                await query.answer("正在生成查询...")
                await send_dashboard_card(query.message, ip_range_kind(start_ts, end_ts), query.from_user.id)
                return
            base_kind = make_range_kind(start_ts, end_ts, label)
            await asyncio.to_thread(save_traffic_range_sync, cache_path, base_kind, start_ts, end_ts, label)
            await open_traffic_dashboard_message(query, traffic_kind_for_dimension(dimension, base_kind))
            return

        back_match = re.fullmatch(r"traffic_custom:back:(year|month|day|hour)", data)
        if back_match:
            target = back_match.group(1)
            state = traffic_custom_state(context)
            cleanup = {
                "year": ("year", "month", "day", "hour", "minute"),
                "month": ("month", "day", "hour", "minute"),
                "day": ("day", "hour", "minute"),
                "hour": ("hour", "minute"),
            }[target]
            for key in cleanup:
                state.pop(key, None)
            state["step"] = target
            await query.answer()
            await show_callback_page(query, traffic_custom_prompt_text(state), traffic_custom_keyboard_for_state(state))
            return

        match = re.fullmatch(r"traffic_dashboard:(pin|unpin|delete):([A-Za-z0-9_]+)", data)
        if not match:
            await query.answer("请求无效，请重新进入。", show_alert=True)
            return
        action, kind = match.group(1), match.group(2)
        chat_id = str(query.message.chat_id)

        if action == "pin":
            try:
                await query.message.pin(disable_notification=True)
            except BadRequest as exc:
                await query.answer(f"置顶失败：{exc.message}", show_alert=True)
                return
            await asyncio.to_thread(pinned_dashboard_set_sync, cache_path, kind, chat_id, query.message.message_id, True)
            await asyncio.to_thread(auto_delete_message_set_sync, cache_path, chat_id, query.message.message_id, True)
            await query.answer("已置顶")
            await query.message.edit_reply_markup(reply_markup=traffic_dashboard_keyboard(kind, is_pinned=True))
            return

        if action == "unpin":
            try:
                await query.message.unpin()
            except BadRequest as exc:
                await query.answer(f"取消置顶失败：{exc.message}", show_alert=True)
                return
            await asyncio.to_thread(pinned_dashboard_set_sync, cache_path, kind, chat_id, query.message.message_id, False)
            await asyncio.to_thread(auto_delete_message_set_sync, cache_path, chat_id, query.message.message_id, False)
            await query.answer("已取消置顶")
            await query.message.edit_reply_markup(reply_markup=traffic_dashboard_keyboard(kind, is_pinned=False))
            return

        if action == "delete":
            await asyncio.to_thread(pinned_dashboard_delete_sync, cache_path, kind, chat_id)
            await asyncio.to_thread(auto_delete_message_delete_sync, cache_path, chat_id, query.message.message_id)
            await query.answer("已删除")
            try:
                await query.message.delete()
            except BadRequest:
                pass
            return


    async def version_update_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.message:
            return
        if not is_allowed(update, cfg):
            await query.answer("未授权，无法使用该功能", show_alert=True)
            return
        if not is_admin_user_id(query.from_user.id, cfg):
            await query.answer("只有管理员可以执行版本更新", show_alert=True)
            return
        data = query.data or ""
        if data == "version_update:cancel":
            await query.answer("已取消更新")
            check = await asyncio.to_thread(version_check_sync)
            await show_callback_page(query, version_text(check, admin_view=True), version_keyboard(check, admin_view=True), parse_mode="HTML")
            return
        m = re.fullmatch(r"version_update:start:(v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)", data)
        if m:
            target = m.group(1)
            await query.answer()
            await show_callback_page(query, update_started_text(target), update_confirm_keyboard(target), parse_mode="HTML")
            return
        m = re.fullmatch(r"version_update:confirm:(v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)", data)
        if m:
            target = m.group(1)
            await query.answer("后台更新已启动")
            ok, message = await asyncio.to_thread(start_background_update_sync, target, str(query.message.chat_id))
            if ok:
                await show_callback_page(
                    query,
                    "⬆️ <b>后台更新已启动</b>\n────────────\n"
                    f"目标版本：<code>{html.escape(target)}</code>\n\n"
                    "更新过程会在后台执行，Bot 可能会短暂离线。\n"
                    "更新成功或失败后，我会主动推送结果通知。",
                    InlineKeyboardMarkup([[InlineKeyboardButton("❌ 关闭", callback_data="close_message")]]),
                    parse_mode="HTML",
                )
            else:
                await show_callback_page(
                    query,
                    "❌ <b>无法启动后台更新</b>\n────────────\n" + html.escape(message),
                    version_keyboard(await asyncio.to_thread(version_check_sync), admin_view=True),
                    parse_mode="HTML",
                )
            return
        await query.answer("请求无效，请重新进入。", show_alert=True)

    async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.message:
            return
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                return
            await query.answer("未授权，无法使用该功能", show_alert=True)
            return

        data = query.data or ""
        sections = {
            "main_menu:status_notice": "💬 通知推送",
            "main_menu:debug_tools": "🧪 调试功能",
        }

        if data == "main_menu":
            await query.answer()
            user = query.from_user
            custom_name = await asyncio.to_thread(ui_pref_get_sync, cache_path, user.id, "nickname")
            tg_name = html.escape(str(custom_name or user.full_name or user.username or user.id))
            is_admin = is_admin_user_id(user.id, cfg)
            role_emoji = "👑" if is_admin else "🎩"
            await show_callback_page(query, f"{role_emoji} {tg_name}，<b>请选择功能</b>", main_menu_keyboard(is_admin), parse_mode="HTML")
            return

        if data.startswith("main_menu:op_logs"):
            if not is_admin_user_id(query.from_user.id, cfg):
                await query.answer("只有管理员可以查看操作日志", show_alert=True)
                return
            if data == "main_menu:op_logs":
                await query.answer()
                keyboard = await asyncio.to_thread(operation_logs_menu_keyboard, query.from_user.id)
                await show_callback_page(query, "📜 <b>操作日志</b>\n────────────\n请选择要查看的操作类型。\n\n按钮括号为：未读日志数量/所有日志数量。", keyboard, parse_mode="HTML")
                return
            detail_match = re.fullmatch(r"main_menu:op_logs:(traffic_alert|ip_alert|ip_ignore|reset_cache|reset_ip|auth):(\d+)", data)
            if detail_match:
                category = detail_match.group(1)
                log_id = int(detail_match.group(2))
                await asyncio.to_thread(operation_log_mark_read_sync, cache_path, query.from_user.id, log_id)
                await query.answer("已标记为已读")
                await show_callback_page(query, await asyncio.to_thread(operation_log_detail_text_sync, log_id), operation_log_detail_keyboard(category), parse_mode="HTML")
                return
            log_match = re.fullmatch(r"main_menu:op_logs:(traffic_alert|ip_alert|ip_ignore|reset_cache|reset_ip|auth)", data)
            if log_match:
                category = log_match.group(1)
                await query.answer()
                text = await asyncio.to_thread(operation_log_summary_text_sync, category, query.from_user.id)
                keyboard = await asyncio.to_thread(operation_logs_summary_keyboard, category, query.from_user.id)
                await show_callback_page(query, text, keyboard, parse_mode="HTML")
                return

        if data.startswith("main_menu:auth"):
            if not is_admin_user_id(query.from_user.id, cfg):
                await query.answer("只有管理员可以使用授权管理", show_alert=True)
                return
            is_super_admin = is_super_admin_user_id(query.from_user.id, cfg)
            if data == "main_menu:auth":
                context.user_data.pop("awaiting_auth_add_user_id", None)
                context.user_data.pop("auth_delete_selected", None)
                context.user_data.pop("auth_role_changes", None)
                for target_uid in sorted(cfg.telegram.allowed_user_ids):
                    await resolve_telegram_user_label(target_uid)
                await query.answer()
                await show_callback_page(query, await asyncio.to_thread(telegram_authorization_list_text_sync), authorization_manage_keyboard(is_super_admin), parse_mode="HTML")
                return
            if data == "main_menu:auth:add":
                context.user_data["awaiting_auth_add_user_id"] = {"chat_id": query.message.chat_id, "message_id": query.message.message_id}
                await query.answer()
                await show_callback_page(query, "🔐 <b>增加授权</b>\n────────────\n请输入要授权的 Telegram 用户 ID。", InlineKeyboardMarkup([back_close_row("main_menu:auth", "⬅️ 返回授权管理")]), parse_mode="HTML")
                return
            if data == "main_menu:auth:roles":
                if not is_super_admin:
                    await query.answer("只有超级管理员可以设置普通管理员", show_alert=True)
                    return
                context.user_data["auth_role_changes"] = {}
                await query.answer()
                await show_callback_page(query, authorization_role_change_text(context), authorization_role_change_keyboard(context), parse_mode="HTML")
                return
            role_toggle_match = re.fullmatch(r"main_menu:auth:role_toggle:(\d+)", data)
            if role_toggle_match:
                if not is_super_admin:
                    await query.answer("只有超级管理员可以设置普通管理员", show_alert=True)
                    return
                target_uid = int(role_toggle_match.group(1))
                if target_uid == cfg.telegram.admin_user_id:
                    await query.answer("超级管理员只能通过环境变量修改", show_alert=True)
                    return
                current_role = "manager" if target_uid in cfg.telegram.manager_user_ids else "user"
                role_changes = context.user_data.get("auth_role_changes") or {}
                if not isinstance(role_changes, dict):
                    role_changes = {}
                base_role = "manager" if target_uid in cfg.telegram.manager_user_ids else "user"
                next_role = "user" if str(role_changes.get(target_uid, current_role)) == "manager" else "manager"
                if next_role == base_role:
                    role_changes.pop(target_uid, None)
                else:
                    role_changes[target_uid] = next_role
                context.user_data["auth_role_changes"] = role_changes
                await query.answer("已切换，保存后生效")
                await show_callback_page(query, authorization_role_change_text(context), authorization_role_change_keyboard(context), parse_mode="HTML")
                return
            if data == "main_menu:auth:role_save":
                if not is_super_admin:
                    await query.answer("只有超级管理员可以设置普通管理员", show_alert=True)
                    return
                role_changes = context.user_data.get("auth_role_changes") or {}
                if not isinstance(role_changes, dict) or not role_changes:
                    await query.answer("没有待保存的权限变更", show_alert=True)
                    return
                promote_ids = {int(uid) for uid, role in role_changes.items() if role == "manager"}
                demote_ids = {int(uid) for uid, role in role_changes.items() if role == "user"}
                before_managers = sorted(cfg.telegram.manager_user_ids)
                before_users = sorted(cfg.telegram.authorized_user_ids)
                await asyncio.to_thread(update_telegram_roles_in_cache_sync, cache_path, cfg.telegram.admin_user_id, cfg.telegram.manager_user_ids, cfg.telegram.authorized_user_ids, promote_manager_user_ids=promote_ids, demote_manager_user_ids=demote_ids)
                after_managers = sorted((set(before_managers) | promote_ids) - demote_ids)
                after_users = sorted((set(before_users) | demote_ids) - promote_ids)
                await asyncio.to_thread(log_operation_from_query, query, "auth", "权限变更", f"修改前管理员：{', '.join(str(uid) for uid in before_managers) or '空'}\n修改后管理员：{', '.join(str(uid) for uid in after_managers) or '空'}\n修改前普通用户：{', '.join(str(uid) for uid in before_users) or '空'}\n修改后普通用户：{', '.join(str(uid) for uid in after_users) or '空'}")
                context.user_data.pop("auth_role_changes", None)
                await query.answer("权限变更已保存")
                await show_callback_page(query, "✅ 权限变更已保存。\n变更已保存。", authorization_manage_keyboard(is_super_admin), parse_mode="HTML")
                return
            if data == "main_menu:auth:delete":
                context.user_data["auth_delete_selected"] = set()
                await query.answer()
                delete_hint = "请选择要删除授权的用户。\n超级管理员不可通过 Bot 删除。" if is_super_admin else "请选择要删除授权的普通用户。\n普通管理员不可删除管理员。"
                await show_callback_page(query, "🔓 <b>删除授权</b>\n────────────\n" + delete_hint, authorization_delete_keyboard(context, is_super_admin), parse_mode="HTML")
                return
            toggle_match = re.fullmatch(r"main_menu:auth:del_toggle:(\d+)", data)
            if toggle_match:
                target_uid = int(toggle_match.group(1))
                selected = context.user_data.get("auth_delete_selected") or set()
                if not isinstance(selected, set):
                    selected = set(selected or [])
                if target_uid in selected:
                    selected.remove(target_uid)
                else:
                    selected.add(target_uid)
                context.user_data["auth_delete_selected"] = selected
                await query.answer("已更新选择")
                delete_hint = "请选择要删除授权的用户。\n超级管理员不可通过 Bot 删除。" if is_super_admin else "请选择要删除授权的普通用户。\n普通管理员不可删除管理员。"
                await show_callback_page(query, "🔓 <b>删除授权</b>\n────────────\n" + delete_hint, authorization_delete_keyboard(context, is_super_admin), parse_mode="HTML")
                return
            if data == "main_menu:auth:del_done":
                selected = context.user_data.get("auth_delete_selected") or set()
                if not selected:
                    await query.answer("请先选择要删除的用户", show_alert=True)
                    return
                user_ids = sorted(int(uid) for uid in selected)
                lines = ["⚠️ <b>确认删除授权</b>", "────────────", "将删除以下授权用户："]
                for target_uid in user_ids:
                    emoji = "👑" if target_uid in cfg.telegram.manager_user_ids else "🎩"
                    lines.append(f"{emoji} {html.escape(await resolve_telegram_user_label(target_uid))} (<code>{target_uid}</code>)")
                await query.answer()
                await show_callback_page(query, "\n".join(lines), authorization_delete_confirm_keyboard(), parse_mode="HTML")
                return
            if data == "main_menu:auth:del_confirm":
                selected = context.user_data.get("auth_delete_selected") or set()
                user_ids = {int(uid) for uid in selected}
                if not user_ids:
                    await query.answer("请先选择要删除的用户", show_alert=True)
                    return
                if (user_ids & cfg.telegram.manager_user_ids) and not is_super_admin:
                    await query.answer("普通管理员不可删除管理员", show_alert=True)
                    return
                before_managers = sorted(cfg.telegram.manager_user_ids)
                before_users = sorted(cfg.telegram.authorized_user_ids)
                remove_manager_ids = user_ids & cfg.telegram.manager_user_ids
                remove_user_ids = user_ids & cfg.telegram.authorized_user_ids
                await asyncio.to_thread(update_telegram_roles_in_cache_sync, cache_path, cfg.telegram.admin_user_id, cfg.telegram.manager_user_ids, cfg.telegram.authorized_user_ids, remove_authorized_user_ids=remove_user_ids, remove_manager_user_ids=remove_manager_ids)
                after_managers = [uid for uid in before_managers if uid not in remove_manager_ids]
                after_users = [uid for uid in before_users if uid not in remove_user_ids]
                await asyncio.to_thread(log_operation_from_query, query, "auth", "删除授权", f"修改前管理员：{', '.join(str(uid) for uid in before_managers) or '空'}\n修改后管理员：{', '.join(str(uid) for uid in after_managers) or '空'}\n修改前普通用户：{', '.join(str(uid) for uid in before_users) or '空'}\n修改后普通用户：{', '.join(str(uid) for uid in after_users) or '空'}\n删除：{', '.join(str(uid) for uid in sorted(user_ids))}")
                context.user_data.pop("auth_delete_selected", None)
                await query.answer("授权已删除")
                await show_callback_page(query, "✅ 已删除所选授权用户。\n变更已保存。", authorization_manage_keyboard(is_super_admin), parse_mode="HTML")
                return

        if data == "main_menu:clear_history":
            await query.answer()
            await show_callback_page(
                query,
                "👋🏻 <b>清除对话记录</b>\n────────────\n将尝试清空当前对话记录。\n此操作不可恢复。\n\n⚠️ 确认要继续吗？",
                clear_history_confirm_keyboard(),
                parse_mode="HTML",
                auto_delete=False,
            )
            return

        if data == "main_menu:clear_history_confirm":
            chat_id = query.message.chat_id
            message_id = query.message.message_id
            await query.answer("正在清空历史记录...")
            log.info("开始清空 Telegram 历史记录：chat=%s from_message_id=%s", chat_id, message_id)
            deleted, failed = await purge_chat_history(chat_id, message_id)
            log.info("清空 Telegram 历史记录完成：chat=%s deleted=%s failed=%s", chat_id, deleted, failed)
            return

        if data in {"main_menu:system_check", "main_menu:system_check_refresh"}:
            is_refresh = data.endswith("_refresh")
            if not is_refresh:
                await query.answer("正在执行健康检查，请稍候...")
            text = await asyncio.to_thread(bot_health_overview_text_sync, cfg, cache_path, is_admin_user_id(query.from_user.id, cfg))
            if len(text) <= 3900:
                await show_callback_page(query, text, health_check_keyboard(), parse_mode="HTML")
            else:
                await show_callback_page(
                    query,
                    "🩺 <b>健康检查</b>\n────────────\n结果较长，已完整分段发送在下方。",
                    health_check_keyboard(),
                    parse_mode="HTML",
                )
                await reply_long_text(query.message, text, parse_mode="HTML", reply_markup=health_check_keyboard())
            if is_refresh:
                await query.answer("刷新成功")
            return

        if data in {"main_menu:notifications", "main_menu:status_notice"}:
            await query.answer()
            chat_id = str(query.message.chat_id)
            await show_callback_page(
                query,
                "💬 <b>通知推送</b>\n────────────\n流量报表生成时间：北京时间 00:00\n版本更新检查时间：北京时间 12:00\n\n",
                notification_push_keyboard(chat_id, is_admin_user_id(query.from_user.id, cfg)),
                parse_mode="HTML",
            )
            return

        notification_match = re.fullmatch(r"main_menu:notifications:(daily|weekly|monthly|collector|traffic_alert|ip_alert|version_update)", data)
        if notification_match:
            kind = notification_match.group(1)
            if kind == "version_update" and not is_admin_user_id(query.from_user.id, cfg):
                await query.answer("只有管理员可以设置版本更新推送", show_alert=True)
                return
            chat_id = str(query.message.chat_id)
            result = await asyncio.to_thread(notification_toggle_sync, cache_path, chat_id, kind)
            label = NOTIFICATION_KINDS[kind]
            if kind == "ip_alert":
                await query.answer(f"异地登录已切换为{notification_ip_alert_mode_label(str(result))}通知")
            else:
                await query.answer(f"{label}已{'开启' if result else '关闭'}推送")
            await show_callback_page(
                query,
                "💬 <b>通知推送</b>\n────────────\n流量报表生成时间：北京时间 00:00\n版本更新检查时间：北京时间 12:00\n\n",
                notification_push_keyboard(chat_id, is_admin_user_id(query.from_user.id, cfg)),
                parse_mode="HTML",
            )
            return

        if data == "main_menu:traffic_management":
            await query.answer()
            await show_callback_page(
                query,
                "🌊 <b>流量统计</b>\n────────────\n请选择功能。",
                traffic_management_keyboard(),
                parse_mode="HTML",
            )
            return

        if data == "main_menu:traffic_users":
            await query.answer("正在统计用户用量，请稍候...")
            await send_or_jump_traffic_dashboard(query.message, "users_preset_24h")
            return

        if data == "main_menu:traffic_nodes":
            await query.answer("正在统计节点用量，请稍候...")
            await send_or_jump_traffic_dashboard(query.message, "nodes_preset_24h")
            return

        if data == "main_menu:ip_monitor":
            await query.answer()
            await show_callback_page(
                query,
                "🌐 <b>IP 监控</b>\n────────────\n请选择功能。\n\n" + PROXY_PROTOCOL_NOTICE,
                ip_monitor_keyboard(),
                parse_mode="HTML",
            )
            return

        if data == "main_menu:ip_monitor:period":
            await query.answer("正在生成查询...")
            await send_dashboard_card(query.message, "ip_1h", query.from_user.id)
            return

        if data == "main_menu:ip_monitor:ignore":
            await query.answer()
            await show_callback_page(
                query,
                "🚧 <b>忽略列表</b>\n────────────\n请选择维度。\n\nIPv4 段按 /24 统计；IPv6 暂不参与统计。",
                ip_ignore_menu_keyboard(),
                parse_mode="HTML",
            )
            return

        ignored_rules_match = re.fullmatch(r"main_menu:ip_monitor:ignored_rules:(\d+)", data)
        if ignored_rules_match:
            page = int(ignored_rules_match.group(1))
            await query.answer()
            await show_callback_page(
                query,
                ignored_rules_text_sync(cache_path),
                ignored_rules_keyboard(context, page),
                parse_mode="HTML",
            )
            return

        ignored_rule_toggle_match = re.fullmatch(r"main_menu:ip_monitor:ignored_rule_toggle:(\d+):([A-Za-z0-9]+)", data)
        if ignored_rule_toggle_match:
            page = int(ignored_rule_toggle_match.group(1))
            token = ignored_rule_toggle_match.group(2)
            token_map = context.user_data.get("ip_ignore_tokens") or {}
            token_data = token_map.get(token) if isinstance(token_map, dict) else None
            if not token_data:
                await query.answer("请求无效，请重新进入。", show_alert=True)
                return
            dimension = str(token_data.get("dimension") or "")
            value = str(token_data.get("value") or "")
            if dimension not in {"area", "asn", "cidr"} or not value:
                await query.answer("请求无效，请重新进入。", show_alert=True)
                return
            before_values = await asyncio.to_thread(ignored_rule_values_sync, cache_path, dimension)
            await asyncio.to_thread(ignored_rule_toggle_sync, cache_path, dimension, value)
            after_values = await asyncio.to_thread(ignored_rule_values_sync, cache_path, dimension)
            await asyncio.to_thread(log_operation_from_query, query, "ip_ignore", "解除忽略", f"维度：{dimension}\n对象：{value}\n修改前：{'已忽略' if value in before_values else '未忽略'}\n修改后：{'已忽略' if value in after_values else '未忽略'}")
            await query.answer("已解除忽略")
            await show_callback_page(
                query,
                ignored_rules_text_sync(cache_path),
                ignored_rules_keyboard(context, page),
                parse_mode="HTML",
            )
            return

        ignore_page_match = re.fullmatch(r"main_menu:ip_monitor:ignore:(area|asn|cidr):(\d+)", data)
        if ignore_page_match:
            dimension = ignore_page_match.group(1)
            page = int(ignore_page_match.group(2))
            title = {"area": "忽略地区", "asn": "忽略 ASN", "cidr": "忽略 IP"}[dimension]
            await query.answer()
            await show_callback_page(
                query,
                f"🚧 <b>{title}</b>\n────────────\n按已采集信息去重展示，并按最近出现时间排序。\n点击按钮可切换忽略状态；前缀 ✅ 表示已忽略。",
                ip_ignore_list_keyboard(context, dimension, page),
                parse_mode="HTML",
            )
            return

        ignore_toggle_match = re.fullmatch(r"main_menu:ip_monitor:ignore_toggle:(area|asn|cidr):(\d+):([A-Za-z0-9]+)", data)
        if ignore_toggle_match:
            dimension = ignore_toggle_match.group(1)
            page = int(ignore_toggle_match.group(2))
            token = ignore_toggle_match.group(3)
            token_map = context.user_data.get("ip_ignore_tokens") or {}
            token_data = token_map.get(token) if isinstance(token_map, dict) else None
            if not token_data or token_data.get("dimension") != dimension:
                await query.answer("请求无效，请重新进入。", show_alert=True)
                return
            value = str(token_data.get("value") or "")
            before_values = await asyncio.to_thread(ignored_rule_values_sync, cache_path, dimension)
            enabled = await asyncio.to_thread(ignored_rule_toggle_sync, cache_path, dimension, value)
            after_values = await asyncio.to_thread(ignored_rule_values_sync, cache_path, dimension)
            await asyncio.to_thread(log_operation_from_query, query, "ip_ignore", "切换忽略", f"维度：{dimension}\n对象：{value}\n修改前：{'已忽略' if value in before_values else '未忽略'}\n修改后：{'已忽略' if value in after_values else '未忽略'}")
            title = {"area": "忽略地区", "asn": "忽略 ASN", "cidr": "忽略 IP"}[dimension]
            await query.answer("已加入忽略" if enabled else "已取消忽略")
            await show_callback_page(
                query,
                f"🚧 <b>{title}</b>\n────────────\n按已采集信息去重展示，并按最近出现时间排序。\n点击按钮可切换忽略状态；前缀 ✅ 表示已忽略。",
                ip_ignore_list_keyboard(context, dimension, page),
                parse_mode="HTML",
            )
            return

        if data == "main_menu:noop":
            await query.answer()
            return

        if data == "main_menu:ip_monitor:user_query":
            context.user_data["awaiting_user_ip_query_id"] = True
            context.user_data.pop("user_ip_query_period", None)
            await query.answer()
            await show_callback_page(
                query,
                "🔎 <b>按用户 ID 查询 IP</b>\n────────────\n请输入要查询的用户 ID，例如：1",
                InlineKeyboardMarkup([back_close_row("main_menu:ip_monitor", "⬅️ 返回 IP 监控")]),
                parse_mode="HTML",
            )
            return

        if data == "main_menu:parameter_config":
            await query.answer()
            await show_callback_page(query, "🎨 参数配置\n────────────\n请选择要配置的面板参数。", parameter_config_keyboard())
            return

        if data == "main_menu:parameter_config:cache_retention":
            await query.answer()
            await show_callback_page(query, cache_retention_text_sync(), cache_retention_keyboard(), parse_mode="HTML")
            return

        retention_select_match = re.fullmatch(r"main_menu:parameter_config:cache_retention_select:(1m|1q|1y|all)", data)
        if retention_select_match:
            option_key = retention_select_match.group(1)
            days, _ = CACHE_RETENTION_OPTIONS[option_key]
            preview = await asyncio.to_thread(cache_retention_preview_sync, cache_path, days)
            await query.answer()
            await show_callback_page(query, cache_retention_preview_text(option_key, preview), cache_retention_confirm_keyboard(option_key), parse_mode="HTML")
            return

        retention_confirm_match = re.fullmatch(r"main_menu:parameter_config:cache_retention_confirm:(1m|1q|1y|all)", data)
        if retention_confirm_match:
            option_key = retention_confirm_match.group(1)
            days, label = CACHE_RETENTION_OPTIONS[option_key]
            stats = await asyncio.to_thread(cache_retention_set_and_prune_sync, cache_path, days)
            await asyncio.to_thread(
                log_operation_from_query,
                query,
                "parameter_config",
                "调整缓存保留时间",
                f"设置：{label}\n活跃 IP 记录：{stats['active_ip_records']} 条\nIP 归属地缓存：{stats['ip_geo_cache']} 条\n流量分钟样本：{stats['traffic_delta_samples']} 条",
            )
            await query.answer("缓存保留时间已更新")
            await show_callback_page(
                query,
                "✅ <b>缓存保留时间已更新</b>\n"
                "────────────\n"
                f"当前设置：<b>{html.escape(label)}</b>\n\n"
                "本次已清理：\n"
                f"• 活跃 IP 记录：<b>{int(stats.get('active_ip_records') or 0)}</b> 条\n"
                f"• IP 归属地缓存：<b>{int(stats.get('ip_geo_cache') or 0)}</b> 条\n"
                f"• 流量分钟样本：<b>{int(stats.get('traffic_delta_samples') or 0)}</b> 条\n"
                f"• 采样中断记录：<b>{int(stats.get('traffic_sample_gaps') or 0)}</b> 条\n"
                f"• 自定义范围：<b>{int(stats.get('traffic_ranges') or 0)}</b> 条",
                cache_retention_keyboard(days),
                parse_mode="HTML",
            )
            return

        if data == "main_menu:debug_tools":
            await query.answer()
            await show_callback_page(query, "🧪 调试功能\n────────────\n请选择要执行的调试操作。", debug_tools_keyboard(is_admin_user_id(query.from_user.id, cfg)))
            return

        if data.startswith("main_menu:debug:reset_cache"):
            if not is_admin_user_id(query.from_user.id, cfg):
                await query.answer("只有管理员可以使用重置缓存", show_alert=True)
                return

        if data == "main_menu:debug:reset_cache":
            context.user_data.pop("debug_reset_cache_mode", None)
            await query.answer()
            await show_callback_page(
                query,
                "🧹 重置缓存\n\n这是高风险操作，会清空本地缓存与采样数据。\n不会修改 XBoard / MySQL，只影响 Bot 本地 SQLite。\n\n请选择重置方式：",
                reset_cache_keyboard(),
            )
            return

        if data == "main_menu:debug:reset_cache_now":
            context.user_data["debug_reset_cache_mode"] = "now"
            await query.answer()
            await show_callback_page(
                query,
                "⚠️ 全部重置确认\n\n将清空：\n- 本地缓存\n- 采样数据\n- 活跃 IP 记录\n- 流量统计范围\n- 置顶仪表盘消息\n\n保留：\n- 自定题图\n- 自定昵称\n\n确认后将重新进入健康检查页，请在页面里观察重新采集情况。",
                reset_cache_confirm_keyboard(),
            )
            return

        if data == "main_menu:debug:reset_cache_now_confirm":
            mode = context.user_data.pop("debug_reset_cache_mode", None)
            if mode != "now":
                await query.answer("请先选择重置方式", show_alert=True)
                return
            stats = await asyncio.to_thread(reset_local_cache_sync, cache_path)
            await asyncio.to_thread(log_operation_from_query, query, "reset_cache", "全部重置缓存", f"流量样本 {stats['traffic_delta_samples']} 条，活跃 IP {stats['active_ip_records']} 条")
            await query.answer("缓存已重置")
            await show_callback_page(
                query,
                "✅ 全部重置完成\n\n"
                f"已清空活跃 IP 记录：{stats['active_ip_records']} 条\n"
                f"已清空 IP 归属地缓存：{stats['ip_geo_cache']} 条\n"
                f"已清空用户信息缓存：{stats['users']} 个\n"
                f"已清空流量样本：{stats['traffic_delta_samples']} 条\n"
                f"已清空采样中断记录：{stats['traffic_sample_gaps']} 条\n"
                f"已清空自定义范围：{stats['traffic_ranges']} 条\n"
                f"已清空置顶消息记录：{stats['pinned_dashboard_messages']} 条\n\n"
                "现在请进入健康检查页查看重新采集与补全进度。",
                InlineKeyboardMarkup([[InlineKeyboardButton("🩺 前往健康检查", callback_data="main_menu:system_check")], [InlineKeyboardButton("⬅️ 返回主菜单", callback_data="main_menu")]]),
            )
            return

        if data == "main_menu:debug:reset_cache_floor":
            state = traffic_custom_state(context)
            state.clear()
            state.update({"mode": "floor", "phase": "floor", "step": "year", "debug": True})
            await query.answer()
            await show_callback_page(
                query,
                traffic_custom_prompt_text(state),
                traffic_custom_year_keyboard(str(state.get("mode") or "")),
            )
            return

        if data == "main_menu:debug:reset_user_ip":
            context.user_data["reset_user_ip_selected"] = set()
            await asyncio.to_thread(upsert_all_cache_users, cache_path, cfg.mysql)
            users = await asyncio.to_thread(list_all_cached_user_buttons_sync, cache_path)
            await query.answer()
            await show_callback_page(
                query,
                "👤 重置特定用户 IP 记录\n\n请选择要清理 IP 记录的用户；可多选。\n该操作会把所选用户相关的本地 IP 记录标记为忽略，不会修改 XBoard / MySQL。",
                reset_user_ip_select_keyboard(users, set(), 0),
            )
            return

        reset_user_ip_page_match = re.fullmatch(r"main_menu:debug:reset_user_ip_page:(\d+)", data)
        if reset_user_ip_page_match:
            page = int(reset_user_ip_page_match.group(1))
            await asyncio.to_thread(upsert_all_cache_users, cache_path, cfg.mysql)
            users = await asyncio.to_thread(list_all_cached_user_buttons_sync, cache_path)
            selected = context.user_data.setdefault("reset_user_ip_selected", set())
            if not isinstance(selected, set):
                selected = set(selected or [])
                context.user_data["reset_user_ip_selected"] = selected
            await query.answer()
            await show_callback_page(
                query,
                "👤 重置特定用户 IP 记录\n\n请选择要清理 IP 记录的用户；可多选。\n该操作会把所选用户相关的本地 IP 记录标记为忽略，不会修改 XBoard / MySQL。",
                reset_user_ip_select_keyboard(users, selected, page),
            )
            return

        reset_user_ip_toggle_match = re.fullmatch(r"main_menu:debug:reset_user_ip_toggle:(\d+):(\d+)", data)
        if reset_user_ip_toggle_match:
            page = int(reset_user_ip_toggle_match.group(1))
            xboard_user_id = int(reset_user_ip_toggle_match.group(2))
            selected = context.user_data.setdefault("reset_user_ip_selected", set())
            if not isinstance(selected, set):
                selected = set(selected or [])
                context.user_data["reset_user_ip_selected"] = selected
            if xboard_user_id in selected:
                selected.remove(xboard_user_id)
            else:
                selected.add(xboard_user_id)
            await asyncio.to_thread(upsert_all_cache_users, cache_path, cfg.mysql)
            users = await asyncio.to_thread(list_all_cached_user_buttons_sync, cache_path)
            await query.answer(f"已选择 {len(selected)} 个用户")
            await show_callback_page(
                query,
                "👤 重置特定用户 IP 记录\n\n请选择要清理 IP 记录的用户；可多选。\n该操作会把所选用户相关的本地 IP 记录标记为忽略，不会修改 XBoard / MySQL。",
                reset_user_ip_select_keyboard(users, selected, page),
            )
            return

        if data == "main_menu:debug:reset_user_ip_done":
            selected = context.user_data.get("reset_user_ip_selected") or set()
            if not isinstance(selected, set):
                selected = set(selected or [])
            if not selected:
                await query.answer("请至少选择一个用户", show_alert=True)
                return
            preview = await asyncio.to_thread(preview_clear_user_ip_records_multi_sync, cache_path, list(selected))
            label_lines = "\n".join(f"• {html.escape(label)}" for label in preview.get("labels", [])[:20])
            if len(preview.get("labels", [])) > 20:
                label_lines += f"\n… 另 {len(preview['labels']) - 20} 个用户"
            await query.answer()
            await show_callback_page(
                query,
                "⚠️ 请再次确认是否忽略所选用户的 IP 记录。\n\n"
                f"选择用户：{preview['users']} 个\n"
                f"IP 记录：{preview['records']} 条\n"
                f"涉及 IP：{preview['ips']} 个\n"
                f"最早记录：{format_timestamp(preview['first_seen']) if preview['first_seen'] else '未知'}\n"
                f"最新记录：{format_timestamp(preview['last_seen']) if preview['last_seen'] else '未知'}\n\n"
                f"{label_lines}",
                reset_user_ip_multi_confirm_keyboard(list(selected)),
                parse_mode="HTML",
            )
            return

        if data == "main_menu:debug:reset_user_ip_multi_confirm":
            selected = context.user_data.get("reset_user_ip_selected") or set()
            if not isinstance(selected, set):
                selected = set(selected or [])
            user_ids = sorted(int(x) for x in selected if int(x) > 0)
            if not user_ids:
                await query.answer("选择状态已过期，请重新选择用户", show_alert=True)
                return
            stats = await asyncio.to_thread(clear_user_ip_records_multi_sync, cache_path, user_ids)
            await asyncio.to_thread(log_operation_from_query, query, "reset_ip", "重置特定用户 IP 记录", f"用户 {', '.join(str(uid) for uid in user_ids)}；记录 {stats['records']} 条")
            context.user_data.pop("reset_user_ip_selected", None)
            await query.answer("已标记忽略所选用户 IP 记录")
            await show_callback_page(
                query,
                "✅ 所选用户 IP 记录已标记忽略\n\n"
                f"用户数：{stats['users']} 个\n"
                f"已清理记录：{stats['records']} 条\n"
                f"涉及 IP：{stats['ips']} 个\n"
                f"已标记忽略记录：{stats.get('ignored', 0)} 条\n"
                f"剩余计入统计 IP 记录：{stats.get('remaining_active_ips', 0)} 条\n\n"
                "本次调试清理不会触发异地登录恢复通知；被标记忽略的记录仍会正常采集更新，但不会计入统计、详情和告警。\n\n"
                "你可以继续在调试功能中查看其它项。",
                debug_tools_keyboard(is_admin_user_id(query.from_user.id, cfg)),
            )
            return

        if data == "main_menu:parameter_config:cover":
            context.user_data["awaiting_custom_cover"] = True
            context.user_data.pop("awaiting_custom_nickname", None)
            await query.answer()
            await show_callback_page(query, "🖼 自定题图\n\n请直接发送一张图片。\n收到后，我会把它设为你打开 /start 时显示的题图。", cover_config_keyboard())
            return

        if data == "main_menu:parameter_config:cover_reset":
            context.user_data.pop("awaiting_custom_cover", None)
            await asyncio.to_thread(ui_pref_delete_sync, cache_path, query.from_user.id, "cover_file_id")
            await query.answer("已重置为 Bot 头像", show_alert=True)
            await show_callback_page(query, "🖼 自定题图\n\n已重置：之后 /start 会继续使用 Bot 头像。", parameter_config_keyboard())
            return

        if data == "main_menu:parameter_config:nickname":
            context.user_data["awaiting_custom_nickname"] = True
            context.user_data.pop("awaiting_custom_cover", None)
            await query.answer()
            await show_callback_page(query, "🏷 自定昵称\n\n请发送要显示在 /start 欢迎语里的昵称。", nickname_config_keyboard())
            return

        if data == "main_menu:parameter_config:nickname_reset":
            context.user_data.pop("awaiting_custom_nickname", None)
            await asyncio.to_thread(ui_pref_delete_sync, cache_path, query.from_user.id, "nickname")
            await query.answer("已重置为 Telegram 名称", show_alert=True)
            await show_callback_page(query, "🏷 自定昵称\n\n已重置：之后 /start 会继续使用你的 Telegram 名称。", parameter_config_keyboard())
            return

        if data in sections:
            await query.answer()
            await show_callback_page(query, f"{sections[data]}\n\n此功能入口已预留，等待下一步配置。", empty_section_keyboard())
            return

        await query.answer("该入口暂未开放", show_alert=True)


    async def active_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_message:
            return
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                return
            await reply_connection_status(update, cfg)
            return
        sent = await update.effective_message.reply_text("🌐 请选择在线记录统计周期：", reply_markup=active_users_keyboard())
        await track_auto_delete_message(sent)

    async def active_users_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.message:
            return
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                return
            await query.answer("未授权", show_alert=True)
            return

        periods = {
            "1h": ("近 1 小时", timedelta(hours=1)),
            "24h": ("近 24 小时", timedelta(hours=24)),
            "7d": ("近 7 天", timedelta(days=7)),
            "30d": ("近 30 天", timedelta(days=30)),
        }
        data = query.data or ""

        scoped_query_match = re.fullmatch(r"ip_user_query:(?:(1h|24h|7d|30d)|custom:(\d+):(\d+))", data)
        if scoped_query_match:
            period_key = scoped_query_match.group(1)
            context.user_data["awaiting_user_ip_query_id"] = True
            if period_key:
                context.user_data["user_ip_query_period"] = period_key
            else:
                start_ts = int(scoped_query_match.group(2))
                end_ts = int(scoped_query_match.group(3))
                context.user_data["user_ip_query_period"] = f"custom:{start_ts}:{end_ts}"
            await query.answer()
            await show_callback_page(
                query,
                "🔎 <b>按用户 ID 查询 IP</b>\n────────────\n请输入要查询的用户 ID，例如：1",
                InlineKeyboardMarkup([back_close_row("main_menu:ip_monitor", "⬅️ 返回 IP 监控")]),
                parse_mode="HTML",
            )
            return

        query_match = re.fullmatch(r"active_users_query:(1h|24h|7d|30d)(?::(\d+))?", data)
        if query_match:
            period_key = query_match.group(1)
            page = int(query_match.group(2) or 0)
            label, window = periods[period_key]
            await query.answer("正在生成用户按钮...")
            result, user_buttons = await asyncio.gather(
                asyncio.to_thread(list_user_ips_from_cache_sync, cache_path, label, window),
                asyncio.to_thread(active_user_button_items_from_cache_sync, cache_path, window),
            )
            await show_callback_page(
                query,
                result,
                active_users_keyboard(period_key, user_buttons, page),
                parse_mode="HTML",
            )
            return

        page_match = re.fullmatch(r"user_ip_page:(\d+):(\d+):(.+)", data)
        if page_match:
            xboard_user_id = int(page_match.group(1))
            page = int(page_match.group(2))
            period_spec = page_match.group(3)
            period_key = None if period_spec == "all" else period_spec
            label = window = start_ts = end_ts = None
            if period_key in periods:
                label, window = periods[period_key]
            elif period_key and period_key.startswith("custom:"):
                _, start_text, end_text = period_key.split(":", 2)
                start_ts = int(start_text)
                end_ts = int(end_text)
                label = "自定区间"
            await query.answer("正在翻页...")
            result = await asyncio.to_thread(query_user_ips_from_cache_sync, cache_path, xboard_user_id, label, window, start_ts, end_ts, page, 10)
            total_ips = await asyncio.to_thread(count_user_ips_from_cache_sync, cache_path, xboard_user_id, window, start_ts, end_ts)
            await show_callback_page(query, result, user_ip_query_page_keyboard(period_key, xboard_user_id, total_ips, page), parse_mode="HTML")
            return

        cancel_match = re.fullmatch(r"active_users_cancel:(1h|24h|7d|30d)", data)
        if cancel_match:
            period_key = cancel_match.group(1)
            label, window = periods[period_key]
            await query.answer("已取消")
            result = await asyncio.to_thread(list_user_ips_from_cache_sync, cache_path, label, window)
            await show_callback_page(query, result, active_users_keyboard(period_key), parse_mode="HTML")
            return

        if data == "noop":
            await query.answer()
            return

        detail_match = re.fullmatch(r"active_user_detail:(1h|24h|7d|30d):(\d+)", data)
        if detail_match:
            period_key = detail_match.group(1)
            xboard_user_id = int(detail_match.group(2))
            label, window = periods[period_key]
            await query.answer("正在查询 IP...")
            result = await asyncio.to_thread(
                query_user_ips_from_cache_sync,
                cache_path,
                xboard_user_id,
                label,
                window,
            )
            await show_callback_page(query, result, detail_keyboard(period_key), parse_mode="HTML")
            return

        key = data.split(":", 1)[-1]
        if key not in periods:
            await query.answer("请求无效，请重新进入。", show_alert=True)
            return

        await query.answer("正在生成查询...")
        await edit_dashboard_card(query, f"ip_{key}")

    async def ip_detail_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.message:
            return
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                return
            await query.answer("未授权", show_alert=True)
            return
        data = query.data or ""

        list_match = re.fullmatch(r"ip_detail_list:(ip_(?:1h|24h|7d|30d)|iprange_\d+_\d+):(\d+)", data)
        if list_match:
            kind = list_match.group(1)
            page = int(list_match.group(2))
            parsed = parse_ip_kind(kind)
            if not parsed:
                await query.answer("请求无效，请重新进入。", show_alert=True)
                return
            label, start_ts, end_ts = parsed
            await query.answer("正在生成用户列表...")
            user_buttons = await asyncio.to_thread(active_user_button_items_from_cache_sync, cache_path, None, start_ts, end_ts)
            if label == "自定区间":
                text = "\n".join([
                    "🔍 <b>自定区间用户详情</b>",
                    f"时间区间：{datetime.fromtimestamp(start_ts).strftime('%Y-%m-%d %H:%M')} - {datetime.fromtimestamp(end_ts).strftime('%Y-%m-%d %H:%M')}",
                    "────────────",
                    "请选择要查看的用户。",
                    "",
                    PROXY_PROTOCOL_NOTICE,
                ])
            else:
                text = "\n".join([
                    f"🔍 <b>{label}用户活跃详情</b>",
                    "────────────",
                    "请选择要查看的用户。",
                    "",
                    PROXY_PROTOCOL_NOTICE,
                ])
            if not user_buttons:
                text += "\n\n暂无可查看用户。"
            await show_callback_page(query, text, ip_detail_list_keyboard(kind, user_buttons, page), parse_mode="HTML")
            return

        notice_match = re.fullmatch(r"ip_alert_notice:(\d+)", data)
        if notice_match:
            xboard_user_id = int(notice_match.group(1))
            row = await asyncio.to_thread(ip_alert_row_for_user_sync, cache_path, xboard_user_id)
            mark_no_auto_delete_message(query.message)
            await query.answer()
            if row:
                await show_callback_page(query, format_ip_alert(row), ip_alert_keyboard(row), parse_mode="HTML", auto_delete=False)
            else:
                await show_callback_page(
                    query,
                    "✅ <b>异地登录恢复</b>\n────────────\n当前用户已不再满足异地登录告警条件。",
                    InlineKeyboardMarkup([back_close_row("main_menu:ip_monitor", "⬅️ 返回 IP 监控")]),
                    parse_mode="HTML",
                    auto_delete=False,
                )
            return

        ignore_menu_match = re.fullmatch(r"ip_ignore_menu:(ip_(?:1h|24h|7d|30d)|iprange_\d+_\d+):(\d+):(\d+)(?::(alert))?", data)
        if ignore_menu_match:
            kind = ignore_menu_match.group(1)
            xboard_user_id = int(ignore_menu_match.group(2))
            detail_page = int(ignore_menu_match.group(3))
            source = ignore_menu_match.group(4)
            if source == "alert":
                mark_no_auto_delete_message(query.message)
            await query.answer()
            await show_callback_page(
                query,
                "🚧 <b>忽略当前列表</b>\n────────────\n请选择要从当前活跃 IP 列表中提取的忽略类型。",
                user_ip_ignore_dimension_keyboard(kind, xboard_user_id, detail_page, source),
                parse_mode="HTML",
                auto_delete=(source != "alert"),
            )
            return

        ignore_page_match = re.fullmatch(r"ip_ignore_page:(area|asn|cidr):(ip_(?:1h|24h|7d|30d)|iprange_\d+_\d+):(\d+):(\d+):(\d+)(?::(alert))?", data)
        if ignore_page_match:
            dimension = ignore_page_match.group(1)
            kind = ignore_page_match.group(2)
            xboard_user_id = int(ignore_page_match.group(3))
            detail_page = int(ignore_page_match.group(4))
            list_page = int(ignore_page_match.group(5))
            source = ignore_page_match.group(6)
            if source == "alert":
                mark_no_auto_delete_message(query.message)
            title = {"area": "忽略地区", "asn": "忽略 ASN", "cidr": "忽略 IP"}[dimension]
            await query.answer()
            await show_callback_page(
                query,
                f"🚧 <b>{title}</b>\n────────────\n已从当前页面的活跃 IP 中去重生成按钮。\n点击可切换忽略状态；前缀 ✅ 表示已忽略。",
                user_ip_ignore_list_keyboard(context, dimension, kind, xboard_user_id, detail_page, list_page, source),
                parse_mode="HTML",
                auto_delete=(source != "alert"),
            )
            return

        short_toggle_match = re.fullmatch(r"ip_ig_t:([A-Za-z0-9]+)", data)
        if short_toggle_match:
            route_token = short_toggle_match.group(1)
            token_map = context.user_data.get("ip_ignore_tokens") or {}
            route_data = token_map.get(route_token) if isinstance(token_map, dict) else None
            if not route_data:
                await query.answer("请求无效，请重新进入。", show_alert=True)
                return
            dimension = str(route_data.get("dimension") or "")
            kind = str(route_data.get("kind") or "")
            xboard_user_id = int(route_data.get("user_id") or 0)
            detail_page = int(route_data.get("detail_page") or 0)
            list_page = int(route_data.get("list_page") or 0)
            source = str(route_data.get("source") or "") or None
            if source == "alert":
                mark_no_auto_delete_message(query.message)
            if dimension not in {"area", "asn", "cidr"} or not parse_ip_kind(kind) or xboard_user_id <= 0:
                await query.answer("请求无效，请重新进入。", show_alert=True)
                return
            ignore_value = str(route_data.get("value") or "")
            before_values = await asyncio.to_thread(ignored_rule_values_sync, cache_path, dimension)
            enabled = await asyncio.to_thread(ignored_rule_toggle_sync, cache_path, dimension, ignore_value)
            after_values = await asyncio.to_thread(ignored_rule_values_sync, cache_path, dimension)
            await asyncio.to_thread(log_operation_from_query, query, "ip_ignore", "切换忽略", f"维度：{dimension}\n对象：{ignore_value}\nXBoard 用户：{xboard_user_id}\n修改前：{'已忽略' if ignore_value in before_values else '未忽略'}\n修改后：{'已忽略' if ignore_value in after_values else '未忽略'}")
            title = {"area": "忽略地区", "asn": "忽略 ASN", "cidr": "忽略 IP"}[dimension]
            await query.answer("已加入忽略" if enabled else "已取消忽略")
            await show_callback_page(
                query,
                f"🚧 <b>{title}</b>\n────────────\n已从当前页面的活跃 IP 中去重生成按钮。\n点击可切换忽略状态；前缀 ✅ 表示已忽略。",
                user_ip_ignore_list_keyboard(context, dimension, kind, xboard_user_id, detail_page, list_page, source),
                parse_mode="HTML",
                auto_delete=(source != "alert"),
            )
            return

        ignore_toggle_match = re.fullmatch(r"ip_ignore_toggle:(area|asn|cidr):(ip_(?:1h|24h|7d|30d)|iprange_\d+_\d+):(\d+):(\d+):(\d+):([A-Za-z0-9]+)(?::(alert))?", data)
        if ignore_toggle_match:
            dimension = ignore_toggle_match.group(1)
            kind = ignore_toggle_match.group(2)
            xboard_user_id = int(ignore_toggle_match.group(3))
            detail_page = int(ignore_toggle_match.group(4))
            list_page = int(ignore_toggle_match.group(5))
            token = ignore_toggle_match.group(6)
            source = ignore_toggle_match.group(7)
            if source == "alert":
                mark_no_auto_delete_message(query.message)
            token_map = context.user_data.get("ip_ignore_tokens") or {}
            token_data = token_map.get(token) if isinstance(token_map, dict) else None
            if not token_data or token_data.get("dimension") != dimension:
                await query.answer("请求无效，请重新进入。", show_alert=True)
                return
            ignore_value = str(token_data.get("value") or "")
            before_values = await asyncio.to_thread(ignored_rule_values_sync, cache_path, dimension)
            enabled = await asyncio.to_thread(ignored_rule_toggle_sync, cache_path, dimension, ignore_value)
            after_values = await asyncio.to_thread(ignored_rule_values_sync, cache_path, dimension)
            await asyncio.to_thread(log_operation_from_query, query, "ip_ignore", "切换忽略", f"维度：{dimension}\n对象：{ignore_value}\nXBoard 用户：{xboard_user_id}\n修改前：{'已忽略' if ignore_value in before_values else '未忽略'}\n修改后：{'已忽略' if ignore_value in after_values else '未忽略'}")
            title = {"area": "忽略地区", "asn": "忽略 ASN", "cidr": "忽略 IP"}[dimension]
            await query.answer("已加入忽略" if enabled else "已取消忽略")
            await show_callback_page(
                query,
                f"🚧 <b>{title}</b>\n────────────\n已从当前页面的活跃 IP 中去重生成按钮。\n点击可切换忽略状态；前缀 ✅ 表示已忽略。",
                user_ip_ignore_list_keyboard(context, dimension, kind, xboard_user_id, detail_page, list_page, source),
                parse_mode="HTML",
                auto_delete=(source != "alert"),
            )
            return

        detail_match = re.fullmatch(r"ip_active_user_detail:(ip_(?:1h|24h|7d|30d)|iprange_\d+_\d+):(\d+)(?::(\d+))?(?::(alert))?", data)
        if detail_match:
            kind = detail_match.group(1)
            xboard_user_id = int(detail_match.group(2))
            page = int(detail_match.group(3) or 0)
            source = detail_match.group(4)
            if source == "alert":
                mark_no_auto_delete_message(query.message)
            parsed = parse_ip_kind(kind)
            if not parsed:
                await query.answer("请求无效，请重新进入。", show_alert=True)
                return
            label, start_ts, end_ts = parsed
            await query.answer("正在查询 IP...")
            result = await asyncio.to_thread(
                query_user_ips_from_cache_sync,
                cache_path,
                xboard_user_id,
                label,
                None,
                start_ts,
                end_ts,
                page,
                10,
            )
            total_ips = await asyncio.to_thread(count_user_ips_from_cache_sync, cache_path, xboard_user_id, None, start_ts, end_ts)
            await show_callback_page(query, result, user_ip_detail_keyboard(kind, xboard_user_id, total_ips, page, source), parse_mode="HTML", auto_delete=(source != "alert"))
            return

        await query.answer("请求无效，请重新进入。", show_alert=True)


    async def alert_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.message:
            return
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                return
            await query.answer("未授权", show_alert=True)
            return
        data = query.data or ""

        menu_match = re.fullmatch(r"alert_menu:(traffic|ip)", data)
        if menu_match:
            alert_type = menu_match.group(1)
            text = await asyncio.to_thread(alert_summary_sync, cache_path, alert_type)
            await query.answer()
            await show_callback_page(query, text, alert_menu_keyboard(alert_type), parse_mode="HTML")
            return

        users_match = re.fullmatch(r"alert_users:(traffic|ip):(\d+)", data)
        if users_match:
            alert_type = users_match.group(1)
            page = int(users_match.group(2))
            await asyncio.to_thread(upsert_all_cache_users, cache_path, cfg.mysql)
            users = await asyncio.to_thread(alert_user_list_sync, cache_path, alert_type, 10000)
            title = "用量异常" if alert_type == "traffic" else "异地登录"
            if not users:
                await query.answer()
                await show_callback_page(
                    query,
                    f"🌟 {'异常告警' if alert_type == 'traffic' else '异地登录'}<b>独立规则</b>\n────────────\n当前本地缓存中还没有用户列表。请等待后台采集完成后再试。",
                    InlineKeyboardMarkup([back_close_row(f"alert_menu:{alert_type}", "⬅️ 返回")]),
                    parse_mode="HTML",
                )
                return
            total_pages = max(1, (len(users) + 9) // 10)
            page = min(max(0, page), total_pages - 1)
            await query.answer()
            await show_callback_page(
                query,
                f"🌟 {'异常告警' if alert_type == 'traffic' else '异地登录'}<b>独立规则</b>\n────────────\n请选择用户。",
                alert_user_list_keyboard(alert_type, users, page),
                parse_mode="HTML",
            )
            return

        user_match = re.fullmatch(r"alert_user:(traffic|ip):(\d+)(?::(alert))?", data)
        if user_match:
            alert_type = user_match.group(1)
            xboard_user_id = int(user_match.group(2))
            source = user_match.group(3)
            if source == "alert":
                mark_no_auto_delete_message(query.message)
            text = await asyncio.to_thread(alert_user_setting_text_sync, cache_path, alert_type, xboard_user_id)
            await query.answer()
            await show_callback_page(query, text, alert_user_setting_keyboard_for_source(alert_type, xboard_user_id, source), parse_mode="HTML", auto_delete=(source != "alert"))
            return


        period_page_match = re.fullmatch(r"alert_period_page:(traffic|ip):(\d+)", data)
        if period_page_match:
            alert_type = period_page_match.group(1)
            xboard_user_id = int(period_page_match.group(2))
            title = "流量告警周期" if alert_type == "traffic" else "异地告警周期"
            await query.answer()
            await show_callback_page(
                query,
                f"🕒 <b>{title}</b>\n────────────\n请选择该用户的告警统计周期。",
                alert_user_period_select_keyboard(alert_type, xboard_user_id),
                parse_mode="HTML",
            )
            return

        global_period_page_match = re.fullmatch(r"alert_global_period_page:(traffic|ip)", data)
        if global_period_page_match:
            alert_type = global_period_page_match.group(1)
            title = "用量异常默认周期" if alert_type == "traffic" else "异地登录默认周期"
            await query.answer()
            await show_callback_page(query, f"🕒 <b>{title}</b>\n────────────\n请选择默认告警统计周期。", alert_global_period_select_keyboard(alert_type), parse_mode="HTML")
            return

        global_match = re.fullmatch(r"alert_global:(traffic|ip)(?::custom)?", data)
        if global_match:
            alert_type = global_match.group(1)
            if data.endswith(":custom"):
                context.user_data["awaiting_alert_global_custom"] = {
                    "type": alert_type,
                    "chat_id": query.message.chat_id,
                    "message_id": query.message.message_id,
                }
                unit = "GB，例如：150" if alert_type == "traffic" else "城市数，例如：4"
                await query.answer()
                await show_callback_page(query, f"✍️ 请输入默认规则 ({unit})", InlineKeyboardMarkup([back_close_row(f"alert_global:{alert_type}", "⬅️ 返回")]))
                return
            text = await asyncio.to_thread(alert_global_setting_text_sync, cache_path, alert_type)
            await query.answer()
            await show_callback_page(query, text, alert_global_keyboard(alert_type), parse_mode="HTML")
            return

        global_period_match = re.fullmatch(r"alert_global:(traffic|ip):period:(1h|24h|7d|today|week)", data)
        if global_period_match:
            alert_type = global_period_match.group(1)
            period = global_period_match.group(2)
            before = f"{alert_period_label(await asyncio.to_thread(alert_global_period_sync, cache_path, alert_type))} / {format_bytes(await asyncio.to_thread(alert_global_threshold_sync, cache_path, alert_type)) if alert_type == 'traffic' else str(await asyncio.to_thread(alert_global_threshold_sync, cache_path, alert_type)) + ' 个城市'}"
            await asyncio.to_thread(alert_set_global_period_sync, cache_path, alert_type, period)
            after = f"{alert_period_label(period)} / {format_bytes(await asyncio.to_thread(alert_global_threshold_sync, cache_path, alert_type)) if alert_type == 'traffic' else str(await asyncio.to_thread(alert_global_threshold_sync, cache_path, alert_type)) + ' 个城市'}"
            await asyncio.to_thread(log_operation_from_query, query, alert_category(alert_type), "调整默认周期", alert_setting_before_after_detail(alert_type, "默认规则", before, after))
            text = await asyncio.to_thread(alert_global_setting_text_sync, cache_path, alert_type)
            await query.answer("默认周期已保存")
            await show_callback_page(query, text, alert_global_keyboard(alert_type), parse_mode="HTML")
            return

        user_period_match = re.fullmatch(r"alert_set:(traffic|ip):period:(1h|24h|7d|today|week):(\d+)", data)
        if user_period_match:
            alert_type = user_period_match.group(1)
            period = user_period_match.group(2)
            xboard_user_id = int(user_period_match.group(3))
            before_setting = await asyncio.to_thread(alert_user_setting_sync, cache_path, xboard_user_id)
            before = alert_setting_label(before_setting, alert_type, cache_path)
            if alert_type == "traffic":
                await asyncio.to_thread(alert_upsert_setting_sync, cache_path, xboard_user_id, traffic_period=period, traffic_whitelist=0)
            else:
                await asyncio.to_thread(alert_upsert_setting_sync, cache_path, xboard_user_id, ip_period=period, ip_whitelist=0)
            after_setting = await asyncio.to_thread(alert_user_setting_sync, cache_path, xboard_user_id)
            after = alert_setting_label(after_setting, alert_type, cache_path)
            await asyncio.to_thread(log_operation_from_query, query, alert_category(alert_type), "调整独立周期", alert_setting_before_after_detail(alert_type, "独立规则", before, after, xboard_user_id))
            text = await asyncio.to_thread(alert_user_setting_text_sync, cache_path, alert_type, xboard_user_id)
            await query.answer("周期已保存")
            await show_callback_page(query, text, alert_user_setting_keyboard(alert_type, xboard_user_id), parse_mode="HTML")
            return

        custom_match = re.fullmatch(r"alert_set:(traffic|ip):custom:(\d+)", data)
        if custom_match:
            alert_type = custom_match.group(1)
            xboard_user_id = int(custom_match.group(2))
            context.user_data["awaiting_alert_custom"] = {
                "type": alert_type,
                "user_id": xboard_user_id,
                "chat_id": query.message.chat_id,
                "message_id": query.message.message_id,
            }
            unit = "GB，例如：150" if alert_type == "traffic" else "城市数，例如：4"
            await query.answer()
            await show_callback_page(query, f"✍️ 请输入独立规则 ({unit})", InlineKeyboardMarkup([back_close_row(f"alert_user:{alert_type}:{xboard_user_id}", "⬅️ 返回")]))
            return

        threshold_match = re.fullmatch(r"alert_set:(traffic|ip):threshold:(\d+):(\d+)", data)
        if threshold_match:
            alert_type = threshold_match.group(1)
            xboard_user_id = int(threshold_match.group(2))
            value = int(threshold_match.group(3))
            before_setting = await asyncio.to_thread(alert_user_setting_sync, cache_path, xboard_user_id)
            before = alert_setting_label(before_setting, alert_type, cache_path)
            if alert_type == "traffic":
                await asyncio.to_thread(alert_upsert_setting_sync, cache_path, xboard_user_id, traffic_threshold_bytes=value * 1024 ** 3, traffic_whitelist=0)
            else:
                await asyncio.to_thread(alert_upsert_setting_sync, cache_path, xboard_user_id, ip_city_threshold=value, ip_whitelist=0)
            after_setting = await asyncio.to_thread(alert_user_setting_sync, cache_path, xboard_user_id)
            after = alert_setting_label(after_setting, alert_type, cache_path)
            await asyncio.to_thread(log_operation_from_query, query, alert_category(alert_type), "调整独立规则", alert_setting_before_after_detail(alert_type, "独立规则", before, after, xboard_user_id))
            text = await asyncio.to_thread(alert_user_setting_text_sync, cache_path, alert_type, xboard_user_id)
            await query.answer("规则已保存")
            await show_callback_page(query, text, alert_user_setting_keyboard(alert_type, xboard_user_id), parse_mode="HTML")
            return

        white_match = re.fullmatch(r"alert_set:(traffic|ip):whitelist:(\d+)", data)
        if white_match:
            alert_type = white_match.group(1)
            xboard_user_id = int(white_match.group(2))
            setting = await asyncio.to_thread(alert_user_setting_sync, cache_path, xboard_user_id)
            before = alert_setting_label(setting, alert_type, cache_path)
            if alert_type == "traffic":
                new_value = 0 if int(setting.get("traffic_whitelist") or 0) else 1
                await asyncio.to_thread(alert_upsert_setting_sync, cache_path, xboard_user_id, traffic_whitelist=new_value)
            else:
                new_value = 0 if int(setting.get("ip_whitelist") or 0) else 1
                await asyncio.to_thread(alert_upsert_setting_sync, cache_path, xboard_user_id, ip_whitelist=new_value)
            after_setting = await asyncio.to_thread(alert_user_setting_sync, cache_path, xboard_user_id)
            after = alert_setting_label(after_setting, alert_type, cache_path)
            await asyncio.to_thread(log_operation_from_query, query, alert_category(alert_type), "切换白名单", alert_setting_before_after_detail(alert_type, "白名单", before, after, xboard_user_id))
            text = await asyncio.to_thread(alert_user_setting_text_sync, cache_path, alert_type, xboard_user_id)
            await query.answer("白名单已更新")
            await show_callback_page(query, text, alert_user_setting_keyboard(alert_type, xboard_user_id), parse_mode="HTML")
            return

        reset_match = re.fullmatch(r"alert_set:(traffic|ip):reset:(\d+)", data)
        if reset_match:
            alert_type = reset_match.group(1)
            xboard_user_id = int(reset_match.group(2))
            before_setting = await asyncio.to_thread(alert_user_setting_sync, cache_path, xboard_user_id)
            before = alert_setting_label(before_setting, alert_type, cache_path)
            await asyncio.to_thread(alert_reset_setting_sync, cache_path, xboard_user_id, alert_type)
            after_setting = await asyncio.to_thread(alert_user_setting_sync, cache_path, xboard_user_id)
            after = alert_setting_label(after_setting, alert_type, cache_path)
            await asyncio.to_thread(log_operation_from_query, query, alert_category(alert_type), "恢复默认规则", alert_setting_before_after_detail(alert_type, "独立规则", before, after, xboard_user_id))
            text = await asyncio.to_thread(alert_user_setting_text_sync, cache_path, alert_type, xboard_user_id)
            await query.answer("已恢复默认")
            await show_callback_page(query, text, alert_user_setting_keyboard(alert_type, xboard_user_id), parse_mode="HTML")
            return

        await query.answer("请求无效，请重新进入。", show_alert=True)

    async def close_message_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.message:
            return
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                return
            await query.answer("未授权", show_alert=True)
            return
        await query.answer("已关闭")
        try:
            await query.message.delete()
        except BadRequest:
            pass

    async def detail_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.message:
            return
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                return
            await query.answer("未授权", show_alert=True)
            return
        periods = {
            "1h": ("近 1 小时", timedelta(hours=1)),
            "24h": ("近 24 小时", timedelta(hours=24)),
            "7d": ("近 7 天", timedelta(days=7)),
            "30d": ("近 30 天", timedelta(days=30)),
        }
        target = (query.data or "").split(":", 1)[-1]
        await query.answer("返回")
        if target in periods:
            label, window = periods[target]
            result = await asyncio.to_thread(list_user_ips_from_cache_sync, cache_path, label, window)
            await show_callback_page(query, result, active_users_keyboard(target), parse_mode="HTML")
            return
        await show_callback_page(query, "🌐 请选择在线记录统计周期：", active_users_keyboard())

    async def user_ip_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_message:
            return
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                return
            await reply_connection_status(update, cfg)
            return
        context.user_data["awaiting_user_ip_query_id"] = True
        context.user_data.pop("user_ip_query_period", None)
        sent = await update.effective_message.reply_text("🔎 请输入要查询的用户 ID，例如：1")
        await track_auto_delete_message(sent)

    async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_message:
            return

        async def reply_and_track(text: str, **kwargs: Any) -> None:
            sent = await update.effective_message.reply_text(text, **kwargs)
            await track_auto_delete_message(sent)

        if context.user_data.get("awaiting_auth_add_user_id"):
            if not is_admin_user_id(user_id(update), cfg):
                context.user_data.pop("awaiting_auth_add_user_id", None)
                await reply_connection_status(update, cfg)
                return
            text = (update.effective_message.text or "").strip()
            if not re.fullmatch(r"\d+", text):
                await reply_and_track("Telegram 用户 ID 必须是纯数字，请重新输入；或发送 /start 取消。")
                return
            target_uid = int(text)
            try:
                if target_uid in cfg.telegram.admin_user_ids:
                    await reply_and_track("该用户已是管理员，无需重复授权。", reply_markup=authorization_manage_keyboard(is_super_admin_user_id(user_id(update), cfg)))
                    return
                label = await resolve_telegram_user_label(target_uid)
                before_users = sorted(cfg.telegram.authorized_user_ids)
                await asyncio.to_thread(update_authorized_users_in_cache_sync, cache_path, cfg.telegram.admin_user_id, cfg.telegram.manager_user_ids, cfg.telegram.authorized_user_ids, target_uid, None)
                after_users = sorted(set(before_users) | {target_uid})
                await asyncio.to_thread(log_operation_from_update, update, "auth", "增加授权", f"修改前：{', '.join(str(uid) for uid in before_users) or '空'}\n修改后：{', '.join(str(uid) for uid in after_users) or '空'}\n新增：{target_uid}")
            except ValueError as exc:
                await reply_and_track(str(exc), reply_markup=authorization_manage_keyboard(is_super_admin_user_id(user_id(update), cfg)))
                return
            except Exception as exc:
                log.exception("写入授权用户失败：%s", exc)
                await reply_and_track("写入授权失败，请检查运行状态。", reply_markup=authorization_manage_keyboard(is_super_admin_user_id(user_id(update), cfg)))
                return
            context.user_data.pop("awaiting_auth_add_user_id", None)
            await reply_and_track(
                f"✅ 已增加授权：{html.escape(label)} (<code>{target_uid}</code>)\n变更已保存。",
                parse_mode="HTML",
                reply_markup=authorization_manage_keyboard(is_super_admin_user_id(user_id(update), cfg)),
            )
            return

        if context.user_data.get("awaiting_custom_cover"):
            if not is_allowed(update, cfg):
                if is_bot_self_update(update, cfg):
                    return
                context.user_data.pop("awaiting_custom_cover", None)
                await reply_connection_status(update, cfg)
                return
            photos = update.effective_message.photo or []
            if not photos:
                await reply_and_track(
                    "请发送一张图片作为题图；或点击 /start 返回主菜单。",
                    reply_markup=cover_config_keyboard(),
                )
                return
            uid = user_id(update)
            if uid is None:
                await reply_and_track("无法识别你的 Telegram 用户 ID，请重新 /start。")
                return
            file_id = photos[-1].file_id
            await asyncio.to_thread(ui_pref_set_sync, cache_path, uid, "cover_file_id", file_id)
            context.user_data.pop("awaiting_custom_cover", None)
            await reply_and_track(
                "✅ 自定题图已保存。\n之后你打开 /start 时会优先显示这张题图。",
                reply_markup=parameter_config_keyboard(),
            )
            return

        if context.user_data.get("awaiting_custom_nickname"):
            if not is_allowed(update, cfg):
                if is_bot_self_update(update, cfg):
                    return
                context.user_data.pop("awaiting_custom_nickname", None)
                await reply_connection_status(update, cfg)
                return
            text = (update.effective_message.text or "").strip()
            if not text:
                await reply_and_track(
                    "请发送文字昵称；或点击 /start 返回主菜单。",
                    reply_markup=nickname_config_keyboard(),
                )
                return
            if len(text) > 32:
                await reply_and_track("昵称最多 32 个字符，请重新发送。")
                return
            uid = user_id(update)
            if uid is None:
                await reply_and_track("无法识别你的 Telegram 用户 ID，请重新 /start。")
                return
            await asyncio.to_thread(ui_pref_set_sync, cache_path, uid, "nickname", text)
            context.user_data.pop("awaiting_custom_nickname", None)
            await reply_and_track(
                f"✅ 自定昵称已保存：{text}\n之后你打开 /start 时会显示这个昵称。",
                reply_markup=parameter_config_keyboard(),
            )
            return

        if context.user_data.get("awaiting_alert_global_custom"):
            if not is_allowed(update, cfg):
                if is_bot_self_update(update, cfg):
                    return
                context.user_data.pop("awaiting_alert_global_custom", None)
                await reply_connection_status(update, cfg)
                return
            custom = context.user_data.get("awaiting_alert_global_custom")
            if isinstance(custom, dict):
                alert_type = str(custom.get("type") or "")
                chat_id = custom.get("chat_id")
                message_id = custom.get("message_id")
            else:
                alert_type = str(custom or "")
                chat_id = None
                message_id = None
            text = (update.effective_message.text or "").strip()

            async def edit_global_alert_prompt(message_text: str, keyboard: InlineKeyboardMarkup | None = None, parse_mode: str | None = "HTML") -> None:
                if chat_id and message_id:
                    try:
                        await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=message_text, parse_mode=parse_mode, reply_markup=keyboard)
                        return
                    except BadRequest as exc:
                        log.warning("编辑全局告警规则原文本消息失败：%s", exc)
                    try:
                        await context.bot.edit_message_caption(chat_id=chat_id, message_id=message_id, caption=message_text, parse_mode=parse_mode, reply_markup=keyboard)
                        return
                    except BadRequest as exc:
                        log.warning("编辑全局告警规则原图片说明失败：%s", exc)
                try:
                    await update.effective_message.edit_text(message_text, parse_mode=parse_mode, reply_markup=keyboard)
                except BadRequest as exc:
                    log.warning("编辑默认规则输入消息失败：%s", exc)

            if not re.fullmatch(r"\d+", text):
                unit = "GB，例如：150" if alert_type == "traffic" else "城市数，例如：4"
                await edit_global_alert_prompt(f"✍️ 请输入默认规则 ({unit})\n\n⚠️ 默认规则必须是正整数，请重新输入。", InlineKeyboardMarkup([back_close_row(f"alert_global:{alert_type}", "⬅️ 返回")]), None)
                return
            value = int(text)
            if value <= 0:
                unit = "GB，例如：150" if alert_type == "traffic" else "城市数，例如：4"
                await edit_global_alert_prompt(f"✍️ 请输入默认规则 ({unit})\n\n⚠️ 默认规则必须大于 0，请重新输入。", InlineKeyboardMarkup([back_close_row(f"alert_global:{alert_type}", "⬅️ 返回")]), None)
                return
            context.user_data.pop("awaiting_alert_global_custom", None)
            if alert_type not in {"traffic", "ip"}:
                await edit_global_alert_prompt("设置类型无效，请从菜单重新进入。", None, None)
                return
            before_period = await asyncio.to_thread(alert_global_period_sync, cache_path, alert_type)
            before_threshold = await asyncio.to_thread(alert_global_threshold_sync, cache_path, alert_type)
            before = f"{alert_period_label(before_period)} / {format_bytes(before_threshold) if alert_type == 'traffic' else str(before_threshold) + ' 个城市'}"
            await asyncio.to_thread(alert_set_global_threshold_sync, cache_path, alert_type, value)
            after_threshold = await asyncio.to_thread(alert_global_threshold_sync, cache_path, alert_type)
            after = f"{alert_period_label(before_period)} / {format_bytes(after_threshold) if alert_type == 'traffic' else str(after_threshold) + ' 个城市'}"
            await asyncio.to_thread(log_operation_from_update, update, alert_category(alert_type), "调整默认规则", alert_setting_before_after_detail(alert_type, "默认规则", before, after))
            result = await asyncio.to_thread(alert_global_setting_text_sync, cache_path, alert_type)
            await edit_global_alert_prompt(result, alert_global_keyboard(alert_type), "HTML")
            return

        if context.user_data.get("awaiting_alert_custom"):
            if not is_allowed(update, cfg):
                if is_bot_self_update(update, cfg):
                    return
                context.user_data.pop("awaiting_alert_custom", None)
                await reply_connection_status(update, cfg)
                return
            custom = context.user_data.get("awaiting_alert_custom") or {}
            alert_type = str(custom.get("type") or "")
            xboard_user_id = int(custom.get("user_id") or 0)
            text = (update.effective_message.text or "").strip()
            chat_id = custom.get("chat_id")
            message_id = custom.get("message_id")

            async def edit_alert_prompt(message_text: str, keyboard: InlineKeyboardMarkup | None = None, parse_mode: str | None = "HTML") -> None:
                if chat_id and message_id:
                    try:
                        await context.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=message_id,
                            text=message_text,
                            parse_mode=parse_mode,
                            reply_markup=keyboard,
                        )
                        return
                    except BadRequest as exc:
                        log.warning("编辑告警规则原文本消息失败：%s", exc)
                    try:
                        await context.bot.edit_message_caption(
                            chat_id=chat_id,
                            message_id=message_id,
                            caption=message_text,
                            parse_mode=parse_mode,
                            reply_markup=keyboard,
                        )
                        return
                    except BadRequest as exc:
                        log.warning("编辑告警规则原图片说明失败：%s", exc)
                # Do not create a new bot result message for threshold input. If the
                # original prompt is no longer editable, answer by editing the user's
                # input message as a last resort.
                try:
                    await update.effective_message.edit_text(message_text, parse_mode=parse_mode, reply_markup=keyboard)
                except BadRequest as exc:
                    log.warning("编辑用户规则输入消息失败：%s", exc)

            if not re.fullmatch(r"\d+", text):
                unit = "GB，例如：150" if alert_type == "traffic" else "城市数，例如：4"
                await edit_alert_prompt(
                    f"✍️ 请输入独立规则 ({unit})\n\n⚠️ 规则必须是正整数，请重新输入。",
                    InlineKeyboardMarkup([back_close_row(f"alert_user:{alert_type}:{xboard_user_id}", "⬅️ 返回")]),
                    None,
                )
                return
            value = int(text)
            if value <= 0:
                unit = "GB，例如：150" if alert_type == "traffic" else "城市数，例如：4"
                await edit_alert_prompt(
                    f"✍️ 请输入独立规则 ({unit})\n\n⚠️ 规则必须大于 0，请重新输入。",
                    InlineKeyboardMarkup([back_close_row(f"alert_user:{alert_type}:{xboard_user_id}", "⬅️ 返回")]),
                    None,
                )
                return
            context.user_data.pop("awaiting_alert_custom", None)
            before_setting = await asyncio.to_thread(alert_user_setting_sync, cache_path, xboard_user_id)
            before = alert_setting_label(before_setting, alert_type, cache_path)
            if alert_type == "traffic":
                await asyncio.to_thread(alert_upsert_setting_sync, cache_path, xboard_user_id, traffic_threshold_bytes=value * 1024 ** 3, traffic_whitelist=0)
            elif alert_type == "ip":
                await asyncio.to_thread(alert_upsert_setting_sync, cache_path, xboard_user_id, ip_city_threshold=value, ip_whitelist=0)
            else:
                await edit_alert_prompt("设置类型无效，请从菜单重新进入。", None, None)
                return
            after_setting = await asyncio.to_thread(alert_user_setting_sync, cache_path, xboard_user_id)
            after = alert_setting_label(after_setting, alert_type, cache_path)
            await asyncio.to_thread(log_operation_from_update, update, alert_category(alert_type), "调整独立规则", alert_setting_before_after_detail(alert_type, "独立规则", before, after, xboard_user_id))
            result = await asyncio.to_thread(alert_user_setting_text_sync, cache_path, alert_type, xboard_user_id)
            await edit_alert_prompt(result, alert_user_setting_keyboard(alert_type, xboard_user_id), "HTML")
            return

        if context.user_data.get("awaiting_user_ip_query_id"):
            if not is_allowed(update, cfg):
                if is_bot_self_update(update, cfg):
                    return
                context.user_data.pop("awaiting_user_ip_query_id", None)
                context.user_data.pop("user_ip_query_period", None)
                await reply_connection_status(update, cfg)
                return
            text = (update.effective_message.text or "").strip()
            if not re.fullmatch(r"\d+", text):
                await reply_and_track("用户 ID 必须是数字，请重新输入；或发送 /start 取消。")
                return
            context.user_data.pop("awaiting_user_ip_query_id", None)
            period_key = context.user_data.pop("user_ip_query_period", None)
            xboard_user_id = int(text)
            periods = {
                "1h": ("近 1 小时", timedelta(hours=1)),
                "24h": ("近 24 小时", timedelta(hours=24)),
                "7d": ("近 7 天", timedelta(days=7)),
                "30d": ("近 30 天", timedelta(days=30)),
            }
            label, window = periods.get(period_key, (None, None))
            start_ts = end_ts = None
            if period_key and period_key.startswith("custom:"):
                _, start_text, end_text = period_key.split(":", 2)
                start_ts = int(start_text)
                end_ts = int(end_text)
                label = "自定区间"
                window = None
            status_message = await update.effective_message.reply_text("正在读取缓存查询该用户近期活跃 IP，请稍候...")
            await track_auto_delete_message(status_message)
            result = await asyncio.to_thread(query_user_ips_from_cache_sync, cache_path, xboard_user_id, label, window, start_ts, end_ts, 0, 10)
            total_ips = await asyncio.to_thread(count_user_ips_from_cache_sync, cache_path, xboard_user_id, window, start_ts, end_ts)
            await edit_or_replace_status(
                status_message,
                result,
                update,
                parse_mode="HTML",
                reply_markup=user_ip_query_page_keyboard(period_key, xboard_user_id, total_ips, 0),
            )
            return

        await reply_connection_status(update, cfg)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear_history", clear_history_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("version", version_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("health", health_command))
    app.add_handler(CommandHandler("active_users", active_users))
    app.add_handler(CommandHandler("user_ip_query", user_ip_query))
    app.add_handler(CommandHandler("traffic_daily", traffic_daily))
    app.add_handler(CommandHandler("traffic_users", traffic_users))
    app.add_handler(CommandHandler("traffic_nodes", traffic_nodes))
    app.add_handler(CallbackQueryHandler(version_update_callback, pattern=r"^version_update:(?:start|confirm):v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$|^version_update:cancel$"))
    app.add_handler(CallbackQueryHandler(main_menu_callback, pattern=r"^main_menu(?::(clear_history|clear_history_confirm|system_check|system_check_refresh|status_notice|traffic_management|traffic_users|traffic_nodes|traffic_alerts|op_logs(?::(?:traffic_alert|ip_alert|ip_ignore|reset_cache|reset_ip|parameter_config|auth)(?::\d+)?)?|auth(?::(?:add|delete|del_done|del_toggle:\d+|del_confirm|roles|role_toggle:\d+|role_save))?|ip_monitor(?::(?:period|user_query|ignore|ignored_rules:\d+|ignored_rule_toggle:\d+:[A-Za-z0-9]+|ignore:(?:area|asn|cidr):\d+|ignore_toggle:(?:area|asn|cidr):\d+:[A-Za-z0-9]+))?|noop|parameter_config(?::(?:cover|cover_reset|nickname|nickname_reset|cache_retention|cache_retention_select:(?:1m|1q|1y|all)|cache_retention_confirm:(?:1m|1q|1y|all)))?|notifications(?::(?:daily|weekly|monthly|collector|traffic_alert|ip_alert|version_update))?|debug_tools|debug:reset_cache|debug:reset_cache_now|debug:reset_cache_now_confirm|debug:reset_cache_floor|debug:reset_user_ip|debug:reset_user_ip_page:\d+|debug:reset_user_ip_toggle:\d+:\d+|debug:reset_user_ip_done|debug:reset_user_ip_multi_confirm))?$"))
    app.add_handler(CallbackQueryHandler(alert_callback, pattern=r"^(alert_menu:(?:traffic|ip)|alert_period_page:(?:traffic|ip):\d+|alert_global_period_page:(?:traffic|ip)|alert_global:(?:traffic|ip)(?::(?:custom|period:(?:1h|24h|7d|today|week)))?|alert_users:(?:traffic|ip):\d+|alert_user:(?:traffic|ip):\d+(?::alert)?|alert_set:(?:traffic|ip):(?:custom:\d+|period:(?:1h|24h|7d|today|week):\d+|threshold:\d+:\d+|whitelist:\d+|reset:\d+))$"))
    app.add_handler(CallbackQueryHandler(traffic_daily_callback, pattern=r"^(traffic_menu(?::[A-Za-z0-9_]+)?|traffic_back:[A-Za-z0-9_]+|traffic_(?:period|switch):(preset_1h|preset_24h|preset_7d|preset_30d|today|yesterday|this_week|this_month)(?::(?:users|nodes))?|ip_custom:start|traffic_custom:(start(?::(?:combined|users|nodes))?|now|(year|month|day|hour|minute):\d+|back:(year|month|day|hour))|traffic_floor:(start|confirm:\d+)|traffic_dashboard:(pin|unpin|delete):[A-Za-z0-9_]+)$"))
    app.add_handler(CallbackQueryHandler(active_users_callback, pattern=r"^(active_users(?::|_query:)(1h|24h|7d|30d)(?::\d+)?|ip_user_query:(?:(1h|24h|7d|30d)|custom:\d+:\d+)|user_ip_page:\d+:\d+:(?:all|(?:1h|24h|7d|30d)|custom:\d+:\d+)|active_user_detail:(1h|24h|7d|30d):\d+|active_users_cancel:(1h|24h|7d|30d)|noop)$"))
    app.add_handler(CallbackQueryHandler(ip_detail_callback, pattern=r"^(?:ip_(?:detail_list|active_user_detail):(ip_(?:1h|24h|7d|30d)|iprange_\d+_\d+):(\d+)(?::\d+)?(?::alert)?|ip_alert_notice:\d+|ip_ignore_menu:(ip_(?:1h|24h|7d|30d)|iprange_\d+_\d+):\d+:\d+(?::alert)?|ip_ignore_page:(?:area|asn|cidr):(ip_(?:1h|24h|7d|30d)|iprange_\d+_\d+):\d+:\d+:\d+(?::alert)?|ip_ig_t:[A-Za-z0-9]+|ip_ignore_toggle:(?:area|asn|cidr):(ip_(?:1h|24h|7d|30d)|iprange_\d+_\d+):\d+:\d+:\d+:[A-Za-z0-9]+(?::alert)?)$"))
    app.add_handler(CallbackQueryHandler(detail_back_callback, pattern=r"^detail_back:(1h|24h|7d|30d|menu)$"))
    app.add_handler(CallbackQueryHandler(close_message_callback, pattern=r"^close_message$"))
    app.add_handler(MessageHandler(filters.ALL, fallback))
    return app


async def run_once(
    cfg: AppConfig,
    stop_event: asyncio.Event,
) -> None:
    cache_path = resolve_cache_path(cfg.cache_path, APP_DIR)
    init_cache(cache_path)

    app = build_application(cfg, cache_path)
    collector_stop_event = asyncio.Event()
    collector_task: asyncio.Task[Any] | None = None
    sampler_stop_event = asyncio.Event()
    sampler_task: asyncio.Task[Any] | None = None
    dashboard_stop_event = asyncio.Event()
    dashboard_task: asyncio.Task[Any] | None = None
    report_stop_event = asyncio.Event()
    report_task: asyncio.Task[Any] | None = None
    version_stop_event = asyncio.Event()
    version_task: asyncio.Task[Any] | None = None

    await app.initialize()
    await app.bot.set_my_commands(BOT_COMMANDS)
    await app.start()
    if not app.updater:
        raise RuntimeError("Telegram updater 初始化失败")
    redis_ok, redis_detail, mysql_ok, mysql_detail, geo_total, geo_success, geo_failed = await asyncio.to_thread(initialize_cache_before_notifications_sync, cfg, cache_path)
    await notify_collector_health_transition(app, cfg, cache_path, "redis", redis_ok, redis_detail or "Redis 缓存采集已恢复成功。")
    if redis_ok or mysql_detail:
        await notify_collector_health_transition(app, cfg, cache_path, "mysql", mysql_ok, mysql_detail or "MySQL 用户信息采集已恢复成功。")
    if geo_success:
        await notify_collector_health_transition(app, cfg, cache_path, "ip_api", True, "IP-API 已恢复响应，启动初始化已完成 IP 归属地补全。")
    elif geo_failed:
        await notify_collector_health_transition(app, cfg, cache_path, "ip_api", False, f"启动初始化 IP 归属地补全失败 {geo_failed} 个。")
    await check_ip_alerts(app, cfg, cache_path)
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    await cleanup_legacy_traffic_dashboard_messages(app, cache_path)
    collector_task = asyncio.create_task(cache_collector_loop(app, cfg, cache_path, collector_stop_event))
    sampler_task = asyncio.create_task(traffic_sampler_loop(app, cfg, cache_path, sampler_stop_event))
    dashboard_task = asyncio.create_task(traffic_dashboard_refresh_loop(app, cfg, cache_path, dashboard_stop_event))
    report_task = asyncio.create_task(traffic_report_push_loop(app, cache_path, report_stop_event))
    version_task = asyncio.create_task(version_update_check_loop(app, cfg, cache_path, version_stop_event))
    await send_update_result_notice(app)
    log.info("Telegram Bot 已启动，缓存文件：%s", cache_path)

    try:
        await stop_event.wait()
        return None
    finally:
        log.info("正在停止 Telegram Bot 和缓存采集任务...")
        collector_stop_event.set()
        sampler_stop_event.set()
        dashboard_stop_event.set()
        report_stop_event.set()
        version_stop_event.set()
        if collector_task:
            try:
                await asyncio.wait_for(collector_task, timeout=10)
            except asyncio.TimeoutError:
                collector_task.cancel()
        if sampler_task:
            try:
                await asyncio.wait_for(sampler_task, timeout=10)
            except asyncio.TimeoutError:
                sampler_task.cancel()
        if dashboard_task:
            try:
                await asyncio.wait_for(dashboard_task, timeout=10)
            except asyncio.TimeoutError:
                dashboard_task.cancel()
        if report_task:
            try:
                await asyncio.wait_for(report_task, timeout=10)
            except asyncio.TimeoutError:
                report_task.cancel()
        if version_task:
            try:
                await asyncio.wait_for(version_task, timeout=10)
            except asyncio.TimeoutError:
                version_task.cancel()
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        log.info("Telegram Bot 已停止")


async def serve() -> None:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    cfg = build_config_from_env()

    while not stop_event.is_set():
        try:
            await run_once(cfg, stop_event)
            break
        except Exception as exc:
            log.exception("服务运行异常：%s", exc)
            if stop_event.is_set():
                break
            log.info("5 秒后重试启动")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=5)
                break
            except asyncio.TimeoutError:
                cfg = build_config_from_env()
                continue


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Xbot Telegram Monitor Bot")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    # 避免 httpx 在日志中输出 Telegram Bot Token 所在的完整请求 URL。
    logging.getLogger("httpx").setLevel(logging.WARNING)

    parse_args()
    asyncio.run(serve())


if __name__ == "__main__":
    main()

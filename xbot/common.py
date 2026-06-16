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
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
    Update,
)
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
BOT_COMMANDS = [
    BotCommand("start", "主菜单"),
    BotCommand("version", "查看版本"),
    BotCommand("clear_history", "清除对话记录"),
]
log = logging.getLogger("xbot")
PROCESS_STARTED_AT = datetime.now()
TRAFFIC_SAMPLE_INTERVAL_SECONDS = 60
TRAFFIC_SAMPLE_GAP_TOLERANCE_SECONDS = 90
BEIJING_TZ = timezone(timedelta(hours=8))
TRAFFIC_REPORT_KINDS = {
    "daily": "流量日报",
    "weekly": "流量周报",
    "monthly": "流量月报",
}
ALERT_NOTIFICATION_KINDS = {"traffic_alert": "用量异常", "ip_alert": "异地登录"}
NOTIFICATION_KINDS = {
    "collector": "采集异常",
    **ALERT_NOTIFICATION_KINDS,
    **TRAFFIC_REPORT_KINDS,
    "version_update": "版本更新",
}
DEFAULT_ALLOWLIST_NOTIFICATION_KINDS = set(NOTIFICATION_KINDS)
COLLECTOR_HEALTH_SERVICES = {"redis": "Redis", "mysql": "MySQL", "ip_api": "IP-API"}
TRAFFIC_ALERT_DEFAULT_PERIOD = "1h"
TRAFFIC_ALERT_DEFAULT_THRESHOLD_BYTES = 100 * 1024**3
IP_ALERT_DEFAULT_CITY_THRESHOLD = 3
DEFAULT_CACHE_RETENTION_DAYS = 0
DEFAULT_COLLECTOR_INTERVAL_SECONDS = 60.0
DEFAULT_IP_GEO_QUERIES_PER_MINUTE = 30
CACHE_RETENTION_OPTIONS = {
    "1m": (31, "一月"),
    "1y": (366, "一年"),
    "all": (0, "一切"),
}
ALERT_DEFAULT_PERIOD = "24h"
ALERT_PERIOD_LABELS = {
    "1h": "近 1 小时",
    "24h": "近 24 小时",
    "7d": "近 7 天",
    "today": "今天",
    "week": "本周",
}
IP_PERIODS = {
    "1h": ("近 1 小时", 3600),
    "24h": ("近 24 小时", 24 * 3600),
    "7d": ("近 7 天", 7 * 24 * 3600),
    "30d": ("近 30 天", 30 * 24 * 3600),
}
APP_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_PATH = APP_DIR / "data" / "xbot.sqlite3"
TAGS_API_URL = "https://api.github.com/repos/KakidSan/Xbot/tags"
GHCR_IMAGE = "ghcr.io/kakidsan/xbot"
VERSION_FILE = APP_DIR / "VERSION"
FALLBACK_VERSION = "0.0.0-dev"
UPDATE_SCRIPT = APP_DIR / "scripts" / "update.sh"
UPDATE_STATUS_FILE = APP_DIR / ".install-state" / "update-status.json"
VERSION_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
_INITIALIZED_CACHE_PATHS: set[Path] = set()


# Export shared constants/imports, including private module-level state that was
# formerly global in the single-file runtime.


def _as_int_set(value: Any) -> set[int]:
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = str(value).replace(";", ",").split(",")
    result: set[int] = set()
    for item in items:
        item_str = str(item).strip()
        if not item_str:
            continue
        try:
            result.add(int(item_str))
        except ValueError:
            log.warning("忽略无效 Telegram 用户 ID：%s", item_str)
    return result


def html_code(value: Any) -> str:
    return f"<code>{html.escape(str(value))}</code>"


def compact_connection_error_lines(result: str) -> list[str]:
    raw_lines = [line.strip() for line in result.splitlines() if line.strip()]
    summary_candidates = [
        line
        for line in raw_lines[1:]
        if line.startswith("❌") and "错误类型" not in line and "错误代码" not in line
    ]
    lines: list[str] = []
    if summary_candidates:
        lines.append(f"　{summary_candidates[0]}")
    for line in raw_lines:
        if "错误类型" in line or "错误代码" in line:
            lines.append(f"　{line}")
    reason_candidates = [
        line
        for line in summary_candidates[1:]
        if "可能" not in line and "常见原因" not in line
    ]
    if reason_candidates:
        lines.append(f"　{reason_candidates[-1]}")
    return lines


# Keep this at the bottom so star imports include shared private helpers too.


def beijing_now() -> datetime:
    return datetime.now(BEIJING_TZ)


def beijing_midnight(dt: datetime | None = None) -> datetime:
    current = dt.astimezone(BEIJING_TZ) if dt else beijing_now()
    return current.replace(hour=0, minute=0, second=0, microsecond=0)


def alert_period_label(period: str | None) -> str:
    return ALERT_PERIOD_LABELS.get(
        period or ALERT_DEFAULT_PERIOD, ALERT_PERIOD_LABELS[ALERT_DEFAULT_PERIOD]
    )


def alert_period_window(
    period: str | None, now: datetime | None = None
) -> tuple[int, int, str]:
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


def traffic_report_window(
    kind: str, now: datetime | None = None
) -> tuple[int, int, str]:
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


# Shared authorization helpers used outside Telegram handler modules.
def is_super_admin_user_id(uid: int | None, cfg: Any) -> bool:
    super_admin_ids: set[int] = getattr(cfg.telegram, "super_admin_user_ids", set())
    return bool(uid is not None and uid in super_admin_ids)


def is_admin_user_id(uid: int | None, cfg: Any) -> bool:
    return bool(
        uid is not None and uid in cfg.telegram.manager_user_ids
    ) or is_super_admin_user_id(uid, cfg)

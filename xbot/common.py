from __future__ import annotations
#!/usr/bin/env python3
"""Xbot Telegram Monitor Bot.

当前版本能力：
- Docker / Docker Compose 后台常驻运行；
- 使用环境变量传入 Telegram / Redis / MySQL 连接参数；
- Telegram 用户白名单校验；
- MySQL 连接测试，只执行只读查询；
- Redis 连接测试，并读取少量摘要信息格式化展示。
"""


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
__all__ = [name for name in globals() if not name.startswith("__")]

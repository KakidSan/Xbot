from __future__ import annotations

import asyncio
import base64
import html
import logging
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .config import AppConfig
from .db.cache import speedtest_jump_targets_sync
from .db.mysql import mysql_config_missing, mysql_connect

log = logging.getLogger(__name__)

NODE_LINK_PAGE_SIZE = 10
NODE_LINK_SUBSCRIPTION_REFRESH_SECONDS = 3600


@dataclass(frozen=True)
class SubscriptionNode:
    id: int
    name: str
    link: str
    scheme: str


_subscription_nodes_cache: dict[int, tuple[float, list[SubscriptionNode]]] = {}
_subscription_nodes_refresh_events: dict[int, asyncio.Event] = {}


def _base64_decode_subscription(raw: bytes) -> str:
    data = raw.strip()
    lowered = data[:4096].lower()
    if b"://" in data[:4096] or b"proxies:" in lowered or b"proxy-groups:" in lowered:
        return raw.decode("utf-8", "replace")
    try:
        padding = b"=" * (-len(data) % 4)
        return base64.b64decode(data + padding, validate=False).decode("utf-8", "replace")
    except Exception:
        return raw.decode("utf-8", "replace")


def _subscription_node_name(link: str, index: int) -> str:
    parsed = urllib.parse.urlsplit(link)
    if parsed.fragment:
        return urllib.parse.unquote(parsed.fragment).strip() or f"节点 {index}"
    return f"节点 {index}"


def _relative_time_label(timestamp: float | None) -> str:
    if not timestamp:
        return "未刷新"
    delta = max(0, int(time.time() - timestamp))
    if delta < 10:
        return "刚刚"
    if delta < 60:
        return f"{delta} 秒前"
    minutes = delta // 60
    if minutes < 60:
        return f"{minutes} 分钟前"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} 小时前"
    days = hours // 24
    return f"{days} 天前"


def _fetch_official_subscription_text_sync(cfg: AppConfig, flag: str) -> str:
    if mysql_config_missing(cfg.mysql):
        return ""
    user_id = int(getattr(cfg, "link_extract_user_id", 1) or 1)
    conn = mysql_connect(cfg.mysql)
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT token FROM v2_user WHERE id = %s LIMIT 1", (user_id,))
            user = cursor.fetchone() or {}
            token = str(user.get("token") or "").strip()
            if not token:
                return ""
            cursor.execute(
                "SELECT name, value FROM v2_settings WHERE name IN ('subscribe_url', 'subscribe_path')"
            )
            settings = {
                str(row.get("name")): str(row.get("value") or "")
                for row in cursor.fetchall()
            }
    finally:
        conn.close()

    base_url = (settings.get("subscribe_url") or "").strip().rstrip("/")
    path = (settings.get("subscribe_path") or "s").strip().strip("/")
    if not base_url:
        return ""
    url = f"{base_url}/{path}/{urllib.parse.quote(token, safe='')}?flag={urllib.parse.quote(flag, safe='')}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": f"XbotLinkExtract/1.0 ({flag})"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return _base64_decode_subscription(response.read())


def fetch_subscription_nodes_sync(cfg: AppConfig, *, force_refresh: bool = False) -> list[SubscriptionNode]:
    user_id = int(getattr(cfg, "link_extract_user_id", 1) or 1)
    cached = _subscription_nodes_cache.get(user_id)
    if cached and not force_refresh:
        return list(cached[1])

    text = _fetch_official_subscription_text_sync(cfg, "general")
    nodes: list[SubscriptionNode] = []
    for line in text.splitlines():
        link = line.strip()
        if not link or "://" not in link:
            continue
        scheme = urllib.parse.urlsplit(link).scheme.lower() or "unknown"
        nodes.append(
            SubscriptionNode(
                id=len(nodes) + 1,
                name=_subscription_node_name(link, len(nodes) + 1),
                link=link,
                scheme=scheme,
            )
        )
    _subscription_nodes_cache[user_id] = (time.time(), nodes)
    event = _subscription_nodes_refresh_events.get(user_id)
    if event:
        event.set()
    return list(nodes)


def refresh_subscription_nodes_sync(cfg: AppConfig) -> int:
    return len(fetch_subscription_nodes_sync(cfg, force_refresh=True))


def subscription_nodes_refreshed_label_sync(cfg: AppConfig) -> str:
    user_id = int(getattr(cfg, "link_extract_user_id", 1) or 1)
    cached = _subscription_nodes_cache.get(user_id)
    return _relative_time_label(cached[0] if cached else None)


async def subscription_nodes_refresh_loop(cfg: AppConfig, stop_event: asyncio.Event) -> None:
    user_id = int(getattr(cfg, "link_extract_user_id", 1) or 1)
    event = _subscription_nodes_refresh_events.setdefault(user_id, asyncio.Event())
    while not stop_event.is_set():
        event.clear()
        try:
            total = await asyncio.to_thread(refresh_subscription_nodes_sync, cfg)
            log.info("链接提取订阅缓存已刷新：%s 个节点", total)
        except Exception as exc:
            log.warning("链接提取订阅缓存刷新失败：%s", exc)
        try:
            await asyncio.wait_for(
                asyncio.gather(stop_event.wait(), event.wait(), return_exceptions=True),
                timeout=NODE_LINK_SUBSCRIPTION_REFRESH_SECONDS,
            )
        except asyncio.TimeoutError:
            continue


def node_links_keyboard_sync(cfg: AppConfig, page: int = 0) -> Any:
    nodes = fetch_subscription_nodes_sync(cfg)
    total_pages = max(1, (len(nodes) + NODE_LINK_PAGE_SIZE - 1) // NODE_LINK_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * NODE_LINK_PAGE_SIZE
    rows: list[list[InlineKeyboardButton]] = []
    for node in nodes[start : start + NODE_LINK_PAGE_SIZE]:
        rows.append([InlineKeyboardButton(node.name[:60], callback_data=f"node_link:select:{node.id}:{page}")])
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"node_link:page:{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"node_link:page:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔄 刷新", callback_data=f"node_link:refresh:{page}")])
    rows.append([InlineKeyboardButton("⬅️ 返回主菜单", callback_data="back_to_main_menu")])
    return InlineKeyboardMarkup(rows)


def format_node_links_text_sync(cfg: AppConfig, page: int = 0) -> str:
    nodes = fetch_subscription_nodes_sync(cfg)
    refreshed = subscription_nodes_refreshed_label_sync(cfg)
    return "\n".join(
        [
            "💡 <b>链接提取</b>",
            "────────────",
            f"可用节点：<b>{len(nodes)}</b> 个",
            f"最后刷新：<b>{html.escape(refreshed)}</b>",
            "",
            "请选择一个节点。",
        ]
    )


def format_node_link_detail_sync(cfg: AppConfig, node_id: int) -> str:
    nodes = {node.id: node for node in fetch_subscription_nodes_sync(cfg)}
    node = nodes.get(node_id)
    if not node:
        return "💡 <b>链接提取</b>\n────────────\n节点不存在或订阅已刷新，请返回列表重试。"
    return "\n".join(
        [
            "💡 <b>链接提取</b>",
            "────────────",
            f"节点：<b>{html.escape(node.name)}</b>",
            f"协议：<code>{html.escape(node.scheme)}</code>",
            "",
            f"<code>{html.escape(node.link)}</code>",
        ]
    )


def node_link_detail_keyboard_sync(cache_path: Path, owner_user_id: int, page: int = 0) -> Any:
    rows: list[list[InlineKeyboardButton]] = []
    for row in speedtest_jump_targets_sync(cache_path, owner_user_id):
        nickname = str(row.get("nickname") or row.get("telegram_id") or "测试工具")
        username = str(row.get("username") or "").strip().lstrip("@")
        telegram_id = int(row.get("telegram_id") or 0)
        url = f"https://t.me/{username}" if username else f"tg://user?id={telegram_id}"
        rows.append([InlineKeyboardButton(nickname[:60], url=url)])
    rows.append([InlineKeyboardButton("⬅️ 返回节点列表", callback_data=f"node_link:page:{page}")])
    rows.append([InlineKeyboardButton("❌ 关闭", callback_data="close_message")])
    return InlineKeyboardMarkup(rows)

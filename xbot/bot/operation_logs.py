from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from ..common import BEIJING_TZ, InlineKeyboardButton, InlineKeyboardMarkup, Update, html
from ..db.cache import (
    actor_name_from_user,
    operation_log_add_sync,
    operation_log_counts_sync,
    operation_log_get_sync,
    operation_logs_list_sync,
)
from .authorization import auth_user_ids_to_labels
from .formatters import cached_user_name_by_id, render_user_label


OPERATION_LOG_CATEGORIES = {
    "traffic_alert": "流量告警规则调整",
    "ip_alert": "IP 监控规则调整",
    "ip_ignore": "IP 忽略调整",
    "reset_cache": "重置缓存",
    "reset_ip": "重置 IP 记录",
    "parameter_config": "参数配置",
    "auth": "授权管理",
}

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


def operation_logs_menu_keyboard(cache_path: Path, viewer_user_id: int) -> InlineKeyboardMarkup:
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


def operation_log_action_label(category: str, action: str) -> str:
    icon = OPERATION_LOG_ACTION_ICONS.get(category, {}).get(action)
    return f"{icon} {action}" if icon and not action.startswith(icon) else action


def xboard_user_label_sync(cache_path: Path, uid: int) -> str:
    return render_user_label(uid, cached_user_name_by_id(cache_path, uid))


def xboard_user_ids_to_labels(cache_path: Path, value: str) -> str:
    def repl(match: re.Match[str]) -> str:
        return xboard_user_label_sync(cache_path, int(match.group(1)))

    raw = value.strip()
    if not raw:
        return raw
    return re.sub(r"(?<![\w@])(?:用户|XBoard 用户)\s*(\d+)(?![\w@])", repl, raw)


def operation_log_detail_display_text(cache_path: Path, category: str, detail: str) -> str:
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
            converted.append(f"{key}：{auth_user_ids_to_labels(cache_path, value)}")
        elif key in xboard_user_fields:
            converted.append(f"{key}：{xboard_user_ids_to_labels(cache_path, value)}")
        else:
            converted.append(line)
    return "\n".join(converted)


def operation_logs_summary_keyboard(cache_path: Path, category: str, viewer_user_id: int) -> InlineKeyboardMarkup:
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


def operation_log_summary_text_sync(cache_path: Path, category: str, viewer_user_id: int) -> str:
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


def operation_log_detail_text_sync(cache_path: Path, log_id: int) -> str:
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
    detail = html.escape(operation_log_detail_display_text(cache_path, category, str(row.get("detail") or "")))
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


def log_operation_from_query(cache_path: Path, query: Any, category: str, action: str, detail: str = "") -> None:
    user = getattr(query, "from_user", None)
    operation_log_add_sync(cache_path, getattr(user, "id", None), actor_name_from_user(user), category, action, detail)


def log_operation_from_update(cache_path: Path, update: Update, category: str, action: str, detail: str = "") -> None:
    user = update.effective_user
    operation_log_add_sync(cache_path, getattr(user, "id", None), actor_name_from_user(user), category, action, detail)


def alert_category(alert_type: str) -> str:
    return "traffic_alert" if alert_type == "traffic" else "ip_alert"


def alert_type_label(alert_type: str) -> str:
    return "流量告警" if alert_type == "traffic" else "IP 监控"


def alert_setting_before_after_detail(alert_type: str, scope: str, before: str, after: str, xboard_user_id: int | None = None) -> str:
    target = f"XBoard 用户 {xboard_user_id}" if xboard_user_id is not None else "默认规则"
    return f"对象：{target}\n类型：{alert_type_label(alert_type)}\n修改前：{before}\n修改后：{after}"

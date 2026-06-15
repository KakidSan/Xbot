from __future__ import annotations

from ..common import (
    ALERT_DEFAULT_PERIOD,
    Any,
    CACHE_RETENTION_OPTIONS,
    DEFAULT_ALLOWLIST_NOTIFICATION_KINDS,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    NOTIFICATION_KINDS,
    alert_period_window,
    datetime,
    ip_range_kind,
)
from ..db.cache import (
    alert_global_period_sync,
    alert_global_threshold_sync,
    alert_user_setting_sync,
    cache_retention_days_sync,
    notification_ip_alert_mode_sync,
    notification_status_sync,
)
from .formatters import alert_period_label, format_bytes
from .menus import back_close_row

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


def cache_retention_keyboard(cache_path, selected_days: int | None = None) -> InlineKeyboardMarkup:
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


def notification_push_keyboard(cache_path, chat_id: str, is_admin: bool = False) -> InlineKeyboardMarkup:
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


def alert_user_current_period_and_threshold(cache_path, alert_type: str, xboard_user_id: int) -> tuple[str, str]:
    setting = alert_user_setting_sync(cache_path, xboard_user_id)
    if alert_type == "traffic":
        period = setting.get("traffic_period") or alert_global_period_sync(cache_path, "traffic")
        threshold = int(setting.get("traffic_threshold_bytes") or alert_global_threshold_sync(cache_path, "traffic"))
        return alert_period_label(period), format_bytes(threshold)
    period = setting.get("ip_period") or alert_global_period_sync(cache_path, "ip")
    threshold = int(setting.get("ip_city_threshold") or alert_global_threshold_sync(cache_path, "ip"))
    return alert_period_label(period), f"{threshold} 个城市"


def alert_user_setting_keyboard(cache_path, alert_type: str, xboard_user_id: int) -> InlineKeyboardMarkup:
    setting = alert_user_setting_sync(cache_path, xboard_user_id)
    whitelist_key = "traffic_whitelist" if alert_type == "traffic" else "ip_whitelist"
    is_whitelisted = bool(int(setting.get(whitelist_key) or 0))
    whitelist_text = "🌑 取消白名单" if is_whitelisted else "🌕 设为白名单"
    period_text, threshold_text = alert_user_current_period_and_threshold(cache_path, alert_type, xboard_user_id)
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


def alert_global_current_period_and_threshold(cache_path, alert_type: str) -> tuple[str, str]:
    period = alert_global_period_sync(cache_path, alert_type)
    threshold = alert_global_threshold_sync(cache_path, alert_type)
    if alert_type == "traffic":
        return alert_period_label(period), format_bytes(threshold)
    return alert_period_label(period), f"{threshold} 个城市"


def alert_global_keyboard(cache_path, alert_type: str) -> InlineKeyboardMarkup:
    period_text, threshold_text = alert_global_current_period_and_threshold(cache_path, alert_type)
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
# Export this module's own public symbols for downstream star imports.
__all__ = [name for name in globals() if not name.startswith("_")]

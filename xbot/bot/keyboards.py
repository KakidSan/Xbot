from __future__ import annotations

import calendar
import hashlib
from pathlib import Path

from ..common import (
    ALERT_DEFAULT_PERIOD,
    Any,
    CACHE_RETENTION_OPTIONS,
    ContextTypes,
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
    ignored_list_items_sync,
    ignored_rule_items_sync,
    ignored_rule_values_sync,
    earliest_traffic_sample_at_sync,
    notification_ip_alert_mode_sync,
    notification_status_sync,
    user_ip_ignore_items_sync,
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


def traffic_dashboard_keyboard(kind: str, is_pinned: bool = False) -> InlineKeyboardMarkup:
    return traffic_dashboard_keyboard_static(kind, is_pinned)


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


def cache_retention_keyboard(cache_path: Path, selected_days: int | None = None) -> InlineKeyboardMarkup:
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


def notification_push_keyboard(cache_path: Path, chat_id: str, is_admin: bool = False) -> InlineKeyboardMarkup:
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


def alert_user_current_period_and_threshold(cache_path: Path, alert_type: str, xboard_user_id: int) -> tuple[str, str]:
    setting = alert_user_setting_sync(cache_path, xboard_user_id)
    if alert_type == "traffic":
        period = setting.get("traffic_period") or alert_global_period_sync(cache_path, "traffic")
        threshold = int(setting.get("traffic_threshold_bytes") or alert_global_threshold_sync(cache_path, "traffic"))
        return alert_period_label(period), format_bytes(threshold)
    period = setting.get("ip_period") or alert_global_period_sync(cache_path, "ip")
    threshold = int(setting.get("ip_city_threshold") or alert_global_threshold_sync(cache_path, "ip"))
    return alert_period_label(period), f"{threshold} 个城市"


def alert_user_setting_keyboard(cache_path: Path, alert_type: str, xboard_user_id: int) -> InlineKeyboardMarkup:
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


def alert_global_current_period_and_threshold(cache_path: Path, alert_type: str) -> tuple[str, str]:
    period = alert_global_period_sync(cache_path, alert_type)
    threshold = alert_global_threshold_sync(cache_path, alert_type)
    if alert_type == "traffic":
        return alert_period_label(period), format_bytes(threshold)
    return alert_period_label(period), f"{threshold} 个城市"


def alert_global_keyboard(cache_path: Path, alert_type: str) -> InlineKeyboardMarkup:
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


def alert_user_setting_keyboard_for_source(cache_path: Path, alert_type: str, xboard_user_id: int, source: str | None = None) -> InlineKeyboardMarkup:
    keyboard = alert_user_setting_keyboard(cache_path, alert_type, xboard_user_id)
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


def traffic_custom_available_bounds(cache_path: Path) -> tuple[int, int]:
    first = earliest_traffic_sample_at_sync(cache_path)
    now_ts = int(datetime.now().timestamp())
    return (first or now_ts, now_ts)


def traffic_custom_single_year(cache_path: Path) -> bool:
    first_ts, now_ts = traffic_custom_available_bounds(cache_path)
    return datetime.fromtimestamp(first_ts).year == datetime.fromtimestamp(now_ts).year


def traffic_custom_year_keyboard(cache_path: Path, mode: str | None = None, include_now: bool = False, dimension: str = "combined") -> InlineKeyboardMarkup:
    first_ts, now_ts = traffic_custom_available_bounds(cache_path)
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


def traffic_custom_month_keyboard(cache_path: Path, year: int, include_now: bool = False, mode: str | None = None, dimension: str = "combined") -> InlineKeyboardMarkup:
    first_ts, now_ts = traffic_custom_available_bounds(cache_path)
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
    if traffic_custom_single_year(cache_path):
        back_callback = "main_menu:ip_monitor:period" if mode == "ip_custom" else "traffic_menu"
        rows.append([InlineKeyboardButton("⬅️ 返回", callback_data=back_callback), InlineKeyboardButton("❌ 关闭", callback_data="close_message")])
    else:
        rows.append([InlineKeyboardButton("⬅️ 返回年份", callback_data="traffic_custom:back:year"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")])
    return InlineKeyboardMarkup(rows)


def traffic_custom_day_keyboard(cache_path: Path, year: int, month: int, mode: str | None = None, dimension: str = "combined") -> InlineKeyboardMarkup:
    first_ts, now_ts = traffic_custom_available_bounds(cache_path)
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


def traffic_custom_hour_keyboard(cache_path: Path, year: int, month: int, day: int, mode: str | None = None, dimension: str = "combined") -> InlineKeyboardMarkup:
    first_ts, now_ts = traffic_custom_available_bounds(cache_path)
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


def traffic_custom_minute_keyboard(cache_path: Path, year: int, month: int, day: int, hour: int, mode: str | None = None, dimension: str = "combined") -> InlineKeyboardMarkup:
    first_ts, now_ts = traffic_custom_available_bounds(cache_path)
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

def ignored_rules_keyboard(cache_path: Path, context: ContextTypes.DEFAULT_TYPE, page: int = 0) -> InlineKeyboardMarkup:
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

def ip_ignore_list_keyboard(cache_path: Path, context: ContextTypes.DEFAULT_TYPE, dimension: str, page: int = 0) -> InlineKeyboardMarkup:
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

def user_ip_ignore_list_keyboard(cache_path: Path, context: ContextTypes.DEFAULT_TYPE, dimension: str, kind: str, xboard_user_id: int, detail_page: int, list_page: int = 0, source: str | None = None) -> InlineKeyboardMarkup:
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

def traffic_floor_confirm_keyboard(floor_ts: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ 确认调整起始点", callback_data=f"traffic_floor:confirm:{floor_ts}")],
        [InlineKeyboardButton("🔄 重新选择", callback_data="traffic_floor:start"), InlineKeyboardButton("❎ 取消", callback_data="main_menu")],
    ])

def traffic_custom_keyboard_for_state(cache_path: Path, state: dict[str, Any]) -> InlineKeyboardMarkup:
    step = state.get("step", "year")
    year = int(state.get("year") or 0)
    month = int(state.get("month") or 0)
    day = int(state.get("day") or 0)
    hour = int(state.get("hour") or 0)
    mode = str(state.get("mode") or "")
    dimension = str(state.get("dimension") or "combined")
    if step == "month" and year:
        include_now = state.get("phase") == "end" and state.get("mode") in {"custom", "ip_custom"} and traffic_custom_single_year(cache_path)
        return traffic_custom_month_keyboard(cache_path, year, include_now=include_now, mode=mode, dimension=dimension)
    if step == "day" and year and month:
        return traffic_custom_day_keyboard(cache_path, year, month, mode=mode, dimension=dimension)
    if step == "hour" and year and month and day:
        return traffic_custom_hour_keyboard(cache_path, year, month, day, mode=mode, dimension=dimension)
    if step == "minute" and year and month and day:
        return traffic_custom_minute_keyboard(cache_path, year, month, day, hour, mode=mode, dimension=dimension)
    include_now = state.get("phase") == "end" and state.get("step") == "year" and state.get("mode") in {"custom", "ip_custom"}
    return traffic_custom_year_keyboard(cache_path, mode, include_now=include_now, dimension=dimension)

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

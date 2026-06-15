from __future__ import annotations

from ..common import InlineKeyboardButton, InlineKeyboardMarkup


def back_close_row(back_callback: str = "main_menu", back_text: str = "⬅️ 返回主菜单") -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(back_text, callback_data=back_callback), InlineKeyboardButton("❌ 关闭", callback_data="close_message")]


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
        [InlineKeyboardButton("🚧 忽略列表", callback_data="main_menu:ip_monitor:ignore")],
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

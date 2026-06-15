from __future__ import annotations
from .._bootstrap import install_module_symbols
from ..common import *

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


install_module_symbols(globals())

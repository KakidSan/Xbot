from __future__ import annotations

from ...common import ContextTypes, InlineKeyboardMarkup, Path, Update, asyncio, re
from ...config import AppConfig
from ...db.cache import ignored_rule_toggle_sync, ignored_rule_values_sync
from ...geo import ignored_rules_text_sync
from ..context import BotContext
from ..keyboards import ignored_rules_keyboard, ip_ignore_list_keyboard
from ..menus import back_close_row, ip_ignore_menu_keyboard, ip_monitor_keyboard
from ..operation_details import ip_ignore_detail
from ..operation_logs import log_operation_from_query as log_operation_from_query_with_cache


async def ip_monitor_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    cfg: AppConfig,
    bot_ctx: BotContext,
    cache_path: Path,
    data: str,
    query,
    answer_callback_silently,
    show_callback_page,
    open_dashboard_card,
) -> bool:
    if not (data.startswith("main_menu:ip_monitor") or data == "main_menu:noop"):
        return False
    if data == "main_menu:ip_monitor":
        await answer_callback_silently(query)
        await show_callback_page(
            query,
            "🌐 <b>IP 监控</b>\n────────────\n请选择功能。",
            ip_monitor_keyboard(),
            parse_mode="HTML",
        )
        return

    if data == "main_menu:ip_monitor:period":
        await query.answer("正在生成查询，请稍候...")
        await open_dashboard_card(query, "ip_1h")
        return

    if data == "main_menu:ip_monitor:ignore":
        await answer_callback_silently(query)
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
        await answer_callback_silently(query)
        await show_callback_page(
            query,
            ignored_rules_text_sync(cache_path),
            ignored_rules_keyboard(cache_path, context, page),
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
        await asyncio.to_thread(log_operation_from_query_with_cache, bot_ctx.cache_path, query, "ip_ignore", "解除忽略", ip_ignore_detail(dimension, value, before_values, after_values))
        await query.answer("已解除忽略")
        await show_callback_page(
            query,
            ignored_rules_text_sync(cache_path),
            ignored_rules_keyboard(cache_path, context, page),
            parse_mode="HTML",
        )
        return

    ignore_page_match = re.fullmatch(r"main_menu:ip_monitor:ignore:(area|asn|cidr):(\d+)", data)
    if ignore_page_match:
        dimension = ignore_page_match.group(1)
        page = int(ignore_page_match.group(2))
        title = {"area": "忽略地区", "asn": "忽略 ASN", "cidr": "忽略 IP"}[dimension]
        await answer_callback_silently(query)
        await show_callback_page(
            query,
            f"🚧 <b>{title}</b>\n────────────\n按已采集信息去重展示，并按最近出现时间排序。\n点击按钮可切换忽略状态；前缀 ✅ 表示已忽略。",
            ip_ignore_list_keyboard(cache_path, context, dimension, page),
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
        await asyncio.to_thread(log_operation_from_query_with_cache, bot_ctx.cache_path, query, "ip_ignore", "切换忽略", ip_ignore_detail(dimension, value, before_values, after_values))
        title = {"area": "忽略地区", "asn": "忽略 ASN", "cidr": "忽略 IP"}[dimension]
        await query.answer("已加入忽略" if enabled else "已取消忽略")
        await show_callback_page(
            query,
            f"🚧 <b>{title}</b>\n────────────\n按已采集信息去重展示，并按最近出现时间排序。\n点击按钮可切换忽略状态；前缀 ✅ 表示已忽略。",
            ip_ignore_list_keyboard(cache_path, context, dimension, page),
            parse_mode="HTML",
        )
        return

    if data == "main_menu:noop":
        await answer_callback_silently(query)
        return

    if data == "main_menu:ip_monitor:user_query":
        context.user_data["awaiting_user_ip_query_id"] = True
        context.user_data.pop("user_ip_query_period", None)
        await answer_callback_silently(query)
        await show_callback_page(
            query,
            "🔎 <b>按用户 ID 查询 IP</b>\n────────────\n请输入要查询的用户 ID，例如：1",
            InlineKeyboardMarkup([back_close_row("main_menu:ip_monitor", "⬅️ 返回 IP 监控")]),
            parse_mode="HTML",
        )
        return
    return True

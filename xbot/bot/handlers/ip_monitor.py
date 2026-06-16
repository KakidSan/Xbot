from __future__ import annotations

from ...common import ContextTypes, InlineKeyboardMarkup, Path, Update, asyncio, re
from ...config import AppConfig
from ...db.cache import (
    active_user_button_items_from_cache_sync,
    count_user_ips_from_cache_sync,
    ignored_rule_toggle_sync,
    ignored_rule_values_sync,
    ip_alert_row_for_user_sync,
    list_user_ips_from_cache_sync,
    query_user_ips_from_cache_sync,
    parse_ip_kind,
)
from ...geo import ignored_rules_text_sync
from ..context import BotContext, user_data_of
from ..formatters import format_ip_alert
from ..keyboards import (
    ignored_rules_keyboard,
    ip_alert_keyboard,
    ip_detail_list_keyboard,
    ip_ignore_list_keyboard,
    user_ip_detail_keyboard,
    user_ip_ignore_dimension_keyboard,
    user_ip_ignore_list_keyboard,
)
from ..menus import back_close_row, ip_ignore_menu_keyboard, ip_monitor_keyboard
from ..operation_details import ip_ignore_detail
from ..operation_logs import (
    log_operation_from_query as log_operation_from_query_with_cache,
)
from ..permissions import is_allowed, is_bot_self_update


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
) -> bool | None:
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
        return None

    if data == "main_menu:ip_monitor:period":
        await query.answer("正在生成查询，请稍候...")
        await open_dashboard_card(query, "ip_1h")
        return None

    if data == "main_menu:ip_monitor:ignore":
        await answer_callback_silently(query)
        await show_callback_page(
            query,
            "🚧 <b>忽略列表</b>\n────────────\n请选择维度。\n\nIPv4 段按 /24 统计；IPv6 暂不参与统计。",
            ip_ignore_menu_keyboard(),
            parse_mode="HTML",
        )
        return None

    ignored_rules_match = re.fullmatch(
        r"main_menu:ip_monitor:ignored_rules:(\d+)", data
    )
    if ignored_rules_match:
        page = int(ignored_rules_match.group(1))
        await answer_callback_silently(query)
        await show_callback_page(
            query,
            ignored_rules_text_sync(cache_path),
            ignored_rules_keyboard(cache_path, context, page),
            parse_mode="HTML",
        )
        return None

    ignored_rule_toggle_match = re.fullmatch(
        r"main_menu:ip_monitor:ignored_rule_toggle:(\d+):([A-Za-z0-9]+)", data
    )
    if ignored_rule_toggle_match:
        page = int(ignored_rule_toggle_match.group(1))
        token = ignored_rule_toggle_match.group(2)
        token_map = user_data_of(context).get("ip_ignore_tokens") or {}
        token_data = token_map.get(token) if isinstance(token_map, dict) else None
        if not token_data:
            await query.answer("请求无效，请重新进入。", show_alert=True)
            return None
        dimension = str(token_data.get("dimension") or "")
        value = str(token_data.get("value") or "")
        if dimension not in {"area", "asn", "cidr"} or not value:
            await query.answer("请求无效，请重新进入。", show_alert=True)
            return None
        before_values = await asyncio.to_thread(
            ignored_rule_values_sync, cache_path, dimension
        )
        await asyncio.to_thread(ignored_rule_toggle_sync, cache_path, dimension, value)
        after_values = await asyncio.to_thread(
            ignored_rule_values_sync, cache_path, dimension
        )
        await asyncio.to_thread(
            log_operation_from_query_with_cache,
            bot_ctx.cache_path,
            query,
            "ip_ignore",
            "解除忽略",
            ip_ignore_detail(dimension, value, before_values, after_values),
        )
        await query.answer("已解除忽略")
        await show_callback_page(
            query,
            ignored_rules_text_sync(cache_path),
            ignored_rules_keyboard(cache_path, context, page),
            parse_mode="HTML",
        )
        return None

    ignore_page_match = re.fullmatch(
        r"main_menu:ip_monitor:ignore:(area|asn|cidr):(\d+)", data
    )
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
        return None

    ignore_toggle_match = re.fullmatch(
        r"main_menu:ip_monitor:ignore_toggle:(area|asn|cidr):(\d+):([A-Za-z0-9]+)", data
    )
    if ignore_toggle_match:
        dimension = ignore_toggle_match.group(1)
        page = int(ignore_toggle_match.group(2))
        token = ignore_toggle_match.group(3)
        token_map = user_data_of(context).get("ip_ignore_tokens") or {}
        token_data = token_map.get(token) if isinstance(token_map, dict) else None
        if not token_data or token_data.get("dimension") != dimension:
            await query.answer("请求无效，请重新进入。", show_alert=True)
            return None
        value = str(token_data.get("value") or "")
        before_values = await asyncio.to_thread(
            ignored_rule_values_sync, cache_path, dimension
        )
        enabled = await asyncio.to_thread(
            ignored_rule_toggle_sync, cache_path, dimension, value
        )
        after_values = await asyncio.to_thread(
            ignored_rule_values_sync, cache_path, dimension
        )
        await asyncio.to_thread(
            log_operation_from_query_with_cache,
            bot_ctx.cache_path,
            query,
            "ip_ignore",
            "切换忽略",
            ip_ignore_detail(dimension, value, before_values, after_values),
        )
        title = {"area": "忽略地区", "asn": "忽略 ASN", "cidr": "忽略 IP"}[dimension]
        await query.answer("已加入忽略" if enabled else "已取消忽略")
        await show_callback_page(
            query,
            f"🚧 <b>{title}</b>\n────────────\n按已采集信息去重展示，并按最近出现时间排序。\n点击按钮可切换忽略状态；前缀 ✅ 表示已忽略。",
            ip_ignore_list_keyboard(cache_path, context, dimension, page),
            parse_mode="HTML",
        )
        return None

    if data == "main_menu:noop":
        await answer_callback_silently(query)
        return None

    if data == "main_menu:ip_monitor:user_query":
        user_data_of(context)["awaiting_user_ip_query_id"] = True
        user_data_of(context).pop("user_ip_query_period", None)
        await answer_callback_silently(query)
        await show_callback_page(
            query,
            "🔎 <b>按用户 ID 查询 IP</b>\n────────────\n请输入要查询的用户 ID，例如：1",
            InlineKeyboardMarkup(
                [back_close_row("main_menu:ip_monitor", "⬅️ 返回 IP 监控")]
            ),
            parse_mode="HTML",
        )
        return None
    return True


async def handle_ip_detail_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    cfg: AppConfig,
    bot_ctx: BotContext,
    cache_path: Path,
    show_initialization_gate,
    answer_callback_silently,
    show_callback_page,
    mark_no_auto_delete_message,
) -> None:
    query = update.callback_query
    if not query or not query.message:
        return None
    if not is_allowed(update, cfg):
        if is_bot_self_update(update, cfg):
            return None
        await query.answer("未授权", show_alert=True)
        return None
    if await show_initialization_gate(query):
        return None
    data = query.data or ""

    list_match = re.fullmatch(
        r"ip_detail_list:(ip_(?:1h|24h|7d|30d)|iprange_\d+_\d+):(\d+)", data
    )
    if list_match:
        kind = list_match.group(1)
        page = int(list_match.group(2))
        parsed = parse_ip_kind(kind)
        if not parsed:
            await query.answer("请求无效，请重新进入。", show_alert=True)
            return None
        label, start_ts, end_ts = parsed
        await query.answer("正在生成用户列表，请稍候...")
        user_buttons = await asyncio.to_thread(
            active_user_button_items_from_cache_sync, cache_path, None, start_ts, end_ts
        )
        overview = await asyncio.to_thread(
            list_user_ips_from_cache_sync, cache_path, label, None, start_ts, end_ts
        )
        text = f"{overview}\n\n请选择要查看的用户。"
        if not user_buttons:
            text += "\n\n暂无可查看用户。"
        await show_callback_page(
            query,
            text,
            ip_detail_list_keyboard(kind, user_buttons, page),
            parse_mode="HTML",
        )
        return None

    notice_match = re.fullmatch(r"ip_alert_notice:(\d+)", data)
    if notice_match:
        xboard_user_id = int(notice_match.group(1))
        row = await asyncio.to_thread(
            ip_alert_row_for_user_sync, cache_path, xboard_user_id
        )
        mark_no_auto_delete_message(query.message)
        await answer_callback_silently(query)
        if row:
            await show_callback_page(
                query,
                format_ip_alert(row),
                ip_alert_keyboard(row),
                parse_mode="HTML",
                auto_delete=False,
            )
        else:
            await show_callback_page(
                query,
                "✅ <b>异地登录恢复</b>\n────────────\n当前用户已不再满足异地登录告警条件。",
                InlineKeyboardMarkup(
                    [back_close_row("main_menu:ip_monitor", "⬅️ 返回 IP 监控")]
                ),
                parse_mode="HTML",
                auto_delete=False,
            )
        return None

    ignore_menu_match = re.fullmatch(
        r"ip_ignore_menu:(ip_(?:1h|24h|7d|30d)|iprange_\d+_\d+):(\d+):(\d+)(?::(alert))?",
        data,
    )
    if ignore_menu_match:
        kind = ignore_menu_match.group(1)
        xboard_user_id = int(ignore_menu_match.group(2))
        detail_page = int(ignore_menu_match.group(3))
        source = ignore_menu_match.group(4)
        if source == "alert":
            mark_no_auto_delete_message(query.message)
        await answer_callback_silently(query)
        await show_callback_page(
            query,
            "🚧 <b>忽略当前列表</b>\n────────────\n请选择要从当前活跃 IP 列表中提取的忽略类型。",
            user_ip_ignore_dimension_keyboard(
                kind, xboard_user_id, detail_page, source
            ),
            parse_mode="HTML",
            auto_delete=(source != "alert"),
        )
        return None

    ignore_page_match = re.fullmatch(
        r"ip_ignore_page:(area|asn|cidr):(ip_(?:1h|24h|7d|30d)|iprange_\d+_\d+):(\d+):(\d+):(\d+)(?::(alert))?",
        data,
    )
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
        await answer_callback_silently(query)
        await show_callback_page(
            query,
            f"🚧 <b>{title}</b>\n────────────\n已从当前页面的活跃 IP 中去重生成按钮。\n点击可切换忽略状态；前缀 ✅ 表示已忽略。",
            user_ip_ignore_list_keyboard(
                cache_path,
                context,
                dimension,
                kind,
                xboard_user_id,
                detail_page,
                list_page,
                source,
            ),
            parse_mode="HTML",
            auto_delete=(source != "alert"),
        )
        return None

    short_toggle_match = re.fullmatch(r"ip_ig_t:([A-Za-z0-9]+)", data)
    if short_toggle_match:
        route_token = short_toggle_match.group(1)
        token_map = user_data_of(context).get("ip_ignore_tokens") or {}
        route_data = token_map.get(route_token) if isinstance(token_map, dict) else None
        if not route_data:
            await query.answer("请求无效，请重新进入。", show_alert=True)
            return None
        dimension = str(route_data.get("dimension") or "")
        kind = str(route_data.get("kind") or "")
        xboard_user_id = int(route_data.get("user_id") or 0)
        detail_page = int(route_data.get("detail_page") or 0)
        list_page = int(route_data.get("list_page") or 0)
        source = str(route_data.get("source") or "") or None
        if source == "alert":
            mark_no_auto_delete_message(query.message)
        if (
            dimension not in {"area", "asn", "cidr"}
            or not parse_ip_kind(kind)
            or xboard_user_id <= 0
        ):
            await query.answer("请求无效，请重新进入。", show_alert=True)
            return None
        ignore_value = str(route_data.get("value") or "")
        before_values = await asyncio.to_thread(
            ignored_rule_values_sync, cache_path, dimension
        )
        enabled = await asyncio.to_thread(
            ignored_rule_toggle_sync, cache_path, dimension, ignore_value
        )
        after_values = await asyncio.to_thread(
            ignored_rule_values_sync, cache_path, dimension
        )
        await asyncio.to_thread(
            log_operation_from_query_with_cache,
            bot_ctx.cache_path,
            query,
            "ip_ignore",
            "切换忽略",
            ip_ignore_detail(
                dimension,
                ignore_value,
                before_values,
                after_values,
                xboard_user_id=xboard_user_id,
            ),
        )
        title = {"area": "忽略地区", "asn": "忽略 ASN", "cidr": "忽略 IP"}[dimension]
        await query.answer("已加入忽略" if enabled else "已取消忽略")
        await show_callback_page(
            query,
            f"🚧 <b>{title}</b>\n────────────\n已从当前页面的活跃 IP 中去重生成按钮。\n点击可切换忽略状态；前缀 ✅ 表示已忽略。",
            user_ip_ignore_list_keyboard(
                cache_path,
                context,
                dimension,
                kind,
                xboard_user_id,
                detail_page,
                list_page,
                source,
            ),
            parse_mode="HTML",
            auto_delete=(source != "alert"),
        )
        return None

    ignore_toggle_match = re.fullmatch(
        r"ip_ignore_toggle:(area|asn|cidr):(ip_(?:1h|24h|7d|30d)|iprange_\d+_\d+):(\d+):(\d+):(\d+):([A-Za-z0-9]+)(?::(alert))?",
        data,
    )
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
        token_map = user_data_of(context).get("ip_ignore_tokens") or {}
        token_data = token_map.get(token) if isinstance(token_map, dict) else None
        if not token_data or token_data.get("dimension") != dimension:
            await query.answer("请求无效，请重新进入。", show_alert=True)
            return None
        ignore_value = str(token_data.get("value") or "")
        before_values = await asyncio.to_thread(
            ignored_rule_values_sync, cache_path, dimension
        )
        enabled = await asyncio.to_thread(
            ignored_rule_toggle_sync, cache_path, dimension, ignore_value
        )
        after_values = await asyncio.to_thread(
            ignored_rule_values_sync, cache_path, dimension
        )
        await asyncio.to_thread(
            log_operation_from_query_with_cache,
            bot_ctx.cache_path,
            query,
            "ip_ignore",
            "切换忽略",
            ip_ignore_detail(
                dimension,
                ignore_value,
                before_values,
                after_values,
                xboard_user_id=xboard_user_id,
            ),
        )
        title = {"area": "忽略地区", "asn": "忽略 ASN", "cidr": "忽略 IP"}[dimension]
        await query.answer("已加入忽略" if enabled else "已取消忽略")
        await show_callback_page(
            query,
            f"🚧 <b>{title}</b>\n────────────\n已从当前页面的活跃 IP 中去重生成按钮。\n点击可切换忽略状态；前缀 ✅ 表示已忽略。",
            user_ip_ignore_list_keyboard(
                cache_path,
                context,
                dimension,
                kind,
                xboard_user_id,
                detail_page,
                list_page,
                source,
            ),
            parse_mode="HTML",
            auto_delete=(source != "alert"),
        )
        return None

    detail_match = re.fullmatch(
        r"ip_active_user_detail:(ip_(?:1h|24h|7d|30d)|iprange_\d+_\d+):(\d+)(?::(\d+))?(?::(alert))?",
        data,
    )
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
            return None
        label, start_ts, end_ts = parsed
        await query.answer("正在查询 IP，请稍候...")
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
        total_ips = await asyncio.to_thread(
            count_user_ips_from_cache_sync,
            cache_path,
            xboard_user_id,
            None,
            start_ts,
            end_ts,
        )
        await show_callback_page(
            query,
            result,
            user_ip_detail_keyboard(kind, xboard_user_id, total_ips, page, source),
            parse_mode="HTML",
            auto_delete=(source != "alert"),
        )
        return None

    await query.answer("请求无效，请重新进入。", show_alert=True)

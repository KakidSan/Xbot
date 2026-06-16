from __future__ import annotations

from ...common import ContextTypes, Path, Update, alert_period_label, asyncio, html, is_admin_user_id, re
from ...config import AppConfig
from ...db.cache import (
    alert_global_period_sync,
    alert_global_threshold_sync,
    alert_reset_setting_sync,
    alert_set_global_period_sync,
    alert_set_global_threshold_sync,
    alert_setting_label,
    alert_upsert_setting_sync,
    alert_user_list_sync,
    alert_user_setting_sync,
)
from ..context import BotContext
from ..formatters import alert_global_setting_text_sync, alert_summary_sync, alert_user_setting_text_sync, render_user_label
from ..keyboards import alert_global_keyboard, alert_menu_keyboard, alert_user_list_keyboard, alert_user_setting_keyboard, alert_user_setting_keyboard_for_source
from ..operation_details import alert_category, alert_setting_before_after_detail
from ..operation_logs import log_operation_from_query as log_operation_from_query_with_cache


async def handle_alert_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, *, cfg: AppConfig, bot_ctx: BotContext, cache_path: Path, show_initialization_gate, answer_callback_silently, show_callback_page, mark_no_auto_delete_message) -> None:
    query = update.callback_query
    if not query or not query.message:
        return
    if not is_allowed(update, cfg):
        if is_bot_self_update(update, cfg):
            return
        await query.answer("未授权", show_alert=True)
        return
    if await show_initialization_gate(query):
        return
    data = query.data or ""

    menu_match = re.fullmatch(r"alert_menu:(traffic|ip)", data)
    if menu_match:
        alert_type = menu_match.group(1)
        text = await asyncio.to_thread(alert_summary_sync, cache_path, alert_type)
        await answer_callback_silently(query)
        await show_callback_page(query, text, alert_menu_keyboard(alert_type), parse_mode="HTML")
        return

    users_match = re.fullmatch(r"alert_users:(traffic|ip):(\d+)", data)
    if users_match:
        alert_type = users_match.group(1)
        page = int(users_match.group(2))
        await asyncio.to_thread(upsert_all_cache_users, cache_path, cfg.mysql)
        users = await asyncio.to_thread(alert_user_list_sync, cache_path, alert_type, 10000)
        title = "用量异常" if alert_type == "traffic" else "异地登录"
        if not users:
            await answer_callback_silently(query)
            await show_callback_page(
                query,
                f"🌟 {'异常告警' if alert_type == 'traffic' else '异地登录'}<b>独立规则</b>\n────────────\n当前本地缓存中还没有用户列表。请等待后台采集完成后再试。",
                InlineKeyboardMarkup([back_close_row(f"alert_menu:{alert_type}", "⬅️ 返回")]),
                parse_mode="HTML",
            )
            return
        total_pages = max(1, (len(users) + 9) // 10)
        page = min(max(0, page), total_pages - 1)
        await answer_callback_silently(query)
        await show_callback_page(
            query,
            f"🌟 {'异常告警' if alert_type == 'traffic' else '异地登录'}<b>独立规则</b>\n────────────\n请选择用户。",
            alert_user_list_keyboard(alert_type, users, page),
            parse_mode="HTML",
        )
        return

    user_match = re.fullmatch(r"alert_user:(traffic|ip):(\d+)(?::(alert))?", data)
    if user_match:
        alert_type = user_match.group(1)
        xboard_user_id = int(user_match.group(2))
        source = user_match.group(3)
        if source == "alert":
            mark_no_auto_delete_message(query.message)
        text = await asyncio.to_thread(alert_user_setting_text_sync, cache_path, alert_type, xboard_user_id)
        await answer_callback_silently(query)
        await show_callback_page(query, text, alert_user_setting_keyboard_for_source(bot_ctx.cache_path, alert_type, xboard_user_id, source), parse_mode="HTML", auto_delete=(source != "alert"))
        return


    period_page_match = re.fullmatch(r"alert_period_page:(traffic|ip):(\d+)", data)
    if period_page_match:
        alert_type = period_page_match.group(1)
        xboard_user_id = int(period_page_match.group(2))
        title = "流量告警周期" if alert_type == "traffic" else "异地告警周期"
        await answer_callback_silently(query)
        await show_callback_page(
            query,
            f"🕒 <b>{title}</b>\n────────────\n请选择该用户的告警统计周期。",
            alert_user_period_select_keyboard(alert_type, xboard_user_id),
            parse_mode="HTML",
        )
        return

    global_period_page_match = re.fullmatch(r"alert_global_period_page:(traffic|ip)", data)
    if global_period_page_match:
        alert_type = global_period_page_match.group(1)
        title = "用量异常默认周期" if alert_type == "traffic" else "异地登录默认周期"
        await answer_callback_silently(query)
        await show_callback_page(query, f"🕒 <b>{title}</b>\n────────────\n请选择默认告警统计周期。", alert_global_period_select_keyboard(alert_type), parse_mode="HTML")
        return

    global_match = re.fullmatch(r"alert_global:(traffic|ip)(?::custom)?", data)
    if global_match:
        alert_type = global_match.group(1)
        if data.endswith(":custom"):
            context.user_data["awaiting_alert_global_custom"] = {
                "type": alert_type,
                "chat_id": query.message.chat_id,
                "message_id": query.message.message_id,
            }
            unit = "GB，例如：150" if alert_type == "traffic" else "城市数，例如：4"
            await answer_callback_silently(query)
            await show_callback_page(query, f"✍️ 请输入默认规则 ({unit})", InlineKeyboardMarkup([back_close_row(f"alert_global:{alert_type}", "⬅️ 返回")]))
            return
        text = await asyncio.to_thread(alert_global_setting_text_sync, cache_path, alert_type)
        await answer_callback_silently(query)
        await show_callback_page(query, text, alert_global_keyboard(bot_ctx.cache_path, alert_type), parse_mode="HTML")
        return

    global_period_match = re.fullmatch(r"alert_global:(traffic|ip):period:(1h|24h|7d|today|week)", data)
    if global_period_match:
        alert_type = global_period_match.group(1)
        period = global_period_match.group(2)
        before = f"{alert_period_label(await asyncio.to_thread(alert_global_period_sync, cache_path, alert_type))} / {format_bytes(await asyncio.to_thread(alert_global_threshold_sync, cache_path, alert_type)) if alert_type == 'traffic' else str(await asyncio.to_thread(alert_global_threshold_sync, cache_path, alert_type)) + ' 个城市'}"
        await asyncio.to_thread(alert_set_global_period_sync, cache_path, alert_type, period)
        after = f"{alert_period_label(period)} / {format_bytes(await asyncio.to_thread(alert_global_threshold_sync, cache_path, alert_type)) if alert_type == 'traffic' else str(await asyncio.to_thread(alert_global_threshold_sync, cache_path, alert_type)) + ' 个城市'}"
        await asyncio.to_thread(log_operation_from_query_with_cache, bot_ctx.cache_path, query, alert_category(alert_type), "调整默认周期", alert_setting_before_after_detail(alert_type, "默认规则", before, after))
        text = await asyncio.to_thread(alert_global_setting_text_sync, cache_path, alert_type)
        await query.answer("默认周期已保存")
        await show_callback_page(query, text, alert_global_keyboard(bot_ctx.cache_path, alert_type), parse_mode="HTML")
        return

    user_period_match = re.fullmatch(r"alert_set:(traffic|ip):period:(1h|24h|7d|today|week):(\d+)", data)
    if user_period_match:
        alert_type = user_period_match.group(1)
        period = user_period_match.group(2)
        xboard_user_id = int(user_period_match.group(3))
        before_setting = await asyncio.to_thread(alert_user_setting_sync, cache_path, xboard_user_id)
        before = alert_setting_label(before_setting, alert_type, cache_path)
        if alert_type == "traffic":
            await asyncio.to_thread(alert_upsert_setting_sync, cache_path, xboard_user_id, traffic_period=period, traffic_whitelist=0)
        else:
            await asyncio.to_thread(alert_upsert_setting_sync, cache_path, xboard_user_id, ip_period=period, ip_whitelist=0)
        after_setting = await asyncio.to_thread(alert_user_setting_sync, cache_path, xboard_user_id)
        after = alert_setting_label(after_setting, alert_type, cache_path)
        await asyncio.to_thread(log_operation_from_query_with_cache, bot_ctx.cache_path, query, alert_category(alert_type), "调整独立周期", alert_setting_before_after_detail(alert_type, "独立规则", before, after, xboard_user_id))
        text = await asyncio.to_thread(alert_user_setting_text_sync, cache_path, alert_type, xboard_user_id)
        await query.answer("周期已保存")
        await show_callback_page(query, text, alert_user_setting_keyboard(bot_ctx.cache_path, alert_type, xboard_user_id), parse_mode="HTML")
        return

    custom_match = re.fullmatch(r"alert_set:(traffic|ip):custom:(\d+)", data)
    if custom_match:
        alert_type = custom_match.group(1)
        xboard_user_id = int(custom_match.group(2))
        context.user_data["awaiting_alert_custom"] = {
            "type": alert_type,
            "user_id": xboard_user_id,
            "chat_id": query.message.chat_id,
            "message_id": query.message.message_id,
        }
        unit = "GB，例如：150" if alert_type == "traffic" else "城市数，例如：4"
        await answer_callback_silently(query)
        await show_callback_page(query, f"✍️ 请输入独立规则 ({unit})", InlineKeyboardMarkup([back_close_row(f"alert_user:{alert_type}:{xboard_user_id}", "⬅️ 返回")]))
        return

    threshold_match = re.fullmatch(r"alert_set:(traffic|ip):threshold:(\d+):(\d+)", data)
    if threshold_match:
        alert_type = threshold_match.group(1)
        xboard_user_id = int(threshold_match.group(2))
        value = int(threshold_match.group(3))
        before_setting = await asyncio.to_thread(alert_user_setting_sync, cache_path, xboard_user_id)
        before = alert_setting_label(before_setting, alert_type, cache_path)
        if alert_type == "traffic":
            await asyncio.to_thread(alert_upsert_setting_sync, cache_path, xboard_user_id, traffic_threshold_bytes=value * 1024 ** 3, traffic_whitelist=0)
        else:
            await asyncio.to_thread(alert_upsert_setting_sync, cache_path, xboard_user_id, ip_city_threshold=value, ip_whitelist=0)
        after_setting = await asyncio.to_thread(alert_user_setting_sync, cache_path, xboard_user_id)
        after = alert_setting_label(after_setting, alert_type, cache_path)
        await asyncio.to_thread(log_operation_from_query_with_cache, bot_ctx.cache_path, query, alert_category(alert_type), "调整独立规则", alert_setting_before_after_detail(alert_type, "独立规则", before, after, xboard_user_id))
        text = await asyncio.to_thread(alert_user_setting_text_sync, cache_path, alert_type, xboard_user_id)
        await query.answer("规则已保存")
        await show_callback_page(query, text, alert_user_setting_keyboard(bot_ctx.cache_path, alert_type, xboard_user_id), parse_mode="HTML")
        return

    white_match = re.fullmatch(r"alert_set:(traffic|ip):whitelist:(\d+)", data)
    if white_match:
        alert_type = white_match.group(1)
        xboard_user_id = int(white_match.group(2))
        setting = await asyncio.to_thread(alert_user_setting_sync, cache_path, xboard_user_id)
        before = alert_setting_label(setting, alert_type, cache_path)
        if alert_type == "traffic":
            new_value = 0 if int(setting.get("traffic_whitelist") or 0) else 1
            await asyncio.to_thread(alert_upsert_setting_sync, cache_path, xboard_user_id, traffic_whitelist=new_value)
        else:
            new_value = 0 if int(setting.get("ip_whitelist") or 0) else 1
            await asyncio.to_thread(alert_upsert_setting_sync, cache_path, xboard_user_id, ip_whitelist=new_value)
        after_setting = await asyncio.to_thread(alert_user_setting_sync, cache_path, xboard_user_id)
        after = alert_setting_label(after_setting, alert_type, cache_path)
        await asyncio.to_thread(log_operation_from_query_with_cache, bot_ctx.cache_path, query, alert_category(alert_type), "切换白名单", alert_setting_before_after_detail(alert_type, "白名单", before, after, xboard_user_id))
        text = await asyncio.to_thread(alert_user_setting_text_sync, cache_path, alert_type, xboard_user_id)
        await query.answer("白名单已更新")
        await show_callback_page(query, text, alert_user_setting_keyboard(bot_ctx.cache_path, alert_type, xboard_user_id), parse_mode="HTML")
        return

    reset_match = re.fullmatch(r"alert_set:(traffic|ip):reset:(\d+)", data)
    if reset_match:
        alert_type = reset_match.group(1)
        xboard_user_id = int(reset_match.group(2))
        before_setting = await asyncio.to_thread(alert_user_setting_sync, cache_path, xboard_user_id)
        before = alert_setting_label(before_setting, alert_type, cache_path)
        await asyncio.to_thread(alert_reset_setting_sync, cache_path, xboard_user_id, alert_type)
        after_setting = await asyncio.to_thread(alert_user_setting_sync, cache_path, xboard_user_id)
        after = alert_setting_label(after_setting, alert_type, cache_path)
        await asyncio.to_thread(log_operation_from_query_with_cache, bot_ctx.cache_path, query, alert_category(alert_type), "恢复默认规则", alert_setting_before_after_detail(alert_type, "独立规则", before, after, xboard_user_id))
        text = await asyncio.to_thread(alert_user_setting_text_sync, cache_path, alert_type, xboard_user_id)
        await query.answer("已恢复默认")
        await show_callback_page(query, text, alert_user_setting_keyboard(bot_ctx.cache_path, alert_type, xboard_user_id), parse_mode="HTML")
        return

    await query.answer("请求无效，请重新进入。", show_alert=True)

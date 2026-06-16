from __future__ import annotations

from ...common import ContextTypes, Update, asyncio, re
from ...common import is_admin_user_id
from ...config import AppConfig
from ...db.cache import operation_log_mark_read_sync
from ..context import BotContext
from ..operation_logs import (
    operation_log_detail_keyboard,
    operation_log_detail_text_sync,
    operation_log_summary_text_sync,
    operation_logs_menu_keyboard,
    operation_logs_summary_keyboard,
)


async def operation_logs_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    cfg: AppConfig,
    bot_ctx: BotContext,
    cache_path,
    data: str,
    query,
    answer_callback_silently,
    show_callback_page,
) -> bool:
    if not data.startswith("main_menu:op_logs"):
        return False
    if not is_admin_user_id(query.from_user.id, cfg):
        await query.answer("只有管理员可以查看操作日志", show_alert=True)
        return
    if data == "main_menu:op_logs":
        await answer_callback_silently(query)
        keyboard = await asyncio.to_thread(operation_logs_menu_keyboard, bot_ctx.cache_path, query.from_user.id)
        await show_callback_page(query, "📜 <b>操作日志</b>\n────────────\n请选择要查看的操作类型。\n\n按钮括号为：未读日志数量/所有日志数量。", keyboard, parse_mode="HTML")
        return
    detail_match = re.fullmatch(r"main_menu:op_logs:(traffic_alert|ip_alert|ip_ignore|reset_cache|reset_ip|auth):(\d+)", data)
    if detail_match:
        category = detail_match.group(1)
        log_id = int(detail_match.group(2))
        await asyncio.to_thread(operation_log_mark_read_sync, cache_path, query.from_user.id, log_id)
        await query.answer("已标记为已读")
        await show_callback_page(query, await asyncio.to_thread(operation_log_detail_text_sync, bot_ctx.cache_path, log_id), operation_log_detail_keyboard(category), parse_mode="HTML")
        return
    log_match = re.fullmatch(r"main_menu:op_logs:(traffic_alert|ip_alert|ip_ignore|reset_cache|reset_ip|auth)", data)
    if log_match:
        category = log_match.group(1)
        await answer_callback_silently(query)
        text = await asyncio.to_thread(operation_log_summary_text_sync, bot_ctx.cache_path, category, query.from_user.id)
        keyboard = await asyncio.to_thread(operation_logs_summary_keyboard, bot_ctx.cache_path, category, query.from_user.id)
        await show_callback_page(query, text, keyboard, parse_mode="HTML")
        return
    return True

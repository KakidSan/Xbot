from __future__ import annotations

from ...common import BadRequest, ContextTypes, Path, Update, asyncio, timedelta
from ...config import AppConfig
from ...db.cache import list_user_ips_from_cache_sync
from ..keyboards import active_users_keyboard
from .legacy import is_allowed, is_bot_self_update


async def handle_close_message_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, *, cfg: AppConfig) -> None:
    query = update.callback_query
    if not query or not query.message:
        return
    if not is_allowed(update, cfg):
        if is_bot_self_update(update, cfg):
            return
        await query.answer("未授权", show_alert=True)
        return
    await query.answer("已关闭")
    try:
        await query.message.delete()
    except BadRequest:
        pass

async def handle_detail_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, *, cfg: AppConfig, cache_path: Path, answer_callback_silently, show_callback_page) -> None:
    query = update.callback_query
    if not query or not query.message:
        return
    if not is_allowed(update, cfg):
        if is_bot_self_update(update, cfg):
            return
        await query.answer("未授权", show_alert=True)
        return
    periods = {
        "1h": ("近 1 小时", timedelta(hours=1)),
        "24h": ("近 24 小时", timedelta(hours=24)),
        "7d": ("近 7 天", timedelta(days=7)),
        "30d": ("近 30 天", timedelta(days=30)),
    }
    target = (query.data or "").split(":", 1)[-1]
    await answer_callback_silently(query)
    if target in periods:
        label, window = periods[target]
        result = await asyncio.to_thread(list_user_ips_from_cache_sync, cache_path, label, window)
        await show_callback_page(query, result, active_users_keyboard(target), parse_mode="HTML")
        return
    await show_callback_page(query, "🌐 请选择在线记录统计周期：", active_users_keyboard())

from __future__ import annotations

from ..context import user_data_of
from ...common import (
    BadRequest,
    ContextTypes,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Path,
    Update,
    asyncio,
    is_admin_user_id,
    log,
)
from ...config import AppConfig
from ...db.cache import initialization_progress_text_sync, initialization_status_sync
from ..formatters import bot_health_overview_text_sync, bot_status_text_sync
from ..keyboards import active_users_keyboard, traffic_period_keyboard
from ..menus import clear_history_confirm_keyboard
from ..message_utils import edit_or_replace_status, reply_connection_status
from ..permissions import is_allowed, is_bot_self_update


async def handle_start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    cfg: AppConfig,
    reply_main_menu,
    delete_trigger_command_message,
) -> None:
    await reply_main_menu(update, context, cfg)
    await delete_trigger_command_message(update)


async def handle_clear_history_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    cfg: AppConfig,
    track_auto_delete_message,
) -> None:
    if not update.effective_message:
        return None
    if not is_allowed(update, cfg):
        if is_bot_self_update(update, cfg):
            return
        await reply_connection_status(update, cfg)
        return None
    sent = await update.effective_message.reply_text(
        "👋🏻 <b>清除对话记录</b>\n────────────\n将尝试清空当前对话记录。\n此操作不可恢复。\n\n⚠️ 确认要继续吗？",
        parse_mode="HTML",
        reply_markup=clear_history_confirm_keyboard(),
    )
    await track_auto_delete_message(sent)


async def handle_status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    cfg: AppConfig,
    cache_path: Path,
    track_auto_delete_message,
) -> None:
    if not update.effective_message:
        return None
    if not is_allowed(update, cfg):
        if is_bot_self_update(update, cfg):
            return
        await reply_connection_status(update, cfg)
        return None
    text = await asyncio.to_thread(bot_status_text_sync, cfg, cache_path)
    sent = await update.effective_message.reply_text(text, parse_mode="HTML")
    await track_auto_delete_message(sent)


async def handle_health_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    cfg: AppConfig,
    cache_path: Path,
    track_auto_delete_message,
    reply_long_text,
) -> None:
    if not update.effective_message:
        return None
    if not is_allowed(update, cfg):
        if is_bot_self_update(update, cfg):
            return
        await reply_connection_status(update, cfg)
        return None
    status_message = await update.effective_message.reply_text(
        "正在执行健康检查，请稍候..."
    )
    await track_auto_delete_message(status_message)
    admin_view = is_admin_user_id(
        update.effective_user.id if update.effective_user else None, cfg
    )
    text = await asyncio.to_thread(
        bot_health_overview_text_sync, cfg, cache_path, admin_view
    )
    if len(text) <= 3900:
        await edit_or_replace_status(status_message, text, update, parse_mode="HTML")
        await track_auto_delete_message(status_message)
    else:
        try:
            await status_message.delete()
        except BadRequest as exc:
            log.debug("删除状态消息失败：%s", exc)
        await reply_long_text(update.effective_message, text, parse_mode="HTML")


async def handle_active_users_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    cfg: AppConfig,
    cache_path: Path,
    track_auto_delete_message,
) -> None:
    if not update.effective_message:
        return None
    if not is_allowed(update, cfg):
        if is_bot_self_update(update, cfg):
            return
        await reply_connection_status(update, cfg)
        return None
    init_status = await asyncio.to_thread(
        initialization_status_sync, cache_path, cfg.ip_geo_queries_per_minute
    )
    if init_status.get("initializing"):
        sent = await update.effective_message.reply_text(
            await asyncio.to_thread(initialization_progress_text_sync, cache_path, cfg),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔄 刷新初始化进度", callback_data="main_menu")]]
            ),
        )
        await track_auto_delete_message(sent)
        return None
    sent = await update.effective_message.reply_text(
        "🌐 请选择在线记录统计周期：", reply_markup=active_users_keyboard()
    )
    await track_auto_delete_message(sent)


async def handle_user_ip_query_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    cfg: AppConfig,
    track_auto_delete_message,
) -> None:
    if not update.effective_message:
        return None
    if not is_allowed(update, cfg):
        if is_bot_self_update(update, cfg):
            return
        await reply_connection_status(update, cfg)
        return None
    user_data_of(context)["awaiting_user_ip_query_id"] = True
    user_data_of(context).pop("user_ip_query_period", None)
    sent = await update.effective_message.reply_text(
        "🔎 请输入要查询的用户 ID，例如：1"
    )
    await track_auto_delete_message(sent)


async def handle_traffic_daily_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    cfg: AppConfig,
    track_auto_delete_message,
) -> None:
    if not update.effective_message:
        return None
    if not is_allowed(update, cfg):
        if is_bot_self_update(update, cfg):
            return
        await reply_connection_status(update, cfg)
        return None
    sent = await update.effective_message.reply_text(
        "🌊 请选择统计周期：", reply_markup=traffic_period_keyboard()
    )
    await track_auto_delete_message(sent)


async def handle_traffic_users_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    cfg: AppConfig,
    send_or_jump_traffic_dashboard,
) -> None:
    if not update.effective_message:
        return None
    if not is_allowed(update, cfg):
        if is_bot_self_update(update, cfg):
            return
        await reply_connection_status(update, cfg)
        return None
    await send_or_jump_traffic_dashboard(update.effective_message, "users_preset_24h")


async def handle_traffic_nodes_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    cfg: AppConfig,
    send_or_jump_traffic_dashboard,
) -> None:
    if not update.effective_message:
        return None
    if not is_allowed(update, cfg):
        if is_bot_self_update(update, cfg):
            return
        await reply_connection_status(update, cfg)
        return None
    await send_or_jump_traffic_dashboard(update.effective_message, "nodes_preset_24h")

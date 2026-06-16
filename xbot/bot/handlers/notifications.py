from __future__ import annotations

from ...common import (
    ContextTypes,
    NOTIFICATION_KINDS,
    Update,
    asyncio,
    is_admin_user_id,
)
from ...config import AppConfig
from ...db.cache import notification_toggle_sync
from ..callback_data import normalize_main_menu_callback
from ..context import BotContext
from ..formatters import notification_ip_alert_mode_label
from ..keyboards import notification_push_keyboard
from ..permissions import is_allowed, is_bot_self_update


async def handle_notifications_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    cfg: AppConfig,
    bot_ctx: BotContext,
    answer_callback_silently,
    show_callback_page,
) -> None:
    query = update.callback_query
    if not query or not query.message:
        return None
    if not is_allowed(update, cfg):
        if is_bot_self_update(update, cfg):
            return None
        await query.answer("未授权，无法使用该功能", show_alert=True)
        return None

    data = normalize_main_menu_callback(query.data or "")
    if data == "notify":
        await answer_callback_silently(query)
        await _show_notifications_page(query, cfg, bot_ctx, show_callback_page)
        return None

    prefix = "notify:"
    if not data.startswith(prefix):
        await query.answer("该入口暂未开放", show_alert=True)
        return None
    kind = data[len(prefix) :]
    if kind not in NOTIFICATION_KINDS:
        await query.answer("该入口暂未开放", show_alert=True)
        return None
    if kind == "version_update" and not is_admin_user_id(query.from_user.id, cfg):
        await query.answer("只有管理员可以设置版本更新推送", show_alert=True)
        return None
    chat_id = _chat_id_of(query)
    result = await asyncio.to_thread(
        notification_toggle_sync, bot_ctx.cache_path, chat_id, kind
    )
    label = NOTIFICATION_KINDS[kind]
    if kind == "ip_alert":
        await query.answer(
            f"异地登录已切换为{notification_ip_alert_mode_label(str(result))}通知"
        )
    else:
        await query.answer(f"{label}已{'开启' if result else '关闭'}推送")
    await _show_notifications_page(query, cfg, bot_ctx, show_callback_page)


async def _show_notifications_page(
    query, cfg: AppConfig, bot_ctx: BotContext, show_callback_page
) -> None:
    chat_id = _chat_id_of(query)
    await show_callback_page(
        query,
        "💬 <b>通知推送</b>\n────────────\n流量报表生成时间：北京时间 00:00\n版本更新检查时间：北京时间 12:00\n\n",
        notification_push_keyboard(
            bot_ctx.cache_path, chat_id, is_admin_user_id(query.from_user.id, cfg)
        ),
        parse_mode="HTML",
    )


def _chat_id_of(query) -> str:
    return (
        str(query.message.chat_id)
        if query.message and hasattr(query.message, "chat_id")
        else ""
    )

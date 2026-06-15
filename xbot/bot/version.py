from __future__ import annotations

from collections.abc import Awaitable, Callable

from ..common import (
    Any,
    ContextTypes,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    asyncio,
    html,
    is_admin_user_id,
    re,
)
from ..updater import (
    read_app_version,
    start_background_update_sync,
    update_confirm_keyboard,
    update_started_text,
    version_check_sync,
    version_keyboard,
    version_text,
)
from .context import BotContext

ReplyCoverCard = Callable[[Update, ContextTypes.DEFAULT_TYPE, str], Awaitable[Any]]
EditOrReplaceStatus = Callable[..., Awaitable[None]]
DeleteTriggerCommandMessage = Callable[[Update], Awaitable[None]]
ReplyConnectionStatus = Callable[[Update, Any], Awaitable[None]]
ShowCallbackPage = Callable[..., Awaitable[None]]
AnswerCallbackSilently = Callable[[Any], Awaitable[None]]


def _user_id(update: Update) -> int | None:
    return update.effective_user.id if update.effective_user else None


def _bot_id_from_token(token: str) -> int | None:
    match = re.match(r"^(\d+):", token.strip())
    return int(match.group(1)) if match else None


def _is_bot_self_update(update: Update, bot_ctx: BotContext) -> bool:
    user = update.effective_user
    if not user:
        return False
    token_bot_id = _bot_id_from_token(bot_ctx.cfg.telegram.bot_token)
    return bool(getattr(user, "is_bot", False)) or (token_bot_id is not None and user.id == token_bot_id)


def _is_allowed(update: Update, bot_ctx: BotContext) -> bool:
    uid = _user_id(update)
    return uid is not None and uid in bot_ctx.cfg.telegram.allowed_user_ids


async def version_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    bot_ctx: BotContext,
    reply_cover_card: ReplyCoverCard,
    edit_or_replace_status_any: EditOrReplaceStatus,
    delete_trigger_command_message: DeleteTriggerCommandMessage,
    reply_connection_status: ReplyConnectionStatus,
) -> None:
    if not update.effective_message:
        return
    if not _is_allowed(update, bot_ctx):
        if _is_bot_self_update(update, bot_ctx):
            return
        await reply_connection_status(update, bot_ctx.cfg)
        return
    is_admin = is_admin_user_id(_user_id(update), bot_ctx.cfg)
    if is_admin:
        status_message = await reply_cover_card(update, context, "正在检查版本更新，请稍候...")
        check = await asyncio.to_thread(version_check_sync)
    else:
        status_message = await reply_cover_card(update, context, "正在读取当前版本，请稍候...")
        check = {"current": read_app_version()}
    await edit_or_replace_status_any(
        status_message,
        version_text(check, admin_view=is_admin),
        update,
        parse_mode="HTML",
        reply_markup=version_keyboard(check, admin_view=is_admin),
    )
    await delete_trigger_command_message(update)


async def version_update_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    bot_ctx: BotContext,
    show_callback_page: ShowCallbackPage,
    answer_callback_silently: AnswerCallbackSilently,
) -> None:
    query = update.callback_query
    if not query or not query.message:
        return
    if not _is_allowed(update, bot_ctx):
        await query.answer("未授权，无法使用该功能", show_alert=True)
        return
    if not is_admin_user_id(query.from_user.id, bot_ctx.cfg):
        await query.answer("只有管理员可以执行版本更新", show_alert=True)
        return
    data = query.data or ""
    if data == "version_update:cancel":
        await query.answer("已取消更新")
        check = await asyncio.to_thread(version_check_sync)
        await show_callback_page(query, version_text(check, admin_view=True), version_keyboard(check, admin_view=True), parse_mode="HTML")
        return
    match = re.fullmatch(r"version_update:start:(v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)", data)
    if match:
        target = match.group(1)
        await answer_callback_silently(query)
        await show_callback_page(query, update_started_text(target), update_confirm_keyboard(target), parse_mode="HTML")
        return
    match = re.fullmatch(r"version_update:confirm:(v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)", data)
    if match:
        target = match.group(1)
        await query.answer("后台更新已启动")
        ok, message = await asyncio.to_thread(start_background_update_sync, target, str(query.message.chat_id))
        if ok:
            await show_callback_page(
                query,
                "⬆️ <b>后台更新已启动</b>\n────────────\n"
                f"目标版本：<code>{html.escape(target)}</code>\n\n"
                "更新过程会在后台执行，Bot 可能会短暂离线。\n"
                "更新成功或失败后，我会主动推送结果通知。",
                InlineKeyboardMarkup([[InlineKeyboardButton("❌ 关闭", callback_data="close_message")]]),
                parse_mode="HTML",
            )
        else:
            await show_callback_page(
                query,
                "❌ <b>无法启动后台更新</b>\n────────────\n" + html.escape(message),
                version_keyboard(await asyncio.to_thread(version_check_sync), admin_view=True),
                parse_mode="HTML",
            )
        return
    await query.answer("请求无效，请重新进入。", show_alert=True)

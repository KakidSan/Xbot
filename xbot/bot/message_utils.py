from __future__ import annotations

from ..common import BadRequest, InlineKeyboardMarkup, ReplyKeyboardRemove, Update, log
from ..config import AppConfig
from .formatters import user_display
from .permissions import is_allowed, is_bot_self_update, user_id


async def reply_connection_status(update: Update, cfg: AppConfig) -> None:
    if not update.effective_message:
        return

    uid = user_id(update)
    if not is_allowed(update, cfg):
        if is_bot_self_update(update, cfg):
            log.info("忽略 Bot 自身更新：%s", user_display(update))
            return
        log.warning("拒绝未授权 Telegram 用户：%s", user_display(update))
        await update.effective_message.reply_html(
            "❌ 连接失败：你的 Telegram 用户 ID 不在白名单中。\n"
            f"你的 ID：<code>{uid or 'unknown'}</code>\n"
            "请联系管理员授权后再重试。",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    log.info("授权 Telegram 用户连接成功：%s", user_display(update))
    await update.effective_message.reply_text(
        "✅ 连接成功。\n"
        "你的 Telegram 用户 ID 已通过白名单校验，Bot 当前在线。\n\n"
        "可点击左下角菜单使用功能。",
        reply_markup=ReplyKeyboardRemove(),
    )


async def edit_or_replace_status(
    status_message,
    result: str,
    update: Update,
    parse_mode: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Prefer editing the waiting message; fall back to replace if Telegram refuses."""
    try:
        await status_message.edit_text(
            result, parse_mode=parse_mode, reply_markup=reply_markup
        )
    except BadRequest as exc:
        log.warning("编辑测试消息失败，改为删除后重新发送：%s", exc)
        try:
            await status_message.delete()
        except BadRequest:
            pass
        if update.effective_message:
            await update.effective_message.reply_text(
                result, parse_mode=parse_mode, reply_markup=reply_markup
            )


async def edit_or_replace_status_any(
    status_message,
    result: str,
    update: Update,
    parse_mode: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Edit either a text status message or a photo-caption status card."""
    try:
        if getattr(status_message, "caption", None) is not None:
            await status_message.edit_caption(
                caption=result, parse_mode=parse_mode, reply_markup=reply_markup
            )
        else:
            await status_message.edit_text(
                result, parse_mode=parse_mode, reply_markup=reply_markup
            )
    except BadRequest as exc:
        log.warning("编辑状态消息失败，改为删除后重新发送：%s", exc)
        try:
            await status_message.delete()
        except BadRequest:
            pass
        if update.effective_message:
            await update.effective_message.reply_text(
                result, parse_mode=parse_mode, reply_markup=reply_markup
            )


__all__ = [
    "edit_or_replace_status",
    "edit_or_replace_status_any",
    "reply_connection_status",
]

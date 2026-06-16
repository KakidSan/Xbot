from __future__ import annotations

from ...common import ContextTypes, Path, ReplyKeyboardRemove, Update, asyncio, html, is_admin_user_id
from ...config import AppConfig
from ...db.cache import (
    alert_global_threshold_sync,
    alert_set_global_threshold_sync,
    alert_upsert_setting_sync,
    cache_retention_days_sync,
    operation_log_add_sync,
    query_user_ips_from_cache_sync,
    save_traffic_range_sync,
    ui_pref_set_sync,
)
from ..context import BotContext
from ..formatters import format_bytes, user_display
from ..keyboards import alert_global_keyboard, alert_user_setting_keyboard, traffic_custom_keyboard_for_state, user_ip_query_page_keyboard
from ..operation_details import alert_category, alert_setting_before_after_detail
from ..operation_logs import log_operation_from_update as log_operation_from_update_with_cache
from ..permissions import is_allowed, is_bot_self_update


async def handle_fallback_message(update: Update, context: ContextTypes.DEFAULT_TYPE, *, cfg: AppConfig, bot_ctx: BotContext, cache_path: Path, track_auto_delete_message, reply_cover_card, resolve_telegram_user_label, context_bot_delete_message, edit_global_alert_prompt, edit_alert_prompt) -> None:
    if not update.effective_message:
        return

    async def reply_and_track(text: str, **kwargs: Any) -> None:
        sent = await update.effective_message.reply_text(text, **kwargs)
        await track_auto_delete_message(sent)

    if context.user_data.get("awaiting_auth_add_user_id"):
        if not is_admin_user_id(user_id(update), cfg):
            context.user_data.pop("awaiting_auth_add_user_id", None)
            await reply_connection_status(update, cfg)
            return
        text = (update.effective_message.text or "").strip()
        if not re.fullmatch(r"\d+", text):
            await reply_cover_card(
                update,
                context,
                "🔐 <b>增加授权</b>\n────────────\nTelegram 用户 ID 必须是纯数字，请重新输入；或发送 /start 取消。",
                InlineKeyboardMarkup([back_close_row("main_menu:auth", "⬅️ 返回授权管理")]),
            )
            return
        target_uid = int(text)
        try:
            if target_uid in cfg.telegram.admin_user_ids:
                await reply_cover_card(
                    update,
                    context,
                    "🔐 <b>增加授权</b>\n────────────\n该用户已是管理员，无需重复授权。",
                    authorization_manage_keyboard_for_cfg(is_super_admin_user_id(user_id(update), bot_ctx.cfg)),
                )
                return
            label = await resolve_telegram_user_label(target_uid)
            before_users = sorted(cfg.telegram.authorized_user_ids)
            new_users = await asyncio.to_thread(update_authorized_users_in_cache_sync, cache_path, cfg.telegram.super_admin_user_ids, cfg.telegram.manager_user_ids, cfg.telegram.authorized_user_ids, target_uid, None)
            cfg.telegram.authorized_user_ids = new_users
            after_users = sorted(new_users)
            await asyncio.to_thread(log_operation_from_update_with_cache, bot_ctx.cache_path, update, "auth", "增加授权", auth_change_detail([], [], before_users, after_users, added_user_id=target_uid))
        except ValueError as exc:
            await reply_cover_card(
                update,
                context,
                f"🔐 <b>增加授权</b>\n────────────\n{html.escape(str(exc))}",
                authorization_manage_keyboard_for_cfg(is_super_admin_user_id(user_id(update), bot_ctx.cfg)),
            )
            return
        except Exception as exc:
            log.exception("写入授权用户失败：%s", exc)
            await reply_cover_card(
                update,
                context,
                "🔐 <b>增加授权</b>\n────────────\n写入授权失败，请检查运行状态。",
                authorization_manage_keyboard_for_cfg(is_super_admin_user_id(user_id(update), bot_ctx.cfg)),
            )
            return
        context.user_data.pop("awaiting_auth_add_user_id", None)
        await reply_cover_card(
            update,
            context,
            f"✅ <b>已增加授权</b>\n────────────\n{html.escape(label)} (<code>{target_uid}</code>)\n变更已保存。",
            authorization_manage_keyboard_for_cfg(is_super_admin_user_id(user_id(update), bot_ctx.cfg)),
        )
        try:
            await context_bot_delete_message(update.effective_message.chat_id, update.effective_message.message_id)
        except Exception:
            pass
        return

    if context.user_data.get("awaiting_custom_cover"):
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                return
            context.user_data.pop("awaiting_custom_cover", None)
            await reply_connection_status(update, cfg)
            return
        photos = update.effective_message.photo or []
        if not photos:
            await reply_and_track(
                "请发送一张图片作为题图；或点击 /start 返回主菜单。",
                reply_markup=cover_config_keyboard(),
            )
            return
        uid = user_id(update)
        if uid is None:
            await reply_and_track("无法识别你的 Telegram 用户 ID，请重新 /start。")
            return
        file_id = photos[-1].file_id
        await asyncio.to_thread(ui_pref_set_sync, cache_path, uid, "cover_file_id", file_id)
        context.user_data.pop("awaiting_custom_cover", None)
        await reply_and_track(
            "✅ 自定题图已保存。\n之后你打开 /start 时会优先显示这张题图。",
            reply_markup=parameter_config_keyboard(),
        )
        return

    if context.user_data.get("awaiting_custom_nickname"):
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                return
            context.user_data.pop("awaiting_custom_nickname", None)
            await reply_connection_status(update, cfg)
            return
        text = (update.effective_message.text or "").strip()
        if not text:
            await reply_and_track(
                "请发送文字昵称；或点击 /start 返回主菜单。",
                reply_markup=nickname_config_keyboard(),
            )
            return
        if len(text) > 32:
            await reply_and_track("昵称最多 32 个字符，请重新发送。")
            return
        uid = user_id(update)
        if uid is None:
            await reply_and_track("无法识别你的 Telegram 用户 ID，请重新 /start。")
            return
        await asyncio.to_thread(ui_pref_set_sync, cache_path, uid, "nickname", text)
        context.user_data.pop("awaiting_custom_nickname", None)
        await reply_and_track(
            f"✅ 自定昵称已保存：{text}\n之后你打开 /start 时会显示这个昵称。",
            reply_markup=parameter_config_keyboard(),
        )
        return

    if context.user_data.get("awaiting_alert_global_custom"):
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                return
            context.user_data.pop("awaiting_alert_global_custom", None)
            await reply_connection_status(update, cfg)
            return
        custom = context.user_data.get("awaiting_alert_global_custom")
        if isinstance(custom, dict):
            alert_type = str(custom.get("type") or "")
            chat_id = custom.get("chat_id")
            message_id = custom.get("message_id")
        else:
            alert_type = str(custom or "")
            chat_id = None
            message_id = None
        text = (update.effective_message.text or "").strip()

        async def edit_global_alert_prompt(message_text: str, keyboard: InlineKeyboardMarkup | None = None, parse_mode: str | None = "HTML") -> None:
            if chat_id and message_id:
                try:
                    await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=message_text, parse_mode=parse_mode, reply_markup=keyboard)
                    return
                except BadRequest as exc:
                    log.warning("编辑全局告警规则原文本消息失败：%s", exc)
                try:
                    await context.bot.edit_message_caption(chat_id=chat_id, message_id=message_id, caption=message_text, parse_mode=parse_mode, reply_markup=keyboard)
                    return
                except BadRequest as exc:
                    log.warning("编辑全局告警规则原图片说明失败：%s", exc)
            try:
                await update.effective_message.edit_text(message_text, parse_mode=parse_mode, reply_markup=keyboard)
            except BadRequest as exc:
                log.warning("编辑默认规则输入消息失败：%s", exc)

        if not re.fullmatch(r"\d+", text):
            unit = "GB，例如：150" if alert_type == "traffic" else "城市数，例如：4"
            await edit_global_alert_prompt(f"✍️ 请输入默认规则 ({unit})\n\n⚠️ 默认规则必须是正整数，请重新输入。", InlineKeyboardMarkup([back_close_row(f"alert_global:{alert_type}", "⬅️ 返回")]), None)
            return
        value = int(text)
        if value <= 0:
            unit = "GB，例如：150" if alert_type == "traffic" else "城市数，例如：4"
            await edit_global_alert_prompt(f"✍️ 请输入默认规则 ({unit})\n\n⚠️ 默认规则必须大于 0，请重新输入。", InlineKeyboardMarkup([back_close_row(f"alert_global:{alert_type}", "⬅️ 返回")]), None)
            return
        context.user_data.pop("awaiting_alert_global_custom", None)
        if alert_type not in {"traffic", "ip"}:
            await edit_global_alert_prompt("设置类型无效，请从菜单重新进入。", None, None)
            return
        input_chat_id = update.effective_message.chat_id
        input_message_id = update.effective_message.message_id
        before_period = await asyncio.to_thread(alert_global_period_sync, cache_path, alert_type)
        before_threshold = await asyncio.to_thread(alert_global_threshold_sync, cache_path, alert_type)
        before = f"{alert_period_label(before_period)} / {format_bytes(before_threshold) if alert_type == 'traffic' else str(before_threshold) + ' 个城市'}"
        await asyncio.to_thread(alert_set_global_threshold_sync, cache_path, alert_type, value)
        after_threshold = await asyncio.to_thread(alert_global_threshold_sync, cache_path, alert_type)
        after = f"{alert_period_label(before_period)} / {format_bytes(after_threshold) if alert_type == 'traffic' else str(after_threshold) + ' 个城市'}"
        await asyncio.to_thread(log_operation_from_update_with_cache, bot_ctx.cache_path, update, alert_category(alert_type), "调整默认规则", alert_setting_before_after_detail(alert_type, "默认规则", before, after))
        result = await asyncio.to_thread(alert_global_setting_text_sync, cache_path, alert_type)
        await edit_global_alert_prompt(result, alert_global_keyboard(bot_ctx.cache_path, alert_type), "HTML")
        await context_bot_delete_message(input_chat_id, input_message_id)
        return

    if context.user_data.get("awaiting_alert_custom"):
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                return
            context.user_data.pop("awaiting_alert_custom", None)
            await reply_connection_status(update, cfg)
            return
        custom = context.user_data.get("awaiting_alert_custom") or {}
        alert_type = str(custom.get("type") or "")
        xboard_user_id = int(custom.get("user_id") or 0)
        text = (update.effective_message.text or "").strip()
        chat_id = custom.get("chat_id")
        message_id = custom.get("message_id")

        async def edit_alert_prompt(message_text: str, keyboard: InlineKeyboardMarkup | None = None, parse_mode: str | None = "HTML") -> None:
            if chat_id and message_id:
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=message_text,
                        parse_mode=parse_mode,
                        reply_markup=keyboard,
                    )
                    return
                except BadRequest as exc:
                    log.warning("编辑告警规则原文本消息失败：%s", exc)
                try:
                    await context.bot.edit_message_caption(
                        chat_id=chat_id,
                        message_id=message_id,
                        caption=message_text,
                        parse_mode=parse_mode,
                        reply_markup=keyboard,
                    )
                    return
                except BadRequest as exc:
                    log.warning("编辑告警规则原图片说明失败：%s", exc)
            # Do not create a new bot result message for threshold input. If the
            # original prompt is no longer editable, answer by editing the user's
            # input message as a last resort.
            try:
                await update.effective_message.edit_text(message_text, parse_mode=parse_mode, reply_markup=keyboard)
            except BadRequest as exc:
                log.warning("编辑用户规则输入消息失败：%s", exc)

        if not re.fullmatch(r"\d+", text):
            unit = "GB，例如：150" if alert_type == "traffic" else "城市数，例如：4"
            await edit_alert_prompt(
                f"✍️ 请输入独立规则 ({unit})\n\n⚠️ 规则必须是正整数，请重新输入。",
                InlineKeyboardMarkup([back_close_row(f"alert_user:{alert_type}:{xboard_user_id}", "⬅️ 返回")]),
                None,
            )
            return
        value = int(text)
        if value <= 0:
            unit = "GB，例如：150" if alert_type == "traffic" else "城市数，例如：4"
            await edit_alert_prompt(
                f"✍️ 请输入独立规则 ({unit})\n\n⚠️ 规则必须大于 0，请重新输入。",
                InlineKeyboardMarkup([back_close_row(f"alert_user:{alert_type}:{xboard_user_id}", "⬅️ 返回")]),
                None,
            )
            return
        context.user_data.pop("awaiting_alert_custom", None)
        input_chat_id = update.effective_message.chat_id
        input_message_id = update.effective_message.message_id
        before_setting = await asyncio.to_thread(alert_user_setting_sync, cache_path, xboard_user_id)
        before = alert_setting_label(before_setting, alert_type, cache_path)
        if alert_type == "traffic":
            await asyncio.to_thread(alert_upsert_setting_sync, cache_path, xboard_user_id, traffic_threshold_bytes=value * 1024 ** 3, traffic_whitelist=0)
        elif alert_type == "ip":
            await asyncio.to_thread(alert_upsert_setting_sync, cache_path, xboard_user_id, ip_city_threshold=value, ip_whitelist=0)
        else:
            await edit_alert_prompt("设置类型无效，请从菜单重新进入。", None, None)
            return
        after_setting = await asyncio.to_thread(alert_user_setting_sync, cache_path, xboard_user_id)
        after = alert_setting_label(after_setting, alert_type, cache_path)
        await asyncio.to_thread(log_operation_from_update_with_cache, bot_ctx.cache_path, update, alert_category(alert_type), "调整独立规则", alert_setting_before_after_detail(alert_type, "独立规则", before, after, xboard_user_id))
        result = await asyncio.to_thread(alert_user_setting_text_sync, cache_path, alert_type, xboard_user_id)
        await edit_alert_prompt(result, alert_user_setting_keyboard(bot_ctx.cache_path, alert_type, xboard_user_id), "HTML")
        await context_bot_delete_message(input_chat_id, input_message_id)
        return

    if context.user_data.get("awaiting_user_ip_query_id"):
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                return
            context.user_data.pop("awaiting_user_ip_query_id", None)
            context.user_data.pop("user_ip_query_period", None)
            await reply_connection_status(update, cfg)
            return
        text = (update.effective_message.text or "").strip()
        if not re.fullmatch(r"\d+", text):
            await reply_and_track("用户 ID 必须是数字，请重新输入；或发送 /start 取消。")
            return
        context.user_data.pop("awaiting_user_ip_query_id", None)
        period_key = context.user_data.pop("user_ip_query_period", None)
        xboard_user_id = int(text)
        periods = {
            "1h": ("近 1 小时", timedelta(hours=1)),
            "24h": ("近 24 小时", timedelta(hours=24)),
            "7d": ("近 7 天", timedelta(days=7)),
            "30d": ("近 30 天", timedelta(days=30)),
        }
        label, window = periods.get(period_key, (None, None))
        start_ts = end_ts = None
        if period_key and period_key.startswith("custom:"):
            _, start_text, end_text = period_key.split(":", 2)
            start_ts = int(start_text)
            end_ts = int(end_text)
            label = "自定区间"
            window = None
        status_message = await update.effective_message.reply_text("正在读取缓存查询该用户近期活跃 IP，请稍候...")
        await track_auto_delete_message(status_message)
        result = await asyncio.to_thread(query_user_ips_from_cache_sync, cache_path, xboard_user_id, label, window, start_ts, end_ts, 0, 10)
        total_ips = await asyncio.to_thread(count_user_ips_from_cache_sync, cache_path, xboard_user_id, window, start_ts, end_ts)
        await edit_or_replace_status(
            status_message,
            result,
            update,
            parse_mode="HTML",
            reply_markup=user_ip_query_page_keyboard(period_key, xboard_user_id, total_ips, 0),
        )
        return

    await reply_connection_status(update, cfg)




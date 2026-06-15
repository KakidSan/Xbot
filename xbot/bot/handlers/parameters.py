from __future__ import annotations

from ...common import CACHE_RETENTION_OPTIONS, ContextTypes, Path, Update, asyncio, html, re
from ...config import AppConfig
from ...db.cache import cache_retention_preview_sync, cache_retention_set_and_prune_sync, ui_pref_delete_sync
from ..context import BotContext
from ..keyboards import cache_retention_confirm_keyboard, cache_retention_keyboard
from ..menus import cover_config_keyboard, nickname_config_keyboard, parameter_config_keyboard
from ..operation_logs import log_operation_from_query as log_operation_from_query_with_cache


async def parameter_callback(
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
    cache_retention_text_sync,
    cache_retention_preview_text,
) -> bool:
    if not data.startswith("main_menu:parameter_config"):
        return False
    if data == "main_menu:parameter_config":
        await answer_callback_silently(query)
        await show_callback_page(query, "🎨 参数配置\n────────────\n请选择要配置的面板参数。", parameter_config_keyboard())
        return

    if data == "main_menu:parameter_config:cache_retention":
        await answer_callback_silently(query)
        await show_callback_page(query, cache_retention_text_sync(), cache_retention_keyboard(bot_ctx.cache_path), parse_mode="HTML")
        return

    retention_select_match = re.fullmatch(r"main_menu:parameter_config:cache_retention_select:(1m|1q|1y|all)", data)
    if retention_select_match:
        option_key = retention_select_match.group(1)
        days, _ = CACHE_RETENTION_OPTIONS[option_key]
        preview = await asyncio.to_thread(cache_retention_preview_sync, cache_path, days)
        await answer_callback_silently(query)
        await show_callback_page(query, cache_retention_preview_text(option_key, preview), cache_retention_confirm_keyboard(option_key), parse_mode="HTML")
        return

    retention_confirm_match = re.fullmatch(r"main_menu:parameter_config:cache_retention_confirm:(1m|1q|1y|all)", data)
    if retention_confirm_match:
        option_key = retention_confirm_match.group(1)
        days, label = CACHE_RETENTION_OPTIONS[option_key]
        stats = await asyncio.to_thread(cache_retention_set_and_prune_sync, cache_path, days)
        await asyncio.to_thread(
            log_operation_from_query_with_cache,
            bot_ctx.cache_path,
            query,
            "parameter_config",
            "调整缓存保留时间",
            f"设置：{label}\n活跃 IP 记录：{stats['active_ip_records']} 条\nIP 归属地缓存：{stats['ip_geo_cache']} 条\n流量分钟样本：{stats['traffic_delta_samples']} 条",
        )
        await query.answer("缓存保留时间已更新")
        await show_callback_page(
            query,
            "✅ <b>缓存保留时间已更新</b>\n"
            "────────────\n"
            f"当前设置：<b>{html.escape(label)}</b>\n\n"
            "本次已清理：\n"
            f"• 活跃 IP 记录：<b>{int(stats.get('active_ip_records') or 0)}</b> 条\n"
            f"• IP 归属地缓存：<b>{int(stats.get('ip_geo_cache') or 0)}</b> 条\n"
            f"• 流量分钟样本：<b>{int(stats.get('traffic_delta_samples') or 0)}</b> 条\n"
            f"• 采样中断记录：<b>{int(stats.get('traffic_sample_gaps') or 0)}</b> 条\n"
            f"• 自定义范围：<b>{int(stats.get('traffic_ranges') or 0)}</b> 条",
            cache_retention_keyboard(bot_ctx.cache_path, days),
            parse_mode="HTML",
        )
        return

    if data == "main_menu:parameter_config:cover":
        context.user_data["awaiting_custom_cover"] = True
        context.user_data.pop("awaiting_custom_nickname", None)
        await answer_callback_silently(query)
        await show_callback_page(query, "🖼 自定题图\n\n请直接发送一张图片。\n收到后，我会把它设为你打开 /start 时显示的题图。", cover_config_keyboard())
        return

    if data == "main_menu:parameter_config:cover_reset":
        context.user_data.pop("awaiting_custom_cover", None)
        await asyncio.to_thread(ui_pref_delete_sync, cache_path, query.from_user.id, "cover_file_id")
        await query.answer("已重置为 Bot 头像", show_alert=True)
        await show_callback_page(query, "🖼 自定题图\n\n已重置：之后 /start 会继续使用 Bot 头像。", parameter_config_keyboard())
        return

    if data == "main_menu:parameter_config:nickname":
        context.user_data["awaiting_custom_nickname"] = True
        context.user_data.pop("awaiting_custom_cover", None)
        await answer_callback_silently(query)
        await show_callback_page(query, "🏷 自定昵称\n\n请发送要显示在 /start 欢迎语里的昵称。", nickname_config_keyboard())
        return

    if data == "main_menu:parameter_config:nickname_reset":
        context.user_data.pop("awaiting_custom_nickname", None)
        await asyncio.to_thread(ui_pref_delete_sync, cache_path, query.from_user.id, "nickname")
        await query.answer("已重置为 Telegram 名称", show_alert=True)
        await show_callback_page(query, "🏷 自定昵称\n\n已重置：之后 /start 会继续使用你的 Telegram 名称。", parameter_config_keyboard())
        return
    return True

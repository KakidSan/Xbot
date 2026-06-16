from __future__ import annotations

from ...common import (
    BadRequest,
    ContextTypes,
    Path,
    Message,
    Update,
    asyncio,
    html,
    is_admin_user_id,
    log,
    re,
    timedelta,
)
from ...config import AppConfig
from ...db.cache import (
    initialization_acknowledge_sync,
    list_user_ips_from_cache_sync,
    ui_pref_get_sync,
)
from ..context import BotContext
from ..formatters import bot_health_overview_text_sync
from ..keyboards import active_users_keyboard
from ...node_monitor import (
    format_node_link_detail_sync,
    format_node_links_text_sync,
    node_link_detail_keyboard_sync,
    node_links_keyboard_sync,
    refresh_subscription_nodes_sync,
)
from ..menus import (
    clear_history_confirm_keyboard,
    empty_section_keyboard,
    health_check_keyboard,
    main_menu_keyboard,
    traffic_management_keyboard,
)
from ..callback_data import normalize_main_menu_callback
from ..permissions import is_allowed, is_bot_self_update
from .operation_logs import operation_logs_callback


async def handle_main_menu_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    cfg: AppConfig,
    bot_ctx: BotContext,
    cache_path: Path,
    cache_retention_text_sync,
    cache_retention_preview_text,
    show_initialization_gate,
    answer_callback_silently,
    show_callback_page,
    send_start_menu,
    open_dashboard_card,
    purge_chat_history,
    resolve_telegram_user_label,
    reply_long_text,
    send_or_jump_traffic_dashboard,
    traffic_custom_state,
    traffic_custom_prompt_text,
) -> None:
    query = update.callback_query
    if not query or not query.message:
        return None
    if not is_allowed(update, cfg):
        if is_bot_self_update(update, cfg):
            return
        await query.answer("未授权，无法使用该功能", show_alert=True)
        return None
    data = normalize_main_menu_callback(query.data or "")
    if data == "main_menu:init_ack":
        await asyncio.to_thread(initialization_acknowledge_sync, cache_path)
        await query.answer("初始化已确认")
        if query.message and hasattr(query.message, "delete"):
            try:
                await query.message.delete()
            except Exception as exc:
                log.warning("删除初始化确认消息失败，继续发送主菜单：%s", exc)
            if isinstance(query.message, Message):
                update._effective_message = query.message
                await send_start_menu(update, context)
        return None
    elif await show_initialization_gate(query):
        return None

    sections = {
        "main_menu:status_notice": "💬 通知推送",
    }

    if data == "main_menu":
        await answer_callback_silently(query)
        user = query.from_user
        custom_name = await asyncio.to_thread(
            ui_pref_get_sync, cache_path, user.id, "nickname"
        )
        tg_name = html.escape(
            str(custom_name or user.full_name or user.username or user.id)
        )
        is_admin = is_admin_user_id(user.id, cfg)
        role_emoji = "👑" if is_admin else "🎩"
        await show_callback_page(
            query,
            f"{role_emoji} {tg_name}，<b>请选择功能</b>",
            main_menu_keyboard(is_admin),
            parse_mode="HTML",
        )
        return None

    if (
        await operation_logs_callback(
            update,
            context,
            cfg=cfg,
            bot_ctx=bot_ctx,
            cache_path=cache_path,
            data=data,
            query=query,
            answer_callback_silently=answer_callback_silently,
            show_callback_page=show_callback_page,
        )
    ) is not False:
        return None

    if data == "main_menu:clear_history":
        await answer_callback_silently(query)
        await show_callback_page(
            query,
            "👋🏻 <b>清除对话记录</b>\n────────────\n将尝试清空当前对话记录。\n此操作不可恢复。\n\n⚠️ 确认要继续吗？",
            clear_history_confirm_keyboard(),
            parse_mode="HTML",
            auto_delete=False,
        )
        return None

    if data == "main_menu:clear_history_confirm":
        chat_id = (
            query.message.chat_id
            if query.message and hasattr(query.message, "chat_id")
            else None
        )
        message_id = query.message.message_id if query.message else None
        await query.answer("正在后台清空历史记录，请稍候...")
        log.info(
            "开始后台清空 Telegram 历史记录：chat=%s from_message_id=%s",
            chat_id,
            message_id,
        )

        async def purge_chat_history_background() -> None:
            try:
                deleted, failed = await purge_chat_history(chat_id, message_id)
                log.info(
                    "后台清空 Telegram 历史记录完成：chat=%s deleted=%s failed=%s",
                    chat_id,
                    deleted,
                    failed,
                )
            except Exception as exc:
                log.exception(
                    "后台清空 Telegram 历史记录失败：chat=%s error=%s", chat_id, exc
                )

        context.application.create_task(purge_chat_history_background())
        return None

    if data in {"main_menu:system_check", "main_menu:system_check_refresh"}:
        is_refresh = data.endswith("_refresh")
        if not is_refresh:
            await query.answer("正在执行健康检查，请稍候...")
        text = await asyncio.to_thread(
            bot_health_overview_text_sync,
            cfg,
            cache_path,
            is_admin_user_id(query.from_user.id, cfg),
        )
        if len(text) <= 3900:
            await show_callback_page(
                query, text, health_check_keyboard(), parse_mode="HTML"
            )
        else:
            await show_callback_page(
                query,
                "🩺 <b>健康检查</b>\n────────────\n结果较长，已完整分段发送在下方。",
                health_check_keyboard(),
                parse_mode="HTML",
            )
            await reply_long_text(
                query.message,
                text,
                parse_mode="HTML",
                reply_markup=health_check_keyboard(),
            )
        if is_refresh:
            await query.answer("刷新成功")
        return None

    if data.startswith("main_menu:node_links") or data.startswith("node_link:"):
        await answer_callback_silently(query)
        page = 0
        select_match = re.fullmatch(r"node_link:select:(\d+):(\d+)", data)
        page_match = re.fullmatch(r"node_link:page:(\d+)", data)
        refresh_match = re.fullmatch(r"node_link:refresh:(\d+)", data)
        if select_match:
            node_id = int(select_match.group(1))
            page = int(select_match.group(2))
            text = await asyncio.to_thread(format_node_link_detail_sync, cfg, node_id)
            keyboard = await asyncio.to_thread(
                node_link_detail_keyboard_sync, cache_path, query.from_user.id, page
            )
            await show_callback_page(query, text, keyboard, parse_mode="HTML")
            return None
        if refresh_match:
            page = int(refresh_match.group(1))
            total = await asyncio.to_thread(refresh_subscription_nodes_sync, cfg)
            await query.answer(f"已刷新，可用节点 {total} 个")
        if page_match:
            page = int(page_match.group(1))
        text = await asyncio.to_thread(format_node_links_text_sync, cfg, page)
        keyboard = await asyncio.to_thread(node_links_keyboard_sync, cfg, page)
        await show_callback_page(query, text, keyboard, parse_mode="HTML")
        return None

    if data == "main_menu:traffic_management":
        await answer_callback_silently(query)
        await show_callback_page(
            query,
            "🌊 <b>流量统计</b>\n────────────\n请选择功能。",
            traffic_management_keyboard(),
            parse_mode="HTML",
        )
        return None

    if data == "main_menu:traffic_users":
        await query.answer("正在统计用户用量，请稍候...")
        await send_or_jump_traffic_dashboard(query.message, "users_preset_24h")
        return None

    if data == "main_menu:traffic_nodes":
        await query.answer("正在统计节点用量，请稍候...")
        await send_or_jump_traffic_dashboard(query.message, "nodes_preset_24h")
        return None

    if data in sections:
        await answer_callback_silently(query)
        await show_callback_page(
            query,
            f"{sections[data]}\n\n此功能入口已预留，等待下一步配置。",
            empty_section_keyboard(),
        )
        return None

    await query.answer("该入口暂未开放", show_alert=True)


async def handle_close_message_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE, *, cfg: AppConfig
) -> None:
    query = update.callback_query
    if not query or not query.message:
        return None
    if not is_allowed(update, cfg):
        if is_bot_self_update(update, cfg):
            return
        await query.answer("未授权", show_alert=True)
        return None
    await query.answer("已关闭")
    try:
        if hasattr(query.message, "delete"):
            await query.message.delete()
    except BadRequest as exc:
        log.debug("关闭消息删除失败：%s", exc)


async def handle_detail_back_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    cfg: AppConfig,
    cache_path: Path,
    answer_callback_silently,
    show_callback_page,
) -> None:
    query = update.callback_query
    if not query or not query.message:
        return None
    if not is_allowed(update, cfg):
        if is_bot_self_update(update, cfg):
            return
        await query.answer("未授权", show_alert=True)
        return None
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
        result = await asyncio.to_thread(
            list_user_ips_from_cache_sync, cache_path, label, window
        )
        await show_callback_page(
            query, result, active_users_keyboard(target), parse_mode="HTML"
        )
        return None
    await show_callback_page(
        query, "🌐 请选择在线记录统计周期：", active_users_keyboard()
    )

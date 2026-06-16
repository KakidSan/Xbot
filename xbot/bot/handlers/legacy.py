from __future__ import annotations

from functools import partial

from ...common import (
    APP_DIR,
    Any,
    Application,
    BEIJING_TZ,
    BOT_COMMANDS,
    BadRequest,
    CACHE_RETENTION_OPTIONS,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    DEFAULT_ALLOWLIST_NOTIFICATION_KINDS,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LOG_FORMAT,
    MessageHandler,
    NOTIFICATION_KINDS,
    Path,
    ReplyKeyboardRemove,
    Update,
    alert_period_label,
    argparse,
    asyncio,
    datetime,
    field,
    filters,
    html,
    ip_range_kind,
    is_admin_user_id,
    is_super_admin_user_id,
    log,
    logging,
    parse_ip_kind,
    re,
    signal,
    sqlite3,
    timedelta,
)
from ...config import AppConfig, build_config_from_env
from .auth import auth_callback
from .operation_logs import operation_logs_callback
from ..authorization import (
    auth_user_ids_to_labels as auth_user_ids_to_labels_from_cache,
    authorization_delete_confirm_keyboard as authorization_delete_confirm_keyboard_for_cfg,
    authorization_delete_keyboard as authorization_delete_keyboard_for_cfg,
    authorization_manage_keyboard as authorization_manage_keyboard_for_cfg,
    authorization_role_change_keyboard as authorization_role_change_keyboard_for_cfg,
    authorization_role_change_text as authorization_role_change_text_for_cfg,
    telegram_authorization_list_text_sync as telegram_authorization_list_text_for_cfg,
    telegram_user_label_sync as telegram_user_label_from_cache,
)
from ..context import BotContext
from ..menus import (
    back_close_row,
    clear_history_confirm_keyboard,
    cover_config_keyboard,
    debug_tools_keyboard,
    empty_section_keyboard,
    health_check_keyboard,
    ip_ignore_menu_keyboard,
    ip_monitor_keyboard,
    main_menu_keyboard,
    nickname_config_keyboard,
    parameter_config_keyboard,
    reset_cache_confirm_keyboard,
    reset_cache_keyboard,
    reset_user_ip_multi_confirm_keyboard,
    traffic_management_keyboard,
)
from ..operation_logs import (
    log_operation_from_query as log_operation_from_query_with_cache,
    log_operation_from_update as log_operation_from_update_with_cache,
    operation_log_detail_keyboard,
    operation_log_detail_text_sync,
    operation_log_summary_text_sync,
    operation_logs_menu_keyboard,
    operation_logs_summary_keyboard,
)
from ..operation_details import alert_category, alert_setting_before_after_detail, auth_change_detail, ip_ignore_detail
from ..version import version_command as handle_version_command, version_update_callback as handle_version_update_callback
from ...db.cache import (
    active_user_button_items_from_cache_sync,
    actor_name_from_user,
    alert_global_period_sync,
    alert_global_threshold_sync,
    alert_reset_setting_sync,
    alert_set_global_period_sync,
    alert_set_global_threshold_sync,
    alert_setting_label,
    alert_upsert_setting_sync,
    alert_user_list_sync,
    alert_user_setting_sync,
    asn_key_for_geo_row,
    asn_text,
    auto_delete_message_delete_sync,
    auto_delete_message_is_pinned_sync,
    auto_delete_message_set_sync,
    cache_retention_days_sync,
    cache_retention_label,
    cache_retention_preview_sync,
    cache_retention_set_and_prune_sync,
    clear_message_tracking_for_chat_sync,
    clear_user_ip_records_multi_sync,
    count_user_ips_from_cache_sync,
    format_timestamp,
    geo_area_key,
    ignored_rule_toggle_sync,
    ignored_rule_values_sync,
    init_cache,
    initialization_acknowledge_sync,
    initialization_progress_text_sync,
    initialization_status_sync,
    ip_alert_row_for_user_sync,
    ipv4_24_cidr,
    list_all_cached_user_buttons_sync,
    list_user_ips_from_cache_sync,
    make_range_kind,
    notification_toggle_sync,
    operation_log_add_sync,
    operation_log_counts_sync,
    operation_log_get_sync,
    operation_log_mark_read_sync,
    operation_logs_list_sync,
    pinned_dashboard_delete_message_sync,
    pinned_dashboard_delete_sync,
    pinned_dashboard_set_sync,
    preview_clear_user_ip_records_multi_sync,
    preview_prune_stats_before_sync,
    prune_stats_before_sync,
    query_user_ips_from_cache_sync,
    reset_local_cache_sync,
    resolve_cache_path,
    save_traffic_range_sync,
    traffic_dimension_from_kind,
    traffic_kind_for_dimension,
    ui_pref_delete_sync,
    ui_pref_get_sync,
    ui_pref_set_sync,
    update_authorized_users_in_cache_sync,
    update_telegram_roles_in_cache_sync,
    upsert_all_cache_users,
)
from ...geo import ignored_rules_text_sync
from ...alerts import check_ip_alerts
from ...collector import (
    cache_collector_loop,
    cleanup_legacy_traffic_dashboard_messages,
    initialize_cache_before_notifications_sync,
    notify_collector_health_transition,
    traffic_dashboard_refresh_loop,
    traffic_report_push_loop,
    traffic_sampler_loop,
)
from ...updater import send_update_result_notice, version_update_check_loop
from ..formatters import (
    alert_global_setting_text_sync,
    alert_summary_sync,
    alert_user_setting_text_sync,
    bot_health_overview_text_sync,
    bot_status_text_sync,
    cached_user_name_by_id,
    format_bytes,
    format_ip_alert,
    notification_ip_alert_mode_label,
    render_user_label,
    traffic_dashboard_text_from_kind_sync,
    user_display,
)
from ..keyboards import (
    alert_global_keyboard,
    alert_menu_keyboard,
    alert_user_list_keyboard,
    alert_user_setting_keyboard,
    active_users_keyboard,
    cache_retention_keyboard,
    notification_push_keyboard,
    reset_user_ip_select_keyboard,
    alert_user_setting_keyboard_for_source,
    detail_keyboard,
    ignored_rules_keyboard,
    ip_alert_keyboard,
    ip_ignore_list_keyboard,
    ip_detail_list_keyboard,
    traffic_custom_available_bounds,
    traffic_custom_day_keyboard,
    traffic_custom_keyboard_for_state,
    traffic_custom_hour_keyboard,
    traffic_custom_minute_keyboard,
    traffic_custom_month_keyboard,
    traffic_custom_single_year,
    traffic_custom_year_keyboard,
    traffic_floor_confirm_keyboard,
    traffic_dashboard_keyboard,
    traffic_period_keyboard,
    user_ip_detail_keyboard,
    user_ip_ignore_dimension_keyboard,
    user_ip_ignore_list_keyboard,
    user_ip_query_page_keyboard,
)

def user_id(update: Update) -> int | None:
    return update.effective_user.id if update.effective_user else None

def bot_id_from_token(token: str) -> int | None:
    match = re.match(r"^(\d+):", token.strip())
    return int(match.group(1)) if match else None

def is_bot_self_update(update: Update, cfg: AppConfig) -> bool:
    user = update.effective_user
    if not user:
        return False
    token_bot_id = bot_id_from_token(cfg.telegram.bot_token)
    return bool(getattr(user, "is_bot", False)) or (token_bot_id is not None and user.id == token_bot_id)

def is_allowed(update: Update, cfg: AppConfig) -> bool:
    uid = user_id(update)
    return uid is not None and uid in cfg.telegram.allowed_user_ids

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
        await status_message.edit_text(result, parse_mode=parse_mode, reply_markup=reply_markup)
    except BadRequest as exc:
        log.warning("编辑测试消息失败，改为删除后重新发送：%s", exc)
        try:
            await status_message.delete()
        except BadRequest:
            pass
        if update.effective_message:
            await update.effective_message.reply_text(result, parse_mode=parse_mode, reply_markup=reply_markup)

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
            await status_message.edit_caption(caption=result, parse_mode=parse_mode, reply_markup=reply_markup)
        else:
            await status_message.edit_text(result, parse_mode=parse_mode, reply_markup=reply_markup)
    except BadRequest as exc:
        log.warning("编辑状态消息失败，改为删除后重新发送：%s", exc)
        try:
            await status_message.delete()
        except BadRequest:
            pass
        if update.effective_message:
            await update.effective_message.reply_text(result, parse_mode=parse_mode, reply_markup=reply_markup)













async def handle_ip_detail_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, *, cfg: AppConfig, bot_ctx: BotContext, cache_path: Path, show_initialization_gate, answer_callback_silently, show_callback_page, mark_no_auto_delete_message) -> None:
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

    list_match = re.fullmatch(r"ip_detail_list:(ip_(?:1h|24h|7d|30d)|iprange_\d+_\d+):(\d+)", data)
    if list_match:
        kind = list_match.group(1)
        page = int(list_match.group(2))
        parsed = parse_ip_kind(kind)
        if not parsed:
            await query.answer("请求无效，请重新进入。", show_alert=True)
            return
        label, start_ts, end_ts = parsed
        await query.answer("正在生成用户列表，请稍候...")
        user_buttons = await asyncio.to_thread(active_user_button_items_from_cache_sync, cache_path, None, start_ts, end_ts)
        overview = await asyncio.to_thread(list_user_ips_from_cache_sync, cache_path, label, None, start_ts, end_ts)
        text = f"{overview}\n\n请选择要查看的用户。"
        if not user_buttons:
            text += "\n\n暂无可查看用户。"
        await show_callback_page(query, text, ip_detail_list_keyboard(kind, user_buttons, page), parse_mode="HTML")
        return

    notice_match = re.fullmatch(r"ip_alert_notice:(\d+)", data)
    if notice_match:
        xboard_user_id = int(notice_match.group(1))
        row = await asyncio.to_thread(ip_alert_row_for_user_sync, cache_path, xboard_user_id)
        mark_no_auto_delete_message(query.message)
        await answer_callback_silently(query)
        if row:
            await show_callback_page(query, format_ip_alert(row), ip_alert_keyboard(row), parse_mode="HTML", auto_delete=False)
        else:
            await show_callback_page(
                query,
                "✅ <b>异地登录恢复</b>\n────────────\n当前用户已不再满足异地登录告警条件。",
                InlineKeyboardMarkup([back_close_row("main_menu:ip_monitor", "⬅️ 返回 IP 监控")]),
                parse_mode="HTML",
                auto_delete=False,
            )
        return

    ignore_menu_match = re.fullmatch(r"ip_ignore_menu:(ip_(?:1h|24h|7d|30d)|iprange_\d+_\d+):(\d+):(\d+)(?::(alert))?", data)
    if ignore_menu_match:
        kind = ignore_menu_match.group(1)
        xboard_user_id = int(ignore_menu_match.group(2))
        detail_page = int(ignore_menu_match.group(3))
        source = ignore_menu_match.group(4)
        if source == "alert":
            mark_no_auto_delete_message(query.message)
        await answer_callback_silently(query)
        await show_callback_page(
            query,
            "🚧 <b>忽略当前列表</b>\n────────────\n请选择要从当前活跃 IP 列表中提取的忽略类型。",
            user_ip_ignore_dimension_keyboard(kind, xboard_user_id, detail_page, source),
            parse_mode="HTML",
            auto_delete=(source != "alert"),
        )
        return

    ignore_page_match = re.fullmatch(r"ip_ignore_page:(area|asn|cidr):(ip_(?:1h|24h|7d|30d)|iprange_\d+_\d+):(\d+):(\d+):(\d+)(?::(alert))?", data)
    if ignore_page_match:
        dimension = ignore_page_match.group(1)
        kind = ignore_page_match.group(2)
        xboard_user_id = int(ignore_page_match.group(3))
        detail_page = int(ignore_page_match.group(4))
        list_page = int(ignore_page_match.group(5))
        source = ignore_page_match.group(6)
        if source == "alert":
            mark_no_auto_delete_message(query.message)
        title = {"area": "忽略地区", "asn": "忽略 ASN", "cidr": "忽略 IP"}[dimension]
        await answer_callback_silently(query)
        await show_callback_page(
            query,
            f"🚧 <b>{title}</b>\n────────────\n已从当前页面的活跃 IP 中去重生成按钮。\n点击可切换忽略状态；前缀 ✅ 表示已忽略。",
            user_ip_ignore_list_keyboard(cache_path, context, dimension, kind, xboard_user_id, detail_page, list_page, source),
            parse_mode="HTML",
            auto_delete=(source != "alert"),
        )
        return

    short_toggle_match = re.fullmatch(r"ip_ig_t:([A-Za-z0-9]+)", data)
    if short_toggle_match:
        route_token = short_toggle_match.group(1)
        token_map = context.user_data.get("ip_ignore_tokens") or {}
        route_data = token_map.get(route_token) if isinstance(token_map, dict) else None
        if not route_data:
            await query.answer("请求无效，请重新进入。", show_alert=True)
            return
        dimension = str(route_data.get("dimension") or "")
        kind = str(route_data.get("kind") or "")
        xboard_user_id = int(route_data.get("user_id") or 0)
        detail_page = int(route_data.get("detail_page") or 0)
        list_page = int(route_data.get("list_page") or 0)
        source = str(route_data.get("source") or "") or None
        if source == "alert":
            mark_no_auto_delete_message(query.message)
        if dimension not in {"area", "asn", "cidr"} or not parse_ip_kind(kind) or xboard_user_id <= 0:
            await query.answer("请求无效，请重新进入。", show_alert=True)
            return
        ignore_value = str(route_data.get("value") or "")
        before_values = await asyncio.to_thread(ignored_rule_values_sync, cache_path, dimension)
        enabled = await asyncio.to_thread(ignored_rule_toggle_sync, cache_path, dimension, ignore_value)
        after_values = await asyncio.to_thread(ignored_rule_values_sync, cache_path, dimension)
        await asyncio.to_thread(log_operation_from_query_with_cache, bot_ctx.cache_path, query, "ip_ignore", "切换忽略", ip_ignore_detail(dimension, ignore_value, before_values, after_values, xboard_user_id=xboard_user_id))
        title = {"area": "忽略地区", "asn": "忽略 ASN", "cidr": "忽略 IP"}[dimension]
        await query.answer("已加入忽略" if enabled else "已取消忽略")
        await show_callback_page(
            query,
            f"🚧 <b>{title}</b>\n────────────\n已从当前页面的活跃 IP 中去重生成按钮。\n点击可切换忽略状态；前缀 ✅ 表示已忽略。",
            user_ip_ignore_list_keyboard(cache_path, context, dimension, kind, xboard_user_id, detail_page, list_page, source),
            parse_mode="HTML",
            auto_delete=(source != "alert"),
        )
        return

    ignore_toggle_match = re.fullmatch(r"ip_ignore_toggle:(area|asn|cidr):(ip_(?:1h|24h|7d|30d)|iprange_\d+_\d+):(\d+):(\d+):(\d+):([A-Za-z0-9]+)(?::(alert))?", data)
    if ignore_toggle_match:
        dimension = ignore_toggle_match.group(1)
        kind = ignore_toggle_match.group(2)
        xboard_user_id = int(ignore_toggle_match.group(3))
        detail_page = int(ignore_toggle_match.group(4))
        list_page = int(ignore_toggle_match.group(5))
        token = ignore_toggle_match.group(6)
        source = ignore_toggle_match.group(7)
        if source == "alert":
            mark_no_auto_delete_message(query.message)
        token_map = context.user_data.get("ip_ignore_tokens") or {}
        token_data = token_map.get(token) if isinstance(token_map, dict) else None
        if not token_data or token_data.get("dimension") != dimension:
            await query.answer("请求无效，请重新进入。", show_alert=True)
            return
        ignore_value = str(token_data.get("value") or "")
        before_values = await asyncio.to_thread(ignored_rule_values_sync, cache_path, dimension)
        enabled = await asyncio.to_thread(ignored_rule_toggle_sync, cache_path, dimension, ignore_value)
        after_values = await asyncio.to_thread(ignored_rule_values_sync, cache_path, dimension)
        await asyncio.to_thread(log_operation_from_query_with_cache, bot_ctx.cache_path, query, "ip_ignore", "切换忽略", ip_ignore_detail(dimension, ignore_value, before_values, after_values, xboard_user_id=xboard_user_id))
        title = {"area": "忽略地区", "asn": "忽略 ASN", "cidr": "忽略 IP"}[dimension]
        await query.answer("已加入忽略" if enabled else "已取消忽略")
        await show_callback_page(
            query,
            f"🚧 <b>{title}</b>\n────────────\n已从当前页面的活跃 IP 中去重生成按钮。\n点击可切换忽略状态；前缀 ✅ 表示已忽略。",
            user_ip_ignore_list_keyboard(cache_path, context, dimension, kind, xboard_user_id, detail_page, list_page, source),
            parse_mode="HTML",
            auto_delete=(source != "alert"),
        )
        return

    detail_match = re.fullmatch(r"ip_active_user_detail:(ip_(?:1h|24h|7d|30d)|iprange_\d+_\d+):(\d+)(?::(\d+))?(?::(alert))?", data)
    if detail_match:
        kind = detail_match.group(1)
        xboard_user_id = int(detail_match.group(2))
        page = int(detail_match.group(3) or 0)
        source = detail_match.group(4)
        if source == "alert":
            mark_no_auto_delete_message(query.message)
        parsed = parse_ip_kind(kind)
        if not parsed:
            await query.answer("请求无效，请重新进入。", show_alert=True)
            return
        label, start_ts, end_ts = parsed
        await query.answer("正在查询 IP，请稍候...")
        result = await asyncio.to_thread(
            query_user_ips_from_cache_sync,
            cache_path,
            xboard_user_id,
            label,
            None,
            start_ts,
            end_ts,
            page,
            10,
        )
        total_ips = await asyncio.to_thread(count_user_ips_from_cache_sync, cache_path, xboard_user_id, None, start_ts, end_ts)
        await show_callback_page(query, result, user_ip_detail_keyboard(kind, xboard_user_id, total_ips, page, source), parse_mode="HTML", auto_delete=(source != "alert"))
        return

    await query.answer("请求无效，请重新进入。", show_alert=True)


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



# Export this module's own public symbols for downstream star imports.
__all__ = [name for name in globals() if not name.startswith("_")]

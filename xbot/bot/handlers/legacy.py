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


async def handle_alert_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, *, cfg: AppConfig, bot_ctx: BotContext, cache_path: Path, show_initialization_gate, answer_callback_silently, show_callback_page, mark_no_auto_delete_message) -> None:
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

    menu_match = re.fullmatch(r"alert_menu:(traffic|ip)", data)
    if menu_match:
        alert_type = menu_match.group(1)
        text = await asyncio.to_thread(alert_summary_sync, cache_path, alert_type)
        await answer_callback_silently(query)
        await show_callback_page(query, text, alert_menu_keyboard(alert_type), parse_mode="HTML")
        return

    users_match = re.fullmatch(r"alert_users:(traffic|ip):(\d+)", data)
    if users_match:
        alert_type = users_match.group(1)
        page = int(users_match.group(2))
        await asyncio.to_thread(upsert_all_cache_users, cache_path, cfg.mysql)
        users = await asyncio.to_thread(alert_user_list_sync, cache_path, alert_type, 10000)
        title = "用量异常" if alert_type == "traffic" else "异地登录"
        if not users:
            await answer_callback_silently(query)
            await show_callback_page(
                query,
                f"🌟 {'异常告警' if alert_type == 'traffic' else '异地登录'}<b>独立规则</b>\n────────────\n当前本地缓存中还没有用户列表。请等待后台采集完成后再试。",
                InlineKeyboardMarkup([back_close_row(f"alert_menu:{alert_type}", "⬅️ 返回")]),
                parse_mode="HTML",
            )
            return
        total_pages = max(1, (len(users) + 9) // 10)
        page = min(max(0, page), total_pages - 1)
        await answer_callback_silently(query)
        await show_callback_page(
            query,
            f"🌟 {'异常告警' if alert_type == 'traffic' else '异地登录'}<b>独立规则</b>\n────────────\n请选择用户。",
            alert_user_list_keyboard(alert_type, users, page),
            parse_mode="HTML",
        )
        return

    user_match = re.fullmatch(r"alert_user:(traffic|ip):(\d+)(?::(alert))?", data)
    if user_match:
        alert_type = user_match.group(1)
        xboard_user_id = int(user_match.group(2))
        source = user_match.group(3)
        if source == "alert":
            mark_no_auto_delete_message(query.message)
        text = await asyncio.to_thread(alert_user_setting_text_sync, cache_path, alert_type, xboard_user_id)
        await answer_callback_silently(query)
        await show_callback_page(query, text, alert_user_setting_keyboard_for_source(bot_ctx.cache_path, alert_type, xboard_user_id, source), parse_mode="HTML", auto_delete=(source != "alert"))
        return


    period_page_match = re.fullmatch(r"alert_period_page:(traffic|ip):(\d+)", data)
    if period_page_match:
        alert_type = period_page_match.group(1)
        xboard_user_id = int(period_page_match.group(2))
        title = "流量告警周期" if alert_type == "traffic" else "异地告警周期"
        await answer_callback_silently(query)
        await show_callback_page(
            query,
            f"🕒 <b>{title}</b>\n────────────\n请选择该用户的告警统计周期。",
            alert_user_period_select_keyboard(alert_type, xboard_user_id),
            parse_mode="HTML",
        )
        return

    global_period_page_match = re.fullmatch(r"alert_global_period_page:(traffic|ip)", data)
    if global_period_page_match:
        alert_type = global_period_page_match.group(1)
        title = "用量异常默认周期" if alert_type == "traffic" else "异地登录默认周期"
        await answer_callback_silently(query)
        await show_callback_page(query, f"🕒 <b>{title}</b>\n────────────\n请选择默认告警统计周期。", alert_global_period_select_keyboard(alert_type), parse_mode="HTML")
        return

    global_match = re.fullmatch(r"alert_global:(traffic|ip)(?::custom)?", data)
    if global_match:
        alert_type = global_match.group(1)
        if data.endswith(":custom"):
            context.user_data["awaiting_alert_global_custom"] = {
                "type": alert_type,
                "chat_id": query.message.chat_id,
                "message_id": query.message.message_id,
            }
            unit = "GB，例如：150" if alert_type == "traffic" else "城市数，例如：4"
            await answer_callback_silently(query)
            await show_callback_page(query, f"✍️ 请输入默认规则 ({unit})", InlineKeyboardMarkup([back_close_row(f"alert_global:{alert_type}", "⬅️ 返回")]))
            return
        text = await asyncio.to_thread(alert_global_setting_text_sync, cache_path, alert_type)
        await answer_callback_silently(query)
        await show_callback_page(query, text, alert_global_keyboard(bot_ctx.cache_path, alert_type), parse_mode="HTML")
        return

    global_period_match = re.fullmatch(r"alert_global:(traffic|ip):period:(1h|24h|7d|today|week)", data)
    if global_period_match:
        alert_type = global_period_match.group(1)
        period = global_period_match.group(2)
        before = f"{alert_period_label(await asyncio.to_thread(alert_global_period_sync, cache_path, alert_type))} / {format_bytes(await asyncio.to_thread(alert_global_threshold_sync, cache_path, alert_type)) if alert_type == 'traffic' else str(await asyncio.to_thread(alert_global_threshold_sync, cache_path, alert_type)) + ' 个城市'}"
        await asyncio.to_thread(alert_set_global_period_sync, cache_path, alert_type, period)
        after = f"{alert_period_label(period)} / {format_bytes(await asyncio.to_thread(alert_global_threshold_sync, cache_path, alert_type)) if alert_type == 'traffic' else str(await asyncio.to_thread(alert_global_threshold_sync, cache_path, alert_type)) + ' 个城市'}"
        await asyncio.to_thread(log_operation_from_query_with_cache, bot_ctx.cache_path, query, alert_category(alert_type), "调整默认周期", alert_setting_before_after_detail(alert_type, "默认规则", before, after))
        text = await asyncio.to_thread(alert_global_setting_text_sync, cache_path, alert_type)
        await query.answer("默认周期已保存")
        await show_callback_page(query, text, alert_global_keyboard(bot_ctx.cache_path, alert_type), parse_mode="HTML")
        return

    user_period_match = re.fullmatch(r"alert_set:(traffic|ip):period:(1h|24h|7d|today|week):(\d+)", data)
    if user_period_match:
        alert_type = user_period_match.group(1)
        period = user_period_match.group(2)
        xboard_user_id = int(user_period_match.group(3))
        before_setting = await asyncio.to_thread(alert_user_setting_sync, cache_path, xboard_user_id)
        before = alert_setting_label(before_setting, alert_type, cache_path)
        if alert_type == "traffic":
            await asyncio.to_thread(alert_upsert_setting_sync, cache_path, xboard_user_id, traffic_period=period, traffic_whitelist=0)
        else:
            await asyncio.to_thread(alert_upsert_setting_sync, cache_path, xboard_user_id, ip_period=period, ip_whitelist=0)
        after_setting = await asyncio.to_thread(alert_user_setting_sync, cache_path, xboard_user_id)
        after = alert_setting_label(after_setting, alert_type, cache_path)
        await asyncio.to_thread(log_operation_from_query_with_cache, bot_ctx.cache_path, query, alert_category(alert_type), "调整独立周期", alert_setting_before_after_detail(alert_type, "独立规则", before, after, xboard_user_id))
        text = await asyncio.to_thread(alert_user_setting_text_sync, cache_path, alert_type, xboard_user_id)
        await query.answer("周期已保存")
        await show_callback_page(query, text, alert_user_setting_keyboard(bot_ctx.cache_path, alert_type, xboard_user_id), parse_mode="HTML")
        return

    custom_match = re.fullmatch(r"alert_set:(traffic|ip):custom:(\d+)", data)
    if custom_match:
        alert_type = custom_match.group(1)
        xboard_user_id = int(custom_match.group(2))
        context.user_data["awaiting_alert_custom"] = {
            "type": alert_type,
            "user_id": xboard_user_id,
            "chat_id": query.message.chat_id,
            "message_id": query.message.message_id,
        }
        unit = "GB，例如：150" if alert_type == "traffic" else "城市数，例如：4"
        await answer_callback_silently(query)
        await show_callback_page(query, f"✍️ 请输入独立规则 ({unit})", InlineKeyboardMarkup([back_close_row(f"alert_user:{alert_type}:{xboard_user_id}", "⬅️ 返回")]))
        return

    threshold_match = re.fullmatch(r"alert_set:(traffic|ip):threshold:(\d+):(\d+)", data)
    if threshold_match:
        alert_type = threshold_match.group(1)
        xboard_user_id = int(threshold_match.group(2))
        value = int(threshold_match.group(3))
        before_setting = await asyncio.to_thread(alert_user_setting_sync, cache_path, xboard_user_id)
        before = alert_setting_label(before_setting, alert_type, cache_path)
        if alert_type == "traffic":
            await asyncio.to_thread(alert_upsert_setting_sync, cache_path, xboard_user_id, traffic_threshold_bytes=value * 1024 ** 3, traffic_whitelist=0)
        else:
            await asyncio.to_thread(alert_upsert_setting_sync, cache_path, xboard_user_id, ip_city_threshold=value, ip_whitelist=0)
        after_setting = await asyncio.to_thread(alert_user_setting_sync, cache_path, xboard_user_id)
        after = alert_setting_label(after_setting, alert_type, cache_path)
        await asyncio.to_thread(log_operation_from_query_with_cache, bot_ctx.cache_path, query, alert_category(alert_type), "调整独立规则", alert_setting_before_after_detail(alert_type, "独立规则", before, after, xboard_user_id))
        text = await asyncio.to_thread(alert_user_setting_text_sync, cache_path, alert_type, xboard_user_id)
        await query.answer("规则已保存")
        await show_callback_page(query, text, alert_user_setting_keyboard(bot_ctx.cache_path, alert_type, xboard_user_id), parse_mode="HTML")
        return

    white_match = re.fullmatch(r"alert_set:(traffic|ip):whitelist:(\d+)", data)
    if white_match:
        alert_type = white_match.group(1)
        xboard_user_id = int(white_match.group(2))
        setting = await asyncio.to_thread(alert_user_setting_sync, cache_path, xboard_user_id)
        before = alert_setting_label(setting, alert_type, cache_path)
        if alert_type == "traffic":
            new_value = 0 if int(setting.get("traffic_whitelist") or 0) else 1
            await asyncio.to_thread(alert_upsert_setting_sync, cache_path, xboard_user_id, traffic_whitelist=new_value)
        else:
            new_value = 0 if int(setting.get("ip_whitelist") or 0) else 1
            await asyncio.to_thread(alert_upsert_setting_sync, cache_path, xboard_user_id, ip_whitelist=new_value)
        after_setting = await asyncio.to_thread(alert_user_setting_sync, cache_path, xboard_user_id)
        after = alert_setting_label(after_setting, alert_type, cache_path)
        await asyncio.to_thread(log_operation_from_query_with_cache, bot_ctx.cache_path, query, alert_category(alert_type), "切换白名单", alert_setting_before_after_detail(alert_type, "白名单", before, after, xboard_user_id))
        text = await asyncio.to_thread(alert_user_setting_text_sync, cache_path, alert_type, xboard_user_id)
        await query.answer("白名单已更新")
        await show_callback_page(query, text, alert_user_setting_keyboard(bot_ctx.cache_path, alert_type, xboard_user_id), parse_mode="HTML")
        return

    reset_match = re.fullmatch(r"alert_set:(traffic|ip):reset:(\d+)", data)
    if reset_match:
        alert_type = reset_match.group(1)
        xboard_user_id = int(reset_match.group(2))
        before_setting = await asyncio.to_thread(alert_user_setting_sync, cache_path, xboard_user_id)
        before = alert_setting_label(before_setting, alert_type, cache_path)
        await asyncio.to_thread(alert_reset_setting_sync, cache_path, xboard_user_id, alert_type)
        after_setting = await asyncio.to_thread(alert_user_setting_sync, cache_path, xboard_user_id)
        after = alert_setting_label(after_setting, alert_type, cache_path)
        await asyncio.to_thread(log_operation_from_query_with_cache, bot_ctx.cache_path, query, alert_category(alert_type), "恢复默认规则", alert_setting_before_after_detail(alert_type, "独立规则", before, after, xboard_user_id))
        text = await asyncio.to_thread(alert_user_setting_text_sync, cache_path, alert_type, xboard_user_id)
        await query.answer("已恢复默认")
        await show_callback_page(query, text, alert_user_setting_keyboard(bot_ctx.cache_path, alert_type, xboard_user_id), parse_mode="HTML")
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


def build_application(cfg: AppConfig, cache_path: Path) -> Application:
    bot_ctx = BotContext(cfg=cfg, cache_path=cache_path)
    app = Application.builder().token(bot_ctx.cfg.telegram.bot_token).build()

    async def resolve_telegram_user_label(uid: int) -> str:
        try:
            chat = await app.bot.get_chat(uid)
            name = getattr(chat, "full_name", None) or getattr(chat, "username", None) or str(uid)
            username = getattr(chat, "username", None)
            if username and username not in str(name):
                name = f"{name} (@{username})"
            await asyncio.to_thread(ui_pref_set_sync, cache_path, uid, "telegram_label", str(name))
            return str(name)
        except Exception:
            cached = await asyncio.to_thread(ui_pref_get_sync, cache_path, uid, "telegram_label")
            return str(cached or f"用户 {uid}")

    def cache_retention_text_sync() -> str:
        days = cache_retention_days_sync(cache_path)
        return "\n".join([
            "🗄 <b>缓存保留时间</b>",
            "────────────",
            f"当前设置：<b>{html.escape(cache_retention_label(days))}</b>",
            "",
            "说明：超过保留时间的 Bot 本地缓存会自动清理；选择新周期并确认后，会立即删除超出期限的老缓存记录。",
            "不会修改 XBoard / MySQL / Redis。",
        ])

    def cache_retention_preview_text(option_key: str, preview: dict[str, int]) -> str:
        days, label = CACHE_RETENTION_OPTIONS[option_key]
        cutoff = int(preview.get("cutoff_ts") or 0)
        cutoff_text = "不限制，保留全部历史" if cutoff <= 0 else format_timestamp(cutoff)
        return "\n".join([
            "⚠️ <b>确认缓存保留时间</b>",
            "────────────",
            f"新设置：<b>{html.escape(label)}</b>",
            f"清理边界：<code>{html.escape(cutoff_text)}</code>",
            "",
            "将删除以下超期本地缓存：",
            f"• 活跃 IP 记录：<b>{int(preview.get('active_ip_records') or 0)}</b> 条",
            f"• IP 归属地缓存：<b>{int(preview.get('ip_geo_cache') or 0)}</b> 条",
            f"• 流量分钟样本：<b>{int(preview.get('traffic_delta_samples') or 0)}</b> 条",
            f"• 采样中断记录：<b>{int(preview.get('traffic_sample_gaps') or 0)}</b> 条",
            f"• 自定义范围：<b>{int(preview.get('traffic_ranges') or 0)}</b> 条",
            "",
            "确认后立即生效。",
        ])

    def start_menu_text(update: Update, custom_name: str | None = None) -> str:
        user = update.effective_user
        tg_name = (user.full_name or user.username or str(user.id)) if user else "用户"
        display_name = html.escape(str(custom_name or tg_name))
        uid = user.id if user else None
        role_emoji = "👑" if is_admin_user_id(uid, cfg) else "🎩"
        return f"{role_emoji} {display_name}，<b>请选择功能</b>"

    no_auto_delete_message_keys: set[tuple[str, int]] = set()

    def mark_no_auto_delete_message(message: Any | None) -> None:
        if not message:
            return
        try:
            no_auto_delete_message_keys.add((str(message.chat_id), int(message.message_id)))
        except Exception:
            pass

    async def track_auto_delete_message(message: Any | None, is_pinned: bool = False) -> None:
        if not message:
            return
        try:
            chat_id = str(message.chat_id)
            await asyncio.to_thread(auto_delete_message_set_sync, cache_path, chat_id, message.message_id, is_pinned)
        except Exception as exc:
            log.debug("登记自动删除消息失败：%s", exc)

    def split_telegram_text(text: str, limit: int = 3900) -> list[str]:
        if len(text) <= limit:
            return [text]
        chunks: list[str] = []
        current = ""
        for line in text.splitlines(keepends=True):
            if len(line) > limit:
                if current:
                    chunks.append(current.rstrip("\n"))
                    current = ""
                for start in range(0, len(line), limit):
                    chunks.append(line[start:start + limit].rstrip("\n"))
                continue
            if len(current) + len(line) > limit:
                chunks.append(current.rstrip("\n"))
                current = line
            else:
                current += line
        if current:
            chunks.append(current.rstrip("\n"))
        return chunks

    async def reply_long_text(message: Any, text: str, parse_mode: str | None = None, reply_markup: InlineKeyboardMarkup | None = None) -> None:
        chunks = split_telegram_text(text)
        for index, chunk in enumerate(chunks):
            markup = reply_markup if index == len(chunks) - 1 else None
            sent = await message.reply_text(chunk, parse_mode=parse_mode, reply_markup=markup)
            await track_auto_delete_message(sent)

    async def show_callback_page(
        query,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
        parse_mode: str | None = None,
        auto_delete: bool = True,
    ) -> None:
        if not query.message:
            return
        try:
            if (str(query.message.chat_id), int(query.message.message_id)) in no_auto_delete_message_keys:
                auto_delete = False
        except Exception:
            pass
        try:
            if query.message.text:
                await query.message.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
            elif query.message.caption:
                await query.message.edit_caption(caption=text, parse_mode=parse_mode, reply_markup=reply_markup)
            else:
                sent = await query.message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
                if auto_delete:
                    await track_auto_delete_message(sent)
                return
            if auto_delete:
                is_pinned = await asyncio.to_thread(auto_delete_message_is_pinned_sync, cache_path, str(query.message.chat_id), query.message.message_id)
                await track_auto_delete_message(query.message, is_pinned=is_pinned)
        except Exception as exc:
            log.warning("编辑菜单消息失败，改为发送新消息：%s", exc)
            sent = await query.message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
            if auto_delete:
                await track_auto_delete_message(sent)

    async def show_initialization_gate(query) -> bool:
        init_status = await asyncio.to_thread(initialization_status_sync, cache_path, cfg.ip_geo_queries_per_minute)
        if not init_status.get("initializing"):
            return False
        await answer_callback_silently(query)
        text = await asyncio.to_thread(initialization_progress_text_sync, cache_path, cfg)
        if init_status.get("awaiting_ack"):
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 进入主菜单", callback_data="main_menu:init_ack")]])
        else:
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 刷新初始化进度", callback_data="main_menu")]])
        await show_callback_page(
            query,
            text,
            keyboard,
            parse_mode="HTML",
        )
        return True

    async def purge_chat_history(chat_id: int | str, from_message_id: int) -> tuple[int, int]:
        deleted = 0
        failed = 0
        start_id = max(1, int(from_message_id))
        batch_size = 25
        for batch_start in range(start_id, 0, -batch_size):
            tasks = []
            for message_id in range(batch_start, max(0, batch_start - batch_size), -1):
                tasks.append(context_bot_delete_message(chat_id, message_id))
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for ok in results:
                if ok is True:
                    deleted += 1
                else:
                    failed += 1
            await asyncio.sleep(0.08)
        await asyncio.to_thread(clear_message_tracking_for_chat_sync, cache_path, str(chat_id))
        return deleted, failed

    async def context_bot_delete_message(chat_id: int | str, message_id: int) -> bool:
        try:
            await app.bot.delete_message(chat_id=chat_id, message_id=message_id)
            return True
        except Exception:
            return False

    async def answer_callback_silently(query: CallbackQuery) -> None:
        try:
            await query.answer()
        except BadRequest as exc:
            if "Query is too old" in str(exc) or "query id is invalid" in str(exc):
                log.debug("忽略已过期 callback 确认：%s", exc)
                return
            raise

    async def delete_trigger_command_message(update: Update) -> None:
        message = update.effective_message
        if not message:
            return
        try:
            await context_bot_delete_message(message.chat_id, message.message_id)
        except Exception:
            pass

    async def send_start_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_message:
            return
        uid = user_id(update)
        init_status = await asyncio.to_thread(initialization_status_sync, cache_path, cfg.ip_geo_queries_per_minute)
        if init_status.get("initializing"):
            text = await asyncio.to_thread(initialization_progress_text_sync, cache_path, cfg)
            sent = await update.effective_message.reply_text(
                text,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 进入主菜单", callback_data="main_menu:init_ack")]] if init_status.get("awaiting_ack") else [[InlineKeyboardButton("🔄 刷新初始化进度", callback_data="main_menu")]]),
            )
            await track_auto_delete_message(sent)
            return
        custom_name = await asyncio.to_thread(ui_pref_get_sync, cache_path, uid, "nickname") if uid is not None else None
        custom_cover = await asyncio.to_thread(ui_pref_get_sync, cache_path, uid, "cover_file_id") if uid is not None else None
        text = start_menu_text(update, custom_name)
        is_admin = is_admin_user_id(uid, cfg)
        if custom_cover:
            try:
                sent = await update.effective_message.reply_photo(
                    photo=custom_cover,
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=main_menu_keyboard(is_admin),
                )
                await track_auto_delete_message(sent)
                return
            except Exception as exc:
                log.warning("发送自定义题图失败，改为使用 Bot 头像：%s", exc)
        try:
            me = await context.bot.get_me()
            photos = await context.bot.get_user_profile_photos(me.id, limit=1)
            if photos.total_count > 0 and photos.photos:
                sent = await update.effective_message.reply_photo(
                    photo=photos.photos[0][-1].file_id,
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=main_menu_keyboard(is_admin),
                )
                await track_auto_delete_message(sent)
                return
        except Exception as exc:
            log.warning("读取 Bot 头像失败，改为发送文本菜单：%s", exc)
        sent = await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=main_menu_keyboard(is_admin))
        await track_auto_delete_message(sent)

    async def reply_cover_card(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup: InlineKeyboardMarkup | None = None):
        """Send a card with the same cover image policy as /start; fallback to text."""
        if not update.effective_message:
            return None
        uid = user_id(update)
        custom_cover = await asyncio.to_thread(ui_pref_get_sync, cache_path, uid, "cover_file_id") if uid is not None else None
        if custom_cover:
            try:
                sent = await update.effective_message.reply_photo(
                    photo=custom_cover,
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                )
                await track_auto_delete_message(sent)
                return sent
            except Exception as exc:
                log.warning("发送自定义题图状态卡片失败，改为使用 Bot 头像：%s", exc)
        try:
            me = await context.bot.get_me()
            photos = await context.bot.get_user_profile_photos(me.id, limit=1)
            if photos.total_count > 0 and photos.photos:
                sent = await update.effective_message.reply_photo(
                    photo=photos.photos[0][-1].file_id,
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                )
                await track_auto_delete_message(sent)
                return sent
        except Exception as exc:
            log.warning("读取 Bot 头像失败，改为发送文本状态卡片：%s", exc)
        sent = await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
        await track_auto_delete_message(sent)
        return sent

    async def reply_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, cfg: AppConfig) -> None:
        if not update.effective_message:
            return
        uid = user_id(update)
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                log.info("忽略 Bot 自身更新：%s", user_display(update))
                return
            log.warning("拒绝未授权 Telegram 用户：%s", user_display(update))
            await update.effective_message.reply_html(
                f"Telegram 用户 <code>{uid or 'unknown'}</code> 不在授权名单中。\n"
                "请联系管理员授权后再使用。",
                reply_markup=ReplyKeyboardRemove(),
            )
            return
        if uid is not None and update.effective_user:
            label_parts = [update.effective_user.full_name or update.effective_user.username or str(uid)]
            if update.effective_user.username:
                label_parts.append(f"@{update.effective_user.username}")
            await asyncio.to_thread(ui_pref_set_sync, cache_path, uid, "telegram_label", " ".join(dict.fromkeys(label_parts)))
        await send_start_menu(update, context)





    def traffic_dashboard_text(kind: str) -> str:
        return traffic_dashboard_text_from_kind_sync(cache_path, kind)

    async def auto_delete_unpinned_dashboard(chat_id: str, message_id: int, kind: str) -> None:
        await asyncio.sleep(180)
        is_pinned = await asyncio.to_thread(auto_delete_message_is_pinned_sync, cache_path, chat_id, message_id)
        if is_pinned:
            return
        try:
            await app.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except BadRequest:
            pass
        await asyncio.to_thread(pinned_dashboard_delete_message_sync, cache_path, chat_id, message_id)
        await asyncio.to_thread(auto_delete_message_delete_sync, cache_path, chat_id, message_id)

    async def send_dashboard_card(message: Any, kind: str, user_pref_id: int | None = None) -> None:
        chat_id = str(message.chat_id)
        text = await asyncio.to_thread(traffic_dashboard_text, kind)
        reply_markup = traffic_dashboard_keyboard(kind, is_pinned=False)
        custom_cover = None
        try:
            sender = getattr(message, "from_user", None)
            pref_id = user_pref_id or (sender.id if sender else None)
            if pref_id:
                custom_cover = await asyncio.to_thread(ui_pref_get_sync, cache_path, pref_id, "cover_file_id")
        except Exception:
            custom_cover = None
        sent = None
        if custom_cover:
            try:
                sent = await message.reply_photo(photo=custom_cover, caption=text, parse_mode="HTML", reply_markup=reply_markup)
            except Exception as exc:
                log.warning("发送自定义题图结果失败，改为文本消息：%s", exc)
        if sent is None:
            try:
                me = await app.bot.get_me()
                photos = await app.bot.get_user_profile_photos(me.id, limit=1)
                if photos.total_count > 0 and photos.photos:
                    sent = await message.reply_photo(photo=photos.photos[0][-1].file_id, caption=text, parse_mode="HTML", reply_markup=reply_markup)
            except Exception as exc:
                log.warning("发送 Bot 头像结果失败，改为文本消息：%s", exc)
        if sent is None:
            sent = await message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
        await asyncio.to_thread(pinned_dashboard_set_sync, cache_path, kind, chat_id, sent.message_id, False)
        await asyncio.to_thread(auto_delete_message_set_sync, cache_path, chat_id, sent.message_id, False)
        asyncio.create_task(auto_delete_unpinned_dashboard(chat_id, sent.message_id, kind))

    async def edit_dashboard_card(query: Any, kind: str) -> None:
        chat_id = str(query.message.chat_id)
        text = await asyncio.to_thread(traffic_dashboard_text, kind)
        is_pinned = await asyncio.to_thread(auto_delete_message_is_pinned_sync, cache_path, chat_id, query.message.message_id)
        reply_markup = traffic_dashboard_keyboard(kind, is_pinned=is_pinned)
        await show_callback_page(query, text, reply_markup, parse_mode="HTML")
        await asyncio.to_thread(pinned_dashboard_set_sync, cache_path, kind, chat_id, query.message.message_id, is_pinned)
        await asyncio.to_thread(auto_delete_message_set_sync, cache_path, chat_id, query.message.message_id, is_pinned)

    async def open_dashboard_card(query: Any, kind: str) -> None:
        if not query.message:
            return
        sender = getattr(query, "from_user", None)
        await send_dashboard_card(query.message, kind, sender.id if sender else None)

    def traffic_custom_state(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
        state = context.user_data.setdefault("traffic_custom", {})
        return state if isinstance(state, dict) else {}

    def traffic_custom_enter_initial_step(state: dict[str, Any]) -> None:
        if state.get("mode") in {"custom", "ip_custom"} and traffic_custom_single_year(cache_path):
            _, now_ts = traffic_custom_available_bounds(cache_path)
            state["year"] = datetime.fromtimestamp(now_ts).year
            state["step"] = "month"
        else:
            state.pop("year", None)
            state["step"] = "year"

    def traffic_custom_prompt_text(state: dict[str, Any]) -> str:
        first_ts, _ = traffic_custom_available_bounds(cache_path)
        step = str(state.get("step") or "year")
        step_label = {"year": "年份", "month": "月份", "day": "日期", "hour": "小时", "minute": "分钟"}.get(step, "时间")

        def selected_combo_text() -> str:
            parts: list[str] = []
            if state.get("year"):
                parts.append(f"{int(state['year'])} 年")
            if state.get("month"):
                parts.append(f"{int(state['month']):02d} 月")
            if state.get("day"):
                parts.append(f"{int(state['day']):02d} 日")
            if state.get("hour") is not None:
                parts.append(f"{int(state['hour']):02d} 时")
            if state.get("minute") is not None:
                parts.append(f"{int(state['minute']):02d} 分")
            return " ".join(parts) if parts else "尚未选择"

        if state.get("mode") == "floor":
            lines = [
                "⚙️ 调整起始点",
                f"请选择新的统计起始点的{step_label}。",
                f"（当前可选择的最早时间：{format_timestamp(first_ts)}）",
                f"已选起始点：{selected_combo_text()}",
                "确认后会删除该时间之前的本地统计缓存，后续周期统计也不会再使用这些旧数据。",
            ]
            return "\n".join(lines)
        phase = state.get("phase", "start")
        target_text = "开始时间" if phase == "start" else "结束时间"
        lines = [
            f"请选择{target_text}的{step_label}。",
            f"（可选择的最早时间：{format_timestamp(first_ts)}）",
        ]
        if phase == "start":
            lines.append(f"已选开始：{selected_combo_text()}")
            if state.get("end_ts"):
                lines.append(f"已选结束：{format_timestamp(int(state['end_ts']))}")
        else:
            if state.get("start_ts"):
                lines.append(f"已选开始：{format_timestamp(int(state['start_ts']))}")
            lines.append(f"已选结束：{selected_combo_text()}")
        return "\n".join(lines)

    def traffic_fixed_range(kind: str) -> tuple[int, int, str] | None:
        now = datetime.now()
        if kind == "today":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = now
            return int(start.timestamp()), int(end.timestamp()), "今天"
        if kind == "yesterday":
            start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            end = start.replace(hour=23, minute=59, second=59)
            return int(start.timestamp()), int(end.timestamp()), "昨天"
        if kind == "this_week":
            start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
            return int(start.timestamp()), int(now.timestamp()), "本周"
        if kind == "this_month":
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            return int(start.timestamp()), int(now.timestamp()), "本月"
        return None



    async def send_or_jump_traffic_dashboard(message: Any, kind: str) -> None:
        sender = getattr(message, "from_user", None)
        await send_dashboard_card(message, kind, sender.id if sender else None)




    async def open_traffic_dashboard_message(query: Any, kind: str) -> None:
        if not query.message:
            return
        await query.answer("正在生成查询，请稍候...")
        await send_or_jump_traffic_dashboard(query.message, kind)

    async def switch_traffic_dashboard_message(query: Any, kind: str) -> None:
        if not query.message:
            return
        await query.answer("正在切换周期，请稍候...")
        await edit_dashboard_card(query, kind)














    from ..router import register_handlers

    register_handlers(
        app,
        bot_ctx,
        reply_main_menu=reply_main_menu,
        delete_trigger_command_message=delete_trigger_command_message,
        track_auto_delete_message=track_auto_delete_message,
        reply_cover_card=reply_cover_card,
        edit_or_replace_status_any=edit_or_replace_status_any,
        reply_connection_status=reply_connection_status,
        reply_long_text=reply_long_text,
        send_or_jump_traffic_dashboard=send_or_jump_traffic_dashboard,
        show_callback_page=show_callback_page,
        answer_callback_silently=answer_callback_silently,
        cache_retention_text_sync=cache_retention_text_sync,
        cache_retention_preview_text=cache_retention_preview_text,
        show_initialization_gate=show_initialization_gate,
        send_start_menu=send_start_menu,
        open_dashboard_card=open_dashboard_card,
        purge_chat_history=purge_chat_history,
        resolve_telegram_user_label=resolve_telegram_user_label,
        mark_no_auto_delete_message=mark_no_auto_delete_message,
        send_dashboard_card=send_dashboard_card,
        edit_dashboard_card=edit_dashboard_card,
        open_traffic_dashboard_message=open_traffic_dashboard_message,
        switch_traffic_dashboard_message=switch_traffic_dashboard_message,
        context_bot_delete_message=context_bot_delete_message,
        edit_global_alert_prompt=edit_global_alert_prompt,
        edit_alert_prompt=edit_alert_prompt,
    )
    return app

async def run_once(
    cfg: AppConfig,
    stop_event: asyncio.Event,
) -> None:
    cache_path = resolve_cache_path(cfg.cache_path, APP_DIR)
    bot_ctx = BotContext(cfg=cfg, cache_path=cache_path)
    init_cache(bot_ctx.cache_path)

    app = build_application(bot_ctx.cfg, bot_ctx.cache_path)
    collector_stop_event = asyncio.Event()
    collector_task: asyncio.Task[Any] | None = None
    sampler_stop_event = asyncio.Event()
    sampler_task: asyncio.Task[Any] | None = None
    dashboard_stop_event = asyncio.Event()
    dashboard_task: asyncio.Task[Any] | None = None
    report_stop_event = asyncio.Event()
    report_task: asyncio.Task[Any] | None = None
    version_stop_event = asyncio.Event()
    version_task: asyncio.Task[Any] | None = None

    await app.initialize()
    await app.bot.set_my_commands(BOT_COMMANDS)
    await app.start()
    if not app.updater:
        raise RuntimeError("Telegram updater 初始化失败")
    redis_ok, redis_detail, mysql_ok, mysql_detail, geo_total, geo_success, geo_failed = await asyncio.to_thread(initialize_cache_before_notifications_sync, cfg, cache_path)
    await notify_collector_health_transition(app, cfg, cache_path, "redis", redis_ok, redis_detail or "Redis 缓存采集已恢复成功。")
    if redis_ok or mysql_detail:
        await notify_collector_health_transition(app, cfg, cache_path, "mysql", mysql_ok, mysql_detail or "MySQL 用户信息采集已恢复成功。")
    if geo_success:
        await notify_collector_health_transition(app, cfg, cache_path, "ip_api", True, "IP-API 已恢复响应，启动初始化已完成 IP 归属地补全。")
    elif geo_failed:
        await notify_collector_health_transition(app, cfg, cache_path, "ip_api", False, f"启动初始化 IP 归属地补全失败 {geo_failed} 个。")
    await check_ip_alerts(app, cfg, cache_path)
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    await cleanup_legacy_traffic_dashboard_messages(app, bot_ctx.cache_path)
    collector_task = asyncio.create_task(cache_collector_loop(app, bot_ctx.cfg, bot_ctx.cache_path, collector_stop_event))
    sampler_task = asyncio.create_task(traffic_sampler_loop(app, bot_ctx.cfg, bot_ctx.cache_path, sampler_stop_event))
    dashboard_task = asyncio.create_task(traffic_dashboard_refresh_loop(app, bot_ctx.cfg, bot_ctx.cache_path, dashboard_stop_event))
    report_task = asyncio.create_task(traffic_report_push_loop(app, bot_ctx.cache_path, report_stop_event))
    version_task = asyncio.create_task(version_update_check_loop(app, bot_ctx.cfg, bot_ctx.cache_path, version_stop_event))
    await send_update_result_notice(app)
    log.info("Telegram Bot 已启动，缓存文件：%s", bot_ctx.cache_path)

    try:
        await stop_event.wait()
        return None
    finally:
        log.info("正在停止 Telegram Bot 和缓存采集任务...")
        collector_stop_event.set()
        sampler_stop_event.set()
        dashboard_stop_event.set()
        report_stop_event.set()
        version_stop_event.set()
        if collector_task:
            try:
                await asyncio.wait_for(collector_task, timeout=10)
            except asyncio.TimeoutError:
                collector_task.cancel()
        if sampler_task:
            try:
                await asyncio.wait_for(sampler_task, timeout=10)
            except asyncio.TimeoutError:
                sampler_task.cancel()
        if dashboard_task:
            try:
                await asyncio.wait_for(dashboard_task, timeout=10)
            except asyncio.TimeoutError:
                dashboard_task.cancel()
        if report_task:
            try:
                await asyncio.wait_for(report_task, timeout=10)
            except asyncio.TimeoutError:
                report_task.cancel()
        if version_task:
            try:
                await asyncio.wait_for(version_task, timeout=10)
            except asyncio.TimeoutError:
                version_task.cancel()
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        log.info("Telegram Bot 已停止")

async def serve() -> None:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    cfg = build_config_from_env()

    while not stop_event.is_set():
        try:
            await run_once(cfg, stop_event)
            break
        except Exception as exc:
            log.exception("服务运行异常：%s", exc)
            if stop_event.is_set():
                break
            log.info("5 秒后重试启动")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=5)
                break
            except asyncio.TimeoutError:
                cfg = build_config_from_env()
                continue

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Xbot Telegram Monitor Bot")
    return parser.parse_args()

def main() -> None:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    # 避免 httpx 在日志中输出 Telegram Bot Token 所在的完整请求 URL。
    logging.getLogger("httpx").setLevel(logging.WARNING)

    parse_args()
    asyncio.run(serve())

# Export this module's own public symbols for downstream star imports.
__all__ = [name for name in globals() if not name.startswith("_")]

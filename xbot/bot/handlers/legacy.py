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

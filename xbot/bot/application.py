from __future__ import annotations

from functools import partial

from ..common import (
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
from ..config import AppConfig, build_config_from_env
from .handlers.auth import auth_callback
from .handlers.operation_logs import operation_logs_callback
from .authorization import (
    auth_user_ids_to_labels as auth_user_ids_to_labels_from_cache,
    authorization_delete_confirm_keyboard as authorization_delete_confirm_keyboard_for_cfg,
    authorization_delete_keyboard as authorization_delete_keyboard_for_cfg,
    authorization_manage_keyboard as authorization_manage_keyboard_for_cfg,
    authorization_role_change_keyboard as authorization_role_change_keyboard_for_cfg,
    authorization_role_change_text as authorization_role_change_text_for_cfg,
    telegram_authorization_list_text_sync as telegram_authorization_list_text_for_cfg,
    telegram_user_label_sync as telegram_user_label_from_cache,
)
from .context import BotContext
from .menus import (
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
from .operation_logs import (
    log_operation_from_query as log_operation_from_query_with_cache,
    log_operation_from_update as log_operation_from_update_with_cache,
    operation_log_detail_keyboard,
    operation_log_detail_text_sync,
    operation_log_summary_text_sync,
    operation_logs_menu_keyboard,
    operation_logs_summary_keyboard,
)
from .operation_details import alert_category, alert_setting_before_after_detail, auth_change_detail, ip_ignore_detail
from .version import version_command as handle_version_command, version_update_callback as handle_version_update_callback
from ..db.cache import (
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
from ..geo import ignored_rules_text_sync
from ..alerts import check_ip_alerts
from ..collector import (
    cache_collector_loop,
    cleanup_traffic_dashboard_messages,
    initialize_cache_before_notifications_sync,
    notify_collector_health_transition,
    traffic_dashboard_refresh_loop,
    traffic_report_push_loop,
    traffic_sampler_loop,
)
from ..updater import send_update_result_notice, version_update_check_loop
from .formatters import (
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
from .keyboards import (
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
from .context import BotContext, BotRuntime
from .message_utils import edit_or_replace_status, edit_or_replace_status_any, reply_connection_status
from .permissions import is_allowed, is_bot_self_update


def build_application(cfg: AppConfig, cache_path: Path) -> Application:
    bot_ctx = BotContext(cfg=cfg, cache_path=cache_path)
    app = Application.builder().token(bot_ctx.cfg.telegram.bot_token).build()

    from .messaging import build_runtime_services
    from .router import register_handlers

    services = build_runtime_services(app, cfg, cache_path)
    runtime = BotRuntime(
        bot_ctx=bot_ctx,
        edit_or_replace_status_any=edit_or_replace_status_any,
        reply_connection_status=reply_connection_status,
        **services,
    )
    register_handlers(app, runtime)
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
    await cleanup_traffic_dashboard_messages(app, bot_ctx.cache_path)
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

from __future__ import annotations

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
    calendar,
    datetime,
    field,
    filters,
    hashlib,
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
    earliest_traffic_sample_at_sync,
    format_timestamp,
    geo_area_key,
    ignored_list_items_sync,
    ignored_rule_items_sync,
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
    notification_ip_alert_mode_sync,
    notification_status_sync,
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
    user_ip_ignore_items_sync,
)
from ..geo import ignored_rules_text_sync
from ..alerts import check_ip_alerts
from ..collector import (
    cache_collector_loop,
    cleanup_legacy_traffic_dashboard_messages,
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
from .keyboards import ip_alert_keyboard, traffic_dashboard_keyboard_static

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

def build_application(cfg: AppConfig, cache_path: Path) -> Application:
    bot_ctx = BotContext(cfg=cfg, cache_path=cache_path)
    app = Application.builder().token(bot_ctx.cfg.telegram.bot_token).build()

    def back_close_row(back_callback: str = "main_menu", back_text: str = "⬅️ 返回主菜单") -> list[InlineKeyboardButton]:
        return [
            InlineKeyboardButton(back_text, callback_data=back_callback),
            InlineKeyboardButton("❌ 关闭", callback_data="close_message"),
        ]

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

    def main_menu_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
        rows = [
            [
                InlineKeyboardButton("🩺 健康检查", callback_data="main_menu:system_check"),
                InlineKeyboardButton("💬 通知推送", callback_data="main_menu:notifications"),
            ],
            [
                InlineKeyboardButton("🌊 流量统计", callback_data="main_menu:traffic_management"),
                InlineKeyboardButton("🌐 IP 监控", callback_data="main_menu:ip_monitor"),
            ],
            [
                InlineKeyboardButton("🎨 参数配置", callback_data="main_menu:parameter_config"),
                InlineKeyboardButton("🧪 调试功能", callback_data="main_menu:debug_tools"),
            ],
        ]
        if is_admin:
            rows.append([InlineKeyboardButton("📜 操作日志", callback_data="main_menu:op_logs")])
            rows.append([InlineKeyboardButton("🔑 授权管理", callback_data="main_menu:auth")])
        rows.append([InlineKeyboardButton("❌ 关闭", callback_data="close_message")])
        return InlineKeyboardMarkup(rows)

    def clear_history_confirm_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("✅确认", callback_data="main_menu:clear_history_confirm")],
            [InlineKeyboardButton("❎ 取消", callback_data="main_menu")],
        ])

    def empty_section_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([back_close_row()])

    def health_check_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 刷新", callback_data="main_menu:system_check_refresh")],
            back_close_row(),
        ])

    def traffic_management_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 用户用量", callback_data="main_menu:traffic_users")],
            [InlineKeyboardButton("🖥 节点用量", callback_data="main_menu:traffic_nodes")],
            [InlineKeyboardButton("🚨 异常告警", callback_data="alert_menu:traffic")],
            back_close_row(),
        ])

    def ip_monitor_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 周期统计", callback_data="main_menu:ip_monitor:period")],
            [InlineKeyboardButton("🚨 异地登录", callback_data="alert_menu:ip")],
            [InlineKeyboardButton(f"🚧 忽略列表", callback_data="main_menu:ip_monitor:ignore")],
            back_close_row(),
        ])

    def ip_ignore_menu_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📍 地区", callback_data="main_menu:ip_monitor:ignore:area:0")],
            [InlineKeyboardButton("🏷 ASN", callback_data="main_menu:ip_monitor:ignore:asn:0")],
            [InlineKeyboardButton("🌐 IP", callback_data="main_menu:ip_monitor:ignore:cidr:0")],
            [InlineKeyboardButton("📎 当前忽略", callback_data="main_menu:ip_monitor:ignored_rules:0")],
            back_close_row("main_menu:ip_monitor", "⬅️ 返回 IP 监控"),
        ])

    def ignored_rules_keyboard(context: ContextTypes.DEFAULT_TYPE, page: int = 0) -> InlineKeyboardMarkup:
        items = ignored_rule_items_sync(cache_path)
        token_map = context.user_data.setdefault("ip_ignore_tokens", {})
        if not isinstance(token_map, dict):
            token_map = {}
            context.user_data["ip_ignore_tokens"] = token_map
        page_size = 10
        total_pages = max(1, (len(items) + page_size - 1) // page_size)
        page = min(max(page, 0), total_pages - 1)
        rows: list[list[InlineKeyboardButton]] = []
        for item in items[page * page_size:(page + 1) * page_size]:
            token = hashlib.sha1(f"rule:{item['dimension']}:{item['value']}".encode("utf-8")).hexdigest()[:12]
            token_map[token] = {"dimension": str(item["dimension"]), "value": str(item["value"])}
            label = f"✅ {item['sub']} {item['label']}"
            rows.append([InlineKeyboardButton(label[:64], callback_data=f"main_menu:ip_monitor:ignored_rule_toggle:{page}:{token}")])
        if len(items) > page_size:
            nav_row: list[InlineKeyboardButton] = []
            if page > 0:
                nav_row.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"main_menu:ip_monitor:ignored_rules:{page - 1}"))
            nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="main_menu:noop"))
            if page < total_pages - 1:
                nav_row.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"main_menu:ip_monitor:ignored_rules:{page + 1}"))
            rows.append(nav_row)
        if not rows:
            rows.append([InlineKeyboardButton("当前暂无忽略内容", callback_data="main_menu:noop")])
        rows.append(back_close_row("main_menu:ip_monitor:ignore", "⬅️ 返回忽略列表"))
        return InlineKeyboardMarkup(rows)

    def ip_ignore_list_keyboard(context: ContextTypes.DEFAULT_TYPE, dimension: str, page: int = 0) -> InlineKeyboardMarkup:
        items = ignored_list_items_sync(cache_path, dimension)
        selected = ignored_rule_values_sync(cache_path, dimension)
        token_map = context.user_data.setdefault("ip_ignore_tokens", {})
        if not isinstance(token_map, dict):
            token_map = {}
            context.user_data["ip_ignore_tokens"] = token_map
        page_size = 10
        total_pages = max(1, (len(items) + page_size - 1) // page_size)
        page = min(max(page, 0), total_pages - 1)
        rows: list[list[InlineKeyboardButton]] = []
        for item in items[page * page_size:(page + 1) * page_size]:
            token = hashlib.sha1(f"{dimension}:{item['value']}".encode("utf-8")).hexdigest()[:12]
            token_map[token] = {"dimension": dimension, "value": str(item["value"])}
            prefix = "✅ " if str(item["value"]) in selected else ""
            label = f"{prefix}{item['label']}"
            sub = str(item.get("sub") or "")
            if sub:
                label = f"{label} · {sub}"
            rows.append([InlineKeyboardButton(label[:64], callback_data=f"main_menu:ip_monitor:ignore_toggle:{dimension}:{page}:{token}")])
        if len(items) > page_size:
            nav_row: list[InlineKeyboardButton] = []
            if page > 0:
                nav_row.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"main_menu:ip_monitor:ignore:{dimension}:{page - 1}"))
            nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
            if page < total_pages - 1:
                nav_row.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"main_menu:ip_monitor:ignore:{dimension}:{page + 1}"))
            rows.append(nav_row)
        if not rows:
            rows.append([InlineKeyboardButton("暂无已采集信息", callback_data="main_menu:noop")])
        rows.append(back_close_row("main_menu:ip_monitor:ignore", "⬅️ 返回忽略列表"))
        return InlineKeyboardMarkup(rows)

    def user_ip_ignore_dimension_keyboard(kind: str, xboard_user_id: int, page: int = 0, source: str | None = None) -> InlineKeyboardMarkup:
        suffix = f":{source}" if source else ""
        back_button = InlineKeyboardButton("⬅️ 返回通知", callback_data=f"ip_alert_notice:{xboard_user_id}") if source == "alert" else InlineKeyboardButton("⬅️ 返回详情", callback_data=f"ip_active_user_detail:{kind}:{xboard_user_id}:{page}{suffix}")
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("忽略地区", callback_data=f"ip_ignore_page:area:{kind}:{xboard_user_id}:{page}:0{suffix}"),
                InlineKeyboardButton("忽略 ASN", callback_data=f"ip_ignore_page:asn:{kind}:{xboard_user_id}:{page}:0{suffix}"),
                InlineKeyboardButton("忽略 IP", callback_data=f"ip_ignore_page:cidr:{kind}:{xboard_user_id}:{page}:0{suffix}"),
            ],
            [back_button, InlineKeyboardButton("❌ 关闭", callback_data="close_message")],
        ])

    def user_ip_ignore_list_keyboard(context: ContextTypes.DEFAULT_TYPE, dimension: str, kind: str, xboard_user_id: int, detail_page: int, list_page: int = 0, source: str | None = None) -> InlineKeyboardMarkup:
        items = user_ip_ignore_items_sync(cache_path, xboard_user_id, kind, detail_page, dimension)
        selected = ignored_rule_values_sync(cache_path, dimension)
        token_map = context.user_data.setdefault("ip_ignore_tokens", {})
        if not isinstance(token_map, dict):
            token_map = {}
            context.user_data["ip_ignore_tokens"] = token_map
        page_size = 10
        total_pages = max(1, (len(items) + page_size - 1) // page_size)
        list_page = min(max(list_page, 0), total_pages - 1)
        suffix = f":{source}" if source else ""
        rows: list[list[InlineKeyboardButton]] = []
        for item in items[list_page * page_size:(list_page + 1) * page_size]:
            token = hashlib.sha1(f"{dimension}:{item['value']}".encode("utf-8")).hexdigest()[:12]
            token_map[token] = {"dimension": dimension, "value": str(item["value"])}
            prefix = "✅ " if str(item["value"]) in selected else ""
            label = f"{prefix}{item['label']}"
            if item.get("sub"):
                label = f"{label} · {item['sub']}"
            route_token = hashlib.sha1(f"route:{dimension}:{kind}:{xboard_user_id}:{detail_page}:{list_page}:{token}:{source or ''}".encode("utf-8")).hexdigest()[:12]
            token_map[route_token] = {
                "dimension": dimension,
                "value": str(item["value"]),
                "kind": kind,
                "user_id": int(xboard_user_id),
                "detail_page": int(detail_page),
                "list_page": int(list_page),
                "source": source or "",
            }
            rows.append([InlineKeyboardButton(label[:64], callback_data=f"ip_ig_t:{route_token}")])
        if len(items) > page_size:
            nav_row: list[InlineKeyboardButton] = []
            if list_page > 0:
                nav_row.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"ip_ignore_page:{dimension}:{kind}:{xboard_user_id}:{detail_page}:{list_page - 1}{suffix}"))
            nav_row.append(InlineKeyboardButton(f"{list_page + 1}/{total_pages}", callback_data="noop"))
            if list_page < total_pages - 1:
                nav_row.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"ip_ignore_page:{dimension}:{kind}:{xboard_user_id}:{detail_page}:{list_page + 1}{suffix}"))
            rows.append(nav_row)
        if not rows:
            rows.append([InlineKeyboardButton("当前列表暂无可忽略项", callback_data="noop")])
        back_button = InlineKeyboardButton("⬅️ 返回通知", callback_data=f"ip_alert_notice:{xboard_user_id}") if source == "alert" else InlineKeyboardButton("⬅️ 返回忽略类型", callback_data=f"ip_ignore_menu:{kind}:{xboard_user_id}:{detail_page}{suffix}")
        rows.append([back_button, InlineKeyboardButton("❌ 关闭", callback_data="close_message")])
        return InlineKeyboardMarkup(rows)

    def ip_monitor_period_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("近 1 小时", callback_data="active_users:1h"), InlineKeyboardButton("近 24 小时", callback_data="active_users:24h")],
            [InlineKeyboardButton("近 7 天", callback_data="active_users:7d"), InlineKeyboardButton("近 30 天", callback_data="active_users:30d")],
            [InlineKeyboardButton("自选区间", callback_data="ip_custom:start")],
            back_close_row("main_menu:ip_monitor", "⬅️ 返回 IP 监控"),
        ])

    def ip_monitor_period_result_keyboard(selected_period: str = "1h") -> InlineKeyboardMarkup:
        period_labels = {
            "1h": "近 1 小时",
            "24h": "近 24 小时",
            "7d": "近 7 天",
            "30d": "近 30 天",
        }
        switch_row = [
            InlineKeyboardButton(label, callback_data=f"active_users:{key}")
            for key, label in period_labels.items()
            if key != selected_period
        ]
        return InlineKeyboardMarkup([
            switch_row,
            [InlineKeyboardButton("自选区间", callback_data="ip_custom:start")],
            back_close_row("main_menu:ip_monitor", "⬅️ 返回 IP 监控"),
        ])

    def parameter_config_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🖼 自定题图", callback_data="main_menu:parameter_config:cover")],
            [InlineKeyboardButton("🏷 自定昵称", callback_data="main_menu:parameter_config:nickname")],
            [InlineKeyboardButton("🗄 缓存保留时间", callback_data="main_menu:parameter_config:cache_retention")],
            back_close_row(),
        ])

    def cache_retention_keyboard(selected_days: int | None = None) -> InlineKeyboardMarkup:
        selected_days = cache_retention_days_sync(cache_path) if selected_days is None else selected_days
        rows = []
        for option_key, (days, label) in CACHE_RETENTION_OPTIONS.items():
            mark = "✅ " if int(days) == int(selected_days) else ""
            rows.append([InlineKeyboardButton(f"{mark}{label}", callback_data=f"main_menu:parameter_config:cache_retention_select:{option_key}")])
        rows.append(back_close_row("main_menu:parameter_config", "⬅️ 返回参数配置"))
        return InlineKeyboardMarkup(rows)

    def cache_retention_confirm_keyboard(option_key: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ 确认并清理", callback_data=f"main_menu:parameter_config:cache_retention_confirm:{option_key}")],
            [InlineKeyboardButton("⬅️ 返回选择", callback_data="main_menu:parameter_config:cache_retention"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")],
        ])

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

    def notification_push_keyboard(chat_id: str, is_admin: bool = False) -> InlineKeyboardMarkup:
        status = notification_status_sync(cache_path, chat_id, DEFAULT_ALLOWLIST_NOTIFICATION_KINDS)
        def label(kind: str) -> str:
            if kind == "ip_alert":
                mode = notification_ip_alert_mode_sync(cache_path, chat_id)
                if mode == "advanced":
                    return "🔔 异地登录+"
                if mode == "basic":
                    return "🔔 异地登录"
                return "🔕 异地登录"
            return f"{'🔔' if status.get(kind) else '🔕'} {NOTIFICATION_KINDS[kind]}"
        rows = [
            [InlineKeyboardButton(label("collector"), callback_data="main_menu:notifications:collector")],
            [InlineKeyboardButton(label("daily"), callback_data="main_menu:notifications:daily")],
            [InlineKeyboardButton(label("weekly"), callback_data="main_menu:notifications:weekly")],
            [InlineKeyboardButton(label("monthly"), callback_data="main_menu:notifications:monthly")],
            [InlineKeyboardButton(label("traffic_alert"), callback_data="main_menu:notifications:traffic_alert")],
            [InlineKeyboardButton(label("ip_alert"), callback_data="main_menu:notifications:ip_alert")],
        ]
        if is_admin:
            rows.append([InlineKeyboardButton(label("version_update"), callback_data="main_menu:notifications:version_update")])
        rows.append(back_close_row())
        return InlineKeyboardMarkup(rows)


    def alert_menu_keyboard(alert_type: str) -> InlineKeyboardMarkup:
        back_target = "main_menu:traffic_management" if alert_type == "traffic" else "main_menu:ip_monitor"
        back_text = "⬅️ 返回流量统计" if alert_type == "traffic" else "⬅️ 返回 IP 监控"
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🎚️ 默认规则", callback_data=f"alert_global:{alert_type}")],
            [InlineKeyboardButton("🌟 独立规则", callback_data=f"alert_users:{alert_type}:0")],
            back_close_row(back_target, back_text),
        ])

    def alert_user_list_keyboard(alert_type: str, users: list[dict[str, Any]], page: int = 0) -> InlineKeyboardMarkup:
        per_page = 10
        page = max(0, page)
        start = page * per_page
        page_users = users[start:start + per_page]
        rows = []
        for user in page_users:
            xboard_user_id = int(user["user_id"])
            name = str(user.get("name") or f"用户{xboard_user_id}")
            setting_label = str(user.get("setting_label") or "默认")
            label_text = f"{name} (user_id: {xboard_user_id}) ({setting_label})"
            rows.append([InlineKeyboardButton(label_text, callback_data=f"alert_user:{alert_type}:{xboard_user_id}")])
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"alert_users:{alert_type}:{page - 1}"))
        if start + per_page < len(users):
            nav.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"alert_users:{alert_type}:{page + 1}"))
        if nav:
            rows.append(nav)
        rows.append([InlineKeyboardButton("⬅️ 返回", callback_data=f"alert_menu:{alert_type}"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")])
        return InlineKeyboardMarkup(rows)

    def alert_period_keyboard(prefix: str, alert_type: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("近 1 小时", callback_data=f"{prefix}:{alert_type}:period:1h"), InlineKeyboardButton("近 24 小时", callback_data=f"{prefix}:{alert_type}:period:24h"), InlineKeyboardButton("近 7 天", callback_data=f"{prefix}:{alert_type}:period:7d")],
            [InlineKeyboardButton("今天", callback_data=f"{prefix}:{alert_type}:period:today"), InlineKeyboardButton("本周", callback_data=f"{prefix}:{alert_type}:period:week")],
        ])

    def alert_user_current_period_and_threshold(alert_type: str, xboard_user_id: int) -> tuple[str, str]:
        setting = alert_user_setting_sync(cache_path, xboard_user_id)
        if alert_type == "traffic":
            period = setting.get("traffic_period") or alert_global_period_sync(cache_path, "traffic")
            threshold = int(setting.get("traffic_threshold_bytes") or alert_global_threshold_sync(cache_path, "traffic"))
            return alert_period_label(period), format_bytes(threshold)
        period = setting.get("ip_period") or alert_global_period_sync(cache_path, "ip")
        threshold = int(setting.get("ip_city_threshold") or alert_global_threshold_sync(cache_path, "ip"))
        return alert_period_label(period), f"{threshold} 个城市"

    def alert_user_setting_keyboard(alert_type: str, xboard_user_id: int) -> InlineKeyboardMarkup:
        setting = alert_user_setting_sync(cache_path, xboard_user_id)
        whitelist_key = "traffic_whitelist" if alert_type == "traffic" else "ip_whitelist"
        is_whitelisted = bool(int(setting.get(whitelist_key) or 0))
        whitelist_text = "🌑 取消白名单" if is_whitelisted else "🌕 设为白名单"
        period_text, threshold_text = alert_user_current_period_and_threshold(alert_type, xboard_user_id)
        if is_whitelisted:
            period_text = "♾️"
            threshold_text = "♾️"
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(whitelist_text, callback_data=f"alert_set:{alert_type}:whitelist:{xboard_user_id}")],
            [InlineKeyboardButton(period_text, callback_data=f"alert_period_page:{alert_type}:{xboard_user_id}"), InlineKeyboardButton(threshold_text, callback_data=f"alert_set:{alert_type}:custom:{xboard_user_id}")],
            [InlineKeyboardButton("♻️ 恢复默认", callback_data=f"alert_set:{alert_type}:reset:{xboard_user_id}")],
            [InlineKeyboardButton("⬅️ 用户列表", callback_data=f"alert_users:{alert_type}:0"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")],
        ])

    def alert_user_period_select_keyboard(alert_type: str, xboard_user_id: int) -> InlineKeyboardMarkup:
        rows = alert_period_keyboard("alert_set", alert_type).inline_keyboard
        return InlineKeyboardMarkup([
            *[[InlineKeyboardButton(button.text, callback_data=f"{button.callback_data}:{xboard_user_id}") for button in row] for row in rows],
            [InlineKeyboardButton("⬅️ 返回", callback_data=f"alert_user:{alert_type}:{xboard_user_id}"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")],
        ])

    def alert_global_current_period_and_threshold(alert_type: str) -> tuple[str, str]:
        period = alert_global_period_sync(cache_path, alert_type)
        threshold = alert_global_threshold_sync(cache_path, alert_type)
        if alert_type == "traffic":
            return alert_period_label(period), format_bytes(threshold)
        return alert_period_label(period), f"{threshold} 个城市"

    def alert_global_keyboard(alert_type: str) -> InlineKeyboardMarkup:
        period_text, threshold_text = alert_global_current_period_and_threshold(alert_type)
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(period_text, callback_data=f"alert_global_period_page:{alert_type}"), InlineKeyboardButton(threshold_text, callback_data=f"alert_global:{alert_type}:custom")],
            [InlineKeyboardButton("⬅️ 返回", callback_data=f"alert_menu:{alert_type}"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")],
        ])

    def alert_global_period_select_keyboard(alert_type: str) -> InlineKeyboardMarkup:
        rows = alert_period_keyboard("alert_global", alert_type).inline_keyboard
        return InlineKeyboardMarkup([
            *rows,
            [InlineKeyboardButton("⬅️ 返回", callback_data=f"alert_global:{alert_type}"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")],
        ])
    def debug_tools_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
        rows = []
        if is_admin:
            rows.append([InlineKeyboardButton("🧹 重置缓存", callback_data="main_menu:debug:reset_cache")])
        rows.append([InlineKeyboardButton("👤 重置特定用户 IP 记录", callback_data="main_menu:debug:reset_user_ip")])
        rows.append(back_close_row())
        return InlineKeyboardMarkup(rows)

    def reset_cache_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑 全部重置", callback_data="main_menu:debug:reset_cache_now")],
            [InlineKeyboardButton("⚙️ 调整起始点", callback_data="main_menu:debug:reset_cache_floor")],
            back_close_row("main_menu:debug_tools", "⬅️ 返回调试功能"),
        ])

    def reset_cache_confirm_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ 确认全部重置", callback_data="main_menu:debug:reset_cache_now_confirm")],
            back_close_row("main_menu:debug:reset_cache", "❎ 取消"),
        ])

    def reset_user_ip_select_keyboard(users: list[tuple[int, str]], selected: set[int], page: int = 0) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        page_size = 10
        total_pages = max(1, (len(users) + page_size - 1) // page_size)
        page = min(max(page, 0), total_pages - 1)
        start = page * page_size
        for user_id_value, label in users[start:start + page_size]:
            prefix = "✅ " if user_id_value in selected else ""
            rows.append([InlineKeyboardButton(f"{prefix}{label}", callback_data=f"main_menu:debug:reset_user_ip_toggle:{page}:{user_id_value}")])
        if len(users) > page_size:
            nav_row: list[InlineKeyboardButton] = []
            if page > 0:
                nav_row.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"main_menu:debug:reset_user_ip_page:{page - 1}"))
            nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
            if page < total_pages - 1:
                nav_row.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"main_menu:debug:reset_user_ip_page:{page + 1}"))
            rows.append(nav_row)
        rows.append([InlineKeyboardButton(f"✅ 完成选择 ({len(selected)})", callback_data="main_menu:debug:reset_user_ip_done")])
        rows.append(back_close_row("main_menu:debug_tools", "⬅️ 返回调试功能"))
        return InlineKeyboardMarkup(rows)

    def reset_user_ip_multi_confirm_keyboard(user_ids: list[int]) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ 确认清理所选用户", callback_data="main_menu:debug:reset_user_ip_multi_confirm")],
            [InlineKeyboardButton("⬅️ 返回选择", callback_data="main_menu:debug:reset_user_ip_page:0"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")],
        ])

    def cover_config_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("♻️ 重置为 Bot 头像", callback_data="main_menu:parameter_config:cover_reset")],
            back_close_row("main_menu:parameter_config", "⬅️ 返回参数配置"),
        ])

    def nickname_config_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("♻️ 重置为 Telegram 名称", callback_data="main_menu:parameter_config:nickname_reset")],
            back_close_row("main_menu:parameter_config", "⬅️ 返回参数配置"),
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

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await reply_main_menu(update, context, cfg)
        await delete_trigger_command_message(update)

    async def clear_history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_message:
            return
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                return
            await reply_connection_status(update, cfg)
            return
        sent = await update.effective_message.reply_text(
            "👋🏻 <b>清除对话记录</b>\n────────────\n将尝试清空当前对话记录。\n此操作不可恢复。\n\n⚠️ 确认要继续吗？",
            parse_mode="HTML",
            reply_markup=clear_history_confirm_keyboard(),
        )
        await track_auto_delete_message(sent)

    async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_message:
            return
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                return
            await reply_connection_status(update, cfg)
            return
        text = await asyncio.to_thread(bot_status_text_sync, cfg, cache_path)
        sent = await update.effective_message.reply_text(text, parse_mode="HTML")
        await track_auto_delete_message(sent)

    async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_message:
            return
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                return
            await reply_connection_status(update, cfg)
            return
        status_message = await update.effective_message.reply_text("正在执行健康检查，请稍候...")
        await track_auto_delete_message(status_message)
        admin_view = is_admin_user_id(update.effective_user.id if update.effective_user else None, cfg)
        text = await asyncio.to_thread(bot_health_overview_text_sync, cfg, cache_path, admin_view)
        if len(text) <= 3900:
            await edit_or_replace_status(status_message, text, update, parse_mode="HTML")
            await track_auto_delete_message(status_message)
        else:
            try:
                await status_message.delete()
            except BadRequest:
                pass
            await reply_long_text(update.effective_message, text, parse_mode="HTML")

    def active_users_keyboard(
        selected_period: str | None = None,
        user_buttons: list[tuple[int, str]] | None = None,
        page: int = 0,
    ) -> InlineKeyboardMarkup:
        rows = [[
            InlineKeyboardButton("近 1 小时", callback_data="active_users:1h"),
            InlineKeyboardButton("近 24 小时", callback_data="active_users:24h"),
            InlineKeyboardButton("近 7 天", callback_data="active_users:7d"),
        ]]
        if selected_period:
            rows.append([InlineKeyboardButton("🔎 按用户 ID 查询", callback_data=f"ip_user_query:{selected_period}"), InlineKeyboardButton("🔍 用户列表", callback_data=f"active_users_query:{selected_period}:0")])

        if selected_period and user_buttons is not None:
            page_size = 5
            total_pages = max(1, (len(user_buttons) + page_size - 1) // page_size)
            page = min(max(page, 0), total_pages - 1)
            start = page * page_size
            for user_id, name in user_buttons[start:start + page_size]:
                rows.append([
                    InlineKeyboardButton(
                        name,
                        callback_data=f"active_user_detail:{selected_period}:{user_id}",
                    )
                ])
            if len(user_buttons) > page_size:
                nav_row = []
                if page > 0:
                    nav_row.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"active_users_query:{selected_period}:{page - 1}"))
                nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
                if page < total_pages - 1:
                    nav_row.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"active_users_query:{selected_period}:{page + 1}"))
                rows.append(nav_row)
            rows.append([InlineKeyboardButton("❎ 取消", callback_data=f"active_users_cancel:{selected_period}")])
        if not selected_period:
            rows.append(back_close_row("main_menu:ip_monitor", "⬅️ 返回 IP 监控"))
        return InlineKeyboardMarkup(rows)

    def ip_detail_list_keyboard(kind: str, user_buttons: list[tuple[int, str]], page: int = 0) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        page_size = 5
        total_pages = max(1, (len(user_buttons) + page_size - 1) // page_size)
        page = min(max(page, 0), total_pages - 1)
        start = page * page_size
        for user_id, name in user_buttons[start:start + page_size]:
            rows.append([InlineKeyboardButton(name, callback_data=f"ip_active_user_detail:{kind}:{user_id}")])
        if len(user_buttons) > page_size:
            nav_row: list[InlineKeyboardButton] = []
            if page > 0:
                nav_row.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"ip_detail_list:{kind}:{page - 1}"))
            nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
            if page < total_pages - 1:
                nav_row.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"ip_detail_list:{kind}:{page + 1}"))
            rows.append(nav_row)
        rows.append([InlineKeyboardButton("💫 切换查询周期", callback_data="main_menu:ip_monitor:period"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")])
        return InlineKeyboardMarkup(rows)

    def user_ip_detail_keyboard(kind: str, xboard_user_id: int, total_ips: int, page: int = 0, source: str | None = None) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        page_size = 10
        total_pages = max(1, (total_ips + page_size - 1) // page_size)
        page = min(max(page, 0), total_pages - 1)
        if total_ips > page_size:
            nav_row: list[InlineKeyboardButton] = []
            if page > 0:
                suffix = f":{source}" if source else ""
                nav_row.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"ip_active_user_detail:{kind}:{xboard_user_id}:{page - 1}{suffix}"))
            nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
            if page < total_pages - 1:
                suffix = f":{source}" if source else ""
                nav_row.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"ip_active_user_detail:{kind}:{xboard_user_id}:{page + 1}{suffix}"))
            rows.append(nav_row)
        suffix = f":{source}" if source else ""
        rows.append([
            InlineKeyboardButton("忽略地区", callback_data=f"ip_ignore_page:area:{kind}:{xboard_user_id}:{page}:0{suffix}"),
            InlineKeyboardButton("忽略 ASN", callback_data=f"ip_ignore_page:asn:{kind}:{xboard_user_id}:{page}:0{suffix}"),
            InlineKeyboardButton("忽略 IP", callback_data=f"ip_ignore_page:cidr:{kind}:{xboard_user_id}:{page}:0{suffix}"),
        ])
        back_button = InlineKeyboardButton("⬅️ 返回用户列表", callback_data=f"ip_detail_list:{kind}:0")
        if source == "alert":
            back_button = InlineKeyboardButton("⬅️ 返回告警", callback_data=f"ip_alert_notice:{xboard_user_id}")
        rows.append([
            back_button,
            InlineKeyboardButton("❌ 关闭", callback_data="close_message"),
        ])
        return InlineKeyboardMarkup(rows)

    def alert_user_setting_keyboard_for_source(alert_type: str, xboard_user_id: int, source: str | None = None) -> InlineKeyboardMarkup:
        keyboard = alert_user_setting_keyboard(alert_type, xboard_user_id)
        if source != "alert" or alert_type != "ip":
            return keyboard
        rows = [list(row) for row in keyboard.inline_keyboard]
        rows[-1] = [InlineKeyboardButton("⬅️ 返回告警", callback_data=f"ip_alert_notice:{xboard_user_id}"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")]
        return InlineKeyboardMarkup(rows)

    def detail_keyboard(period_key: str | None = None) -> InlineKeyboardMarkup:
        back_target = period_key if period_key in {"1h", "24h", "7d", "30d"} else "menu"
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("⬅️ 返回", callback_data=f"detail_back:{back_target}"),
            InlineKeyboardButton("❌ 关闭", callback_data="close_message"),
        ]])

    def user_ip_query_page_keyboard(period_key: str | None, xboard_user_id: int, total_ips: int, page: int = 0) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        page_size = 10
        total_pages = max(1, (total_ips + page_size - 1) // page_size)
        page = min(max(page, 0), total_pages - 1)
        period_spec = period_key or "all"
        if total_ips > page_size:
            nav_row: list[InlineKeyboardButton] = []
            if page > 0:
                nav_row.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"user_ip_page:{xboard_user_id}:{page - 1}:{period_spec}"))
            nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
            if page < total_pages - 1:
                nav_row.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"user_ip_page:{xboard_user_id}:{page + 1}:{period_spec}"))
            rows.append(nav_row)
        back_target = period_key if period_key in {"1h", "24h", "7d", "30d"} else "menu"
        rows.append([
            InlineKeyboardButton("⬅️ 返回", callback_data=f"detail_back:{back_target}"),
            InlineKeyboardButton("❌ 关闭", callback_data="close_message"),
        ])
        return InlineKeyboardMarkup(rows)

    def traffic_period_keyboard(dimension: str = "combined", source_kind: str | None = None) -> InlineKeyboardMarkup:
        suffix = f":{dimension}" if dimension in {"users", "nodes"} else ""
        prefix = "traffic_switch" if source_kind else "traffic_period"
        rows = [
            [
                InlineKeyboardButton("近 1 小时", callback_data=f"{prefix}:preset_1h{suffix}"),
                InlineKeyboardButton("近 24 小时", callback_data=f"{prefix}:preset_24h{suffix}"),
            ],
            [
                InlineKeyboardButton("近 7 天", callback_data=f"{prefix}:preset_7d{suffix}"),
                InlineKeyboardButton("近 30 天", callback_data=f"{prefix}:preset_30d{suffix}"),
            ],
            [
                InlineKeyboardButton("今天", callback_data=f"{prefix}:today{suffix}"),
                InlineKeyboardButton("昨天", callback_data=f"{prefix}:yesterday{suffix}"),
            ],
            [
                InlineKeyboardButton("本周", callback_data=f"{prefix}:this_week{suffix}"),
                InlineKeyboardButton("本月", callback_data=f"{prefix}:this_month{suffix}"),
            ],
        ]
        custom_callback = f"traffic_custom:start:{dimension}"
        if source_kind:
            rows.append([InlineKeyboardButton("自选周期", callback_data=custom_callback)])
            rows.append([InlineKeyboardButton("⬅️ 返回结果", callback_data=f"traffic_back:{source_kind}")])
        else:
            rows.append([InlineKeyboardButton("自选周期", callback_data=custom_callback)])
        rows.append([InlineKeyboardButton("❌ 关闭", callback_data="close_message")])
        return InlineKeyboardMarkup(rows)

    def traffic_dashboard_keyboard(kind: str, is_pinned: bool = False) -> InlineKeyboardMarkup:
        return traffic_dashboard_keyboard_static(kind, is_pinned)



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

    def traffic_custom_available_bounds() -> tuple[int, int]:
        first = earliest_traffic_sample_at_sync(cache_path)
        now_ts = int(datetime.now().timestamp())
        return (first or now_ts, now_ts)

    def traffic_custom_single_year() -> bool:
        first_ts, now_ts = traffic_custom_available_bounds()
        return datetime.fromtimestamp(first_ts).year == datetime.fromtimestamp(now_ts).year

    def traffic_custom_enter_initial_step(state: dict[str, Any]) -> None:
        if state.get("mode") in {"custom", "ip_custom"} and traffic_custom_single_year():
            _, now_ts = traffic_custom_available_bounds()
            state["year"] = datetime.fromtimestamp(now_ts).year
            state["step"] = "month"
        else:
            state.pop("year", None)
            state["step"] = "year"

    def traffic_custom_prompt_text(state: dict[str, Any]) -> str:
        first_ts, _ = traffic_custom_available_bounds()
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

    def traffic_custom_year_keyboard(mode: str | None = None, include_now: bool = False, dimension: str = "combined") -> InlineKeyboardMarkup:
        first_ts, now_ts = traffic_custom_available_bounds()
        first_year = datetime.fromtimestamp(first_ts).year
        now_year = datetime.fromtimestamp(now_ts).year
        rows = []
        row = []
        for year in range(first_year, now_year + 1):
            row.append(InlineKeyboardButton(str(year), callback_data=f"traffic_custom:year:{year}"))
            if len(row) == 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        if include_now:
            rows.append([InlineKeyboardButton("⏰ 至今", callback_data="traffic_custom:now")])
        if mode == "ip_custom":
            back_callback = "main_menu:ip_monitor:period"
        elif mode == "floor":
            back_callback = "main_menu:debug:reset_cache"
        else:
            back_callback = "traffic_menu"
        rows.append([InlineKeyboardButton("⬅️ 返回", callback_data=back_callback), InlineKeyboardButton("❌ 关闭", callback_data="close_message")])
        return InlineKeyboardMarkup(rows)

    def traffic_custom_month_keyboard(year: int, include_now: bool = False, mode: str | None = None, dimension: str = "combined") -> InlineKeyboardMarkup:
        first_ts, now_ts = traffic_custom_available_bounds()
        first_dt = datetime.fromtimestamp(first_ts)
        now_dt = datetime.fromtimestamp(now_ts)
        start_month = first_dt.month if year == first_dt.year else 1
        end_month = now_dt.month if year == now_dt.year else 12
        rows = []
        row = []
        for month in range(start_month, end_month + 1):
            row.append(InlineKeyboardButton(f"{month}月", callback_data=f"traffic_custom:month:{month}"))
            if len(row) == 4:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        if include_now:
            rows.append([InlineKeyboardButton("⏰ 至今", callback_data="traffic_custom:now")])
        if traffic_custom_single_year():
            back_callback = "main_menu:ip_monitor:period" if mode == "ip_custom" else "traffic_menu"
            rows.append([InlineKeyboardButton("⬅️ 返回", callback_data=back_callback), InlineKeyboardButton("❌ 关闭", callback_data="close_message")])
        else:
            rows.append([InlineKeyboardButton("⬅️ 返回年份", callback_data="traffic_custom:back:year"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")])
        return InlineKeyboardMarkup(rows)

    def traffic_custom_day_keyboard(year: int, month: int, mode: str | None = None, dimension: str = "combined") -> InlineKeyboardMarkup:
        first_ts, now_ts = traffic_custom_available_bounds()
        first_dt = datetime.fromtimestamp(first_ts)
        now_dt = datetime.fromtimestamp(now_ts)
        _, days_in_month = calendar.monthrange(year, month)
        start_day = first_dt.day if year == first_dt.year and month == first_dt.month else 1
        end_day = now_dt.day if year == now_dt.year and month == now_dt.month else days_in_month
        rows = []
        row = []
        for day in range(start_day, end_day + 1):
            row.append(InlineKeyboardButton(str(day), callback_data=f"traffic_custom:day:{day}"))
            if len(row) == 7:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([InlineKeyboardButton("⬅️ 返回月份", callback_data="traffic_custom:back:month"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")])
        return InlineKeyboardMarkup(rows)

    def traffic_custom_hour_keyboard(year: int, month: int, day: int, mode: str | None = None, dimension: str = "combined") -> InlineKeyboardMarkup:
        first_ts, now_ts = traffic_custom_available_bounds()
        first_dt = datetime.fromtimestamp(first_ts)
        now_dt = datetime.fromtimestamp(now_ts)
        start_hour = first_dt.hour if (year, month, day) == (first_dt.year, first_dt.month, first_dt.day) else 0
        end_hour = now_dt.hour if (year, month, day) == (now_dt.year, now_dt.month, now_dt.day) else 23
        rows = []
        row = []
        for hour in range(start_hour, end_hour + 1):
            row.append(InlineKeyboardButton(f"{hour:02d}", callback_data=f"traffic_custom:hour:{hour}"))
            if len(row) == 6:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([InlineKeyboardButton("⬅️ 返回日期", callback_data="traffic_custom:back:day"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")])
        return InlineKeyboardMarkup(rows)

    def traffic_custom_minute_keyboard(year: int, month: int, day: int, hour: int, mode: str | None = None, dimension: str = "combined") -> InlineKeyboardMarkup:
        first_ts, now_ts = traffic_custom_available_bounds()
        first_dt = datetime.fromtimestamp(first_ts)
        now_dt = datetime.fromtimestamp(now_ts)
        start_minute = first_dt.minute if (year, month, day, hour) == (first_dt.year, first_dt.month, first_dt.day, first_dt.hour) else 0
        end_minute = now_dt.minute if (year, month, day, hour) == (now_dt.year, now_dt.month, now_dt.day, now_dt.hour) else 59
        rows = []
        row = []
        for minute in range(start_minute, end_minute + 1):
            row.append(InlineKeyboardButton(f"{minute:02d}", callback_data=f"traffic_custom:minute:{minute}"))
            if len(row) == 6:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([InlineKeyboardButton("⬅️ 返回小时", callback_data="traffic_custom:back:hour"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")])
        return InlineKeyboardMarkup(rows)

    def traffic_floor_confirm_keyboard(floor_ts: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ 确认调整起始点", callback_data=f"traffic_floor:confirm:{floor_ts}")],
            [InlineKeyboardButton("🔄 重新选择", callback_data="traffic_floor:start"), InlineKeyboardButton("❎ 取消", callback_data="main_menu")],
        ])

    def traffic_custom_keyboard_for_state(state: dict[str, Any]) -> InlineKeyboardMarkup:
        step = state.get("step", "year")
        year = int(state.get("year") or 0)
        month = int(state.get("month") or 0)
        day = int(state.get("day") or 0)
        hour = int(state.get("hour") or 0)
        mode = str(state.get("mode") or "")
        dimension = str(state.get("dimension") or "combined")
        if step == "month" and year:
            include_now = state.get("phase") == "end" and state.get("mode") in {"custom", "ip_custom"} and traffic_custom_single_year()
            return traffic_custom_month_keyboard(year, include_now=include_now, mode=mode, dimension=dimension)
        if step == "day" and year and month:
            return traffic_custom_day_keyboard(year, month, mode=mode, dimension=dimension)
        if step == "hour" and year and month and day:
            return traffic_custom_hour_keyboard(year, month, day, mode=mode, dimension=dimension)
        if step == "minute" and year and month and day:
            return traffic_custom_minute_keyboard(year, month, day, hour, mode=mode, dimension=dimension)
        include_now = state.get("phase") == "end" and state.get("step") == "year" and state.get("mode") in {"custom", "ip_custom"}
        return traffic_custom_year_keyboard(mode, include_now=include_now, dimension=dimension)

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


    async def traffic_daily(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_message:
            return
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                return
            await reply_connection_status(update, cfg)
            return
        sent = await update.effective_message.reply_text("🌊 请选择统计周期：", reply_markup=traffic_period_keyboard())
        await track_auto_delete_message(sent)

    async def send_or_jump_traffic_dashboard(message: Any, kind: str) -> None:
        sender = getattr(message, "from_user", None)
        await send_dashboard_card(message, kind, sender.id if sender else None)


    async def traffic_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_message:
            return
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                return
            await reply_connection_status(update, cfg)
            return
        await send_or_jump_traffic_dashboard(update.effective_message, "users_preset_24h")

    async def traffic_nodes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_message:
            return
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                return
            await reply_connection_status(update, cfg)
            return
        await send_or_jump_traffic_dashboard(update.effective_message, "nodes_preset_24h")

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

    async def traffic_daily_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

        menu_match = re.fullmatch(r"traffic_menu(?::([A-Za-z0-9_]+))?", data)
        if menu_match:
            source_kind = menu_match.group(1)
            dimension = traffic_dimension_from_kind(source_kind or "combined")
            await answer_callback_silently(query)
            await show_callback_page(query, "🌊 请选择统计周期：", traffic_period_keyboard(dimension, source_kind))
            return

        back_match = re.fullmatch(r"traffic_back:([A-Za-z0-9_]+)", data)
        if back_match:
            await answer_callback_silently(query)
            await edit_dashboard_card(query, back_match.group(1))
            return

        period_match = re.fullmatch(r"traffic_period:(preset_1h|preset_24h|preset_7d|preset_30d|today|yesterday|this_week|this_month)(?::(users|nodes))?", data)
        if period_match:
            selected = period_match.group(1)
            dimension = period_match.group(2) or "combined"
            if selected.startswith("preset_"):
                await open_traffic_dashboard_message(query, traffic_kind_for_dimension(dimension, selected))
                return
            fixed = traffic_fixed_range(selected)
            if not fixed:
                await query.answer("请求无效，请重新进入。", show_alert=True)
                return
            start_ts, end_ts, label = fixed
            base_kind = make_range_kind(start_ts, end_ts, label)
            await asyncio.to_thread(save_traffic_range_sync, cache_path, base_kind, start_ts, end_ts, label)
            await open_traffic_dashboard_message(query, traffic_kind_for_dimension(dimension, base_kind))
            return

        switch_match = re.fullmatch(r"traffic_switch:(preset_1h|preset_24h|preset_7d|preset_30d|today|yesterday|this_week|this_month):(users|nodes)", data)
        if switch_match:
            selected = switch_match.group(1)
            dimension = switch_match.group(2)
            if selected.startswith("preset_"):
                await switch_traffic_dashboard_message(query, traffic_kind_for_dimension(dimension, selected))
                return
            fixed = traffic_fixed_range(selected)
            if not fixed:
                await query.answer("请求无效，请重新进入。", show_alert=True)
                return
            start_ts, end_ts, label = fixed
            base_kind = make_range_kind(start_ts, end_ts, label)
            await asyncio.to_thread(save_traffic_range_sync, cache_path, base_kind, start_ts, end_ts, label)
            await switch_traffic_dashboard_message(query, traffic_kind_for_dimension(dimension, base_kind))
            return

        if data == "ip_custom:start":
            state = traffic_custom_state(context)
            state.clear()
            state.update({"mode": "ip_custom", "phase": "start"})
            traffic_custom_enter_initial_step(state)
            await answer_callback_silently(query)
            await show_callback_page(query, traffic_custom_prompt_text(state), traffic_custom_keyboard_for_state(state))
            return

        traffic_custom_start_match = re.fullmatch(r"traffic_custom:start(?::(combined|users|nodes))?", data)
        if traffic_custom_start_match:
            dimension = traffic_custom_start_match.group(1) or "combined"
            state = traffic_custom_state(context)
            state.clear()
            state.update({"mode": "custom", "dimension": dimension, "phase": "start"})
            traffic_custom_enter_initial_step(state)
            await answer_callback_silently(query)
            await show_callback_page(query, traffic_custom_prompt_text(state), traffic_custom_keyboard_for_state(state))
            return

        if data == "traffic_floor:start":
            state = traffic_custom_state(context)
            state.clear()
            state.update({"mode": "floor", "phase": "floor", "step": "year"})
            await answer_callback_silently(query)
            await show_callback_page(query, traffic_custom_prompt_text(state), traffic_custom_year_keyboard(str(state.get("mode") or "")))
            return

        floor_confirm_match = re.fullmatch(r"traffic_floor:confirm:(\d+)", data)
        if floor_confirm_match:
            floor_ts = int(floor_confirm_match.group(1))
            was_debug_reset = bool(context.user_data.get("traffic_custom", {}).get("debug"))
            counts = await asyncio.to_thread(prune_stats_before_sync, cache_path, floor_ts)
            await asyncio.to_thread(log_operation_from_query_with_cache, bot_ctx.cache_path, query, "reset_cache", "调整统计起始点", f"{format_timestamp(floor_ts)}，流量样本 {counts['traffic_delta_samples']} 条")
            context.user_data.pop("traffic_custom", None)
            await query.answer("统计起始点已重置")
            text = (
                "✅ 起始点调整完成\n\n"
                f"新的统计起始点：{format_timestamp(floor_ts)}\n"
                f"已删除流量样本：{counts['traffic_delta_samples']} 条\n"
                f"已删除采样中断记录：{counts['traffic_sample_gaps']} 条\n"
                f"已删除历史自定义范围：{counts['traffic_ranges']} 条\n"
                f"已删除活跃 IP 记录：{counts['active_ip_records']} 条\n\n"
                "后续缓存采集、流量采样和周期统计会基于新的本地缓存起点。"
            )
            if was_debug_reset:
                await show_callback_page(
                    query,
                    text + "\n\n请进入健康检查页，观察重新采集与采样情况。",
                    InlineKeyboardMarkup([
                        [InlineKeyboardButton("🩺 前往健康检查", callback_data="main_menu:system_check")],
                        back_close_row("main_menu:debug_tools", "⬅️ 返回调试功能"),
                    ]),
                )
            else:
                await show_callback_page(query, text, traffic_period_keyboard())
            return

        if data == "traffic_custom:now":
            state = traffic_custom_state(context)
            if state.get("phase") != "end" or not state.get("start_ts"):
                await query.answer("请求无效，请重新进入。", show_alert=True)
                return
            start_ts = int(state.get("start_ts") or 0)
            end_ts = int(datetime.now().timestamp())
            if end_ts <= start_ts:
                await query.answer("结束时间必须晚于开始时间", show_alert=True)
                return
            label = f"自定义 {datetime.fromtimestamp(start_ts).strftime('%Y-%m-%d %H:%M')} - {datetime.fromtimestamp(end_ts).strftime('%Y-%m-%d %H:%M')}"
            mode = state.get("mode")
            dimension = str(state.get("dimension") or "combined")
            state.clear()
            if mode == "ip_custom":
                await query.answer("正在生成查询，请稍候...")
                await send_dashboard_card(query.message, ip_range_kind(start_ts, end_ts), query.from_user.id)
                return
            base_kind = make_range_kind(start_ts, end_ts, label)
            await asyncio.to_thread(save_traffic_range_sync, cache_path, base_kind, start_ts, end_ts, label)
            await open_traffic_dashboard_message(query, traffic_kind_for_dimension(dimension, base_kind))
            return

        custom_match = re.fullmatch(r"traffic_custom:(year|month|day|hour|minute):(\d+)", data)
        if custom_match:
            field = custom_match.group(1)
            value = int(custom_match.group(2))
            state = traffic_custom_state(context)
            state[field] = value
            next_step = {"year": "month", "month": "day", "day": "hour", "hour": "minute"}.get(field)
            if next_step:
                state["step"] = next_step
                await answer_callback_silently(query)
                await show_callback_page(query, traffic_custom_prompt_text(state), traffic_custom_keyboard_for_state(state))
                return

            year = int(state.get("year") or 0)
            month = int(state.get("month") or 0)
            day = int(state.get("day") or 0)
            hour = int(state.get("hour") or 0)
            minute = int(state.get("minute") or 0)
            phase = state.get("phase", "start")
            second = 0 if phase in {"start", "floor"} else 59
            selected_ts = int(datetime(year, month, day, hour, minute, second).timestamp())
            if state.get("mode") == "floor":
                preview = await asyncio.to_thread(preview_prune_stats_before_sync, cache_path, selected_ts)
                await answer_callback_silently(query)
                await show_callback_page(
                    query,
                    "⚠️ 请确认调整起始点\n\n"
                    f"新的统计起始点：{format_timestamp(selected_ts)}\n\n"
                    "确认后会删除该时间之前的本地缓存与采样：\n"
                    f"流量样本：{preview['traffic_delta_samples']} 条\n"
                    f"采样中断记录：{preview['traffic_sample_gaps']} 条\n"
                    f"历史自定义范围：{preview['traffic_ranges']} 条\n"
                    f"活跃 IP 记录：{preview['active_ip_records']} 条\n\n"
                    "这个操作不会修改 XBoard / MySQL，只影响 Bot 本地 SQLite。",
                    traffic_floor_confirm_keyboard(selected_ts),
                )
                return
            if phase == "start":
                state["start_ts"] = selected_ts
                for k in ("year", "month", "day", "hour", "minute"):
                    state.pop(k, None)
                state.update({"phase": "end"})
                traffic_custom_enter_initial_step(state)
                await query.answer("开始时间已选择")
                await show_callback_page(query, traffic_custom_prompt_text(state), traffic_custom_keyboard_for_state(state))
                return
            state["end_ts"] = selected_ts
            start_ts = int(state.get("start_ts") or 0)
            end_ts = int(state.get("end_ts") or 0)
            if end_ts <= start_ts:
                await query.answer("结束时间必须晚于开始时间", show_alert=True)
                return
            label = f"自定义 {datetime.fromtimestamp(start_ts).strftime('%Y-%m-%d %H:%M')} - {datetime.fromtimestamp(end_ts).strftime('%Y-%m-%d %H:%M')}"
            mode = state.get("mode")
            dimension = str(state.get("dimension") or "combined")
            state.clear()
            if mode == "ip_custom":
                await query.answer("正在生成查询，请稍候...")
                await send_dashboard_card(query.message, ip_range_kind(start_ts, end_ts), query.from_user.id)
                return
            base_kind = make_range_kind(start_ts, end_ts, label)
            await asyncio.to_thread(save_traffic_range_sync, cache_path, base_kind, start_ts, end_ts, label)
            await open_traffic_dashboard_message(query, traffic_kind_for_dimension(dimension, base_kind))
            return

        back_match = re.fullmatch(r"traffic_custom:back:(year|month|day|hour)", data)
        if back_match:
            target = back_match.group(1)
            state = traffic_custom_state(context)
            cleanup = {
                "year": ("year", "month", "day", "hour", "minute"),
                "month": ("month", "day", "hour", "minute"),
                "day": ("day", "hour", "minute"),
                "hour": ("hour", "minute"),
            }[target]
            for key in cleanup:
                state.pop(key, None)
            state["step"] = target
            await answer_callback_silently(query)
            await show_callback_page(query, traffic_custom_prompt_text(state), traffic_custom_keyboard_for_state(state))
            return

        match = re.fullmatch(r"traffic_dashboard:(pin|unpin|delete):([A-Za-z0-9_]+)", data)
        if not match:
            await query.answer("请求无效，请重新进入。", show_alert=True)
            return
        action, kind = match.group(1), match.group(2)
        chat_id = str(query.message.chat_id)

        if action == "pin":
            try:
                await query.message.pin(disable_notification=True)
            except BadRequest as exc:
                await query.answer(f"置顶失败：{exc.message}", show_alert=True)
                return
            await asyncio.to_thread(pinned_dashboard_set_sync, cache_path, kind, chat_id, query.message.message_id, True)
            await asyncio.to_thread(auto_delete_message_set_sync, cache_path, chat_id, query.message.message_id, True)
            await query.answer("已置顶")
            await query.message.edit_reply_markup(reply_markup=traffic_dashboard_keyboard(kind, is_pinned=True))
            return

        if action == "unpin":
            try:
                await query.message.unpin()
            except BadRequest as exc:
                await query.answer(f"取消置顶失败：{exc.message}", show_alert=True)
                return
            await asyncio.to_thread(pinned_dashboard_set_sync, cache_path, kind, chat_id, query.message.message_id, False)
            await asyncio.to_thread(auto_delete_message_set_sync, cache_path, chat_id, query.message.message_id, False)
            await query.answer("已取消置顶")
            await query.message.edit_reply_markup(reply_markup=traffic_dashboard_keyboard(kind, is_pinned=False))
            return

        if action == "delete":
            await asyncio.to_thread(pinned_dashboard_delete_sync, cache_path, kind, chat_id)
            await asyncio.to_thread(auto_delete_message_delete_sync, cache_path, chat_id, query.message.message_id)
            await query.answer("已删除")
            try:
                await query.message.delete()
            except BadRequest:
                pass
            return


    async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.message:
            return
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                return
            await query.answer("未授权，无法使用该功能", show_alert=True)
            return
        data = query.data or ""
        if data == "main_menu:init_ack":
            await asyncio.to_thread(initialization_acknowledge_sync, cache_path)
            await query.answer("初始化已确认")
            if query.message:
                try:
                    await query.message.delete()
                except Exception as exc:
                    log.warning("删除初始化确认消息失败，继续发送主菜单：%s", exc)
                update._effective_message = query.message
                await send_start_menu(update, context)
            return
        elif await show_initialization_gate(query):
            return

        sections = {
            "main_menu:status_notice": "💬 通知推送",
            "main_menu:debug_tools": "🧪 调试功能",
        }

        if data == "main_menu":
            await answer_callback_silently(query)
            user = query.from_user
            custom_name = await asyncio.to_thread(ui_pref_get_sync, cache_path, user.id, "nickname")
            tg_name = html.escape(str(custom_name or user.full_name or user.username or user.id))
            is_admin = is_admin_user_id(user.id, cfg)
            role_emoji = "👑" if is_admin else "🎩"
            await show_callback_page(query, f"{role_emoji} {tg_name}，<b>请选择功能</b>", main_menu_keyboard(is_admin), parse_mode="HTML")
            return

        if data.startswith("main_menu:op_logs"):
            if not is_admin_user_id(query.from_user.id, cfg):
                await query.answer("只有管理员可以查看操作日志", show_alert=True)
                return
            if data == "main_menu:op_logs":
                await answer_callback_silently(query)
                keyboard = await asyncio.to_thread(operation_logs_menu_keyboard, bot_ctx.cache_path, query.from_user.id)
                await show_callback_page(query, "📜 <b>操作日志</b>\n────────────\n请选择要查看的操作类型。\n\n按钮括号为：未读日志数量/所有日志数量。", keyboard, parse_mode="HTML")
                return
            detail_match = re.fullmatch(r"main_menu:op_logs:(traffic_alert|ip_alert|ip_ignore|reset_cache|reset_ip|auth):(\d+)", data)
            if detail_match:
                category = detail_match.group(1)
                log_id = int(detail_match.group(2))
                await asyncio.to_thread(operation_log_mark_read_sync, cache_path, query.from_user.id, log_id)
                await query.answer("已标记为已读")
                await show_callback_page(query, await asyncio.to_thread(operation_log_detail_text_sync, bot_ctx.cache_path, log_id), operation_log_detail_keyboard(category), parse_mode="HTML")
                return
            log_match = re.fullmatch(r"main_menu:op_logs:(traffic_alert|ip_alert|ip_ignore|reset_cache|reset_ip|auth)", data)
            if log_match:
                category = log_match.group(1)
                await answer_callback_silently(query)
                text = await asyncio.to_thread(operation_log_summary_text_sync, bot_ctx.cache_path, category, query.from_user.id)
                keyboard = await asyncio.to_thread(operation_logs_summary_keyboard, bot_ctx.cache_path, category, query.from_user.id)
                await show_callback_page(query, text, keyboard, parse_mode="HTML")
                return

        if data.startswith("main_menu:auth"):
            if not is_admin_user_id(query.from_user.id, cfg):
                await query.answer("只有管理员可以使用授权管理", show_alert=True)
                return
            is_super_admin = is_super_admin_user_id(query.from_user.id, cfg)
            if data == "main_menu:auth":
                context.user_data.pop("awaiting_auth_add_user_id", None)
                context.user_data.pop("auth_delete_selected", None)
                context.user_data.pop("auth_role_changes", None)
                for target_uid in sorted(cfg.telegram.allowed_user_ids):
                    await resolve_telegram_user_label(target_uid)
                await answer_callback_silently(query)
                await show_callback_page(query, await asyncio.to_thread(telegram_authorization_list_text_for_cfg, bot_ctx.cfg, bot_ctx.cache_path), authorization_manage_keyboard_for_cfg(is_super_admin), parse_mode="HTML")
                return
            if data == "main_menu:auth:add":
                context.user_data["awaiting_auth_add_user_id"] = {"chat_id": query.message.chat_id, "message_id": query.message.message_id}
                await answer_callback_silently(query)
                await show_callback_page(query, "🔐 <b>增加授权</b>\n────────────\n请输入要授权的 Telegram 用户 ID。", InlineKeyboardMarkup([back_close_row("main_menu:auth", "⬅️ 返回授权管理")]), parse_mode="HTML")
                return
            if data == "main_menu:auth:roles":
                if not is_super_admin:
                    await query.answer("只有超级管理员可以设置普通管理员", show_alert=True)
                    return
                context.user_data["auth_role_changes"] = {}
                await answer_callback_silently(query)
                await show_callback_page(query, authorization_role_change_text_for_cfg(bot_ctx.cfg, bot_ctx.cache_path, context), authorization_role_change_keyboard_for_cfg(bot_ctx.cfg, bot_ctx.cache_path, context), parse_mode="HTML")
                return
            role_toggle_match = re.fullmatch(r"main_menu:auth:role_toggle:(\d+)", data)
            if role_toggle_match:
                if not is_super_admin:
                    await query.answer("只有超级管理员可以设置普通管理员", show_alert=True)
                    return
                target_uid = int(role_toggle_match.group(1))
                if target_uid in cfg.telegram.super_admin_user_ids:
                    await query.answer("超级管理员只能通过环境变量修改", show_alert=True)
                    return
                current_role = "manager" if target_uid in cfg.telegram.manager_user_ids else "user"
                role_changes = context.user_data.get("auth_role_changes") or {}
                if not isinstance(role_changes, dict):
                    role_changes = {}
                base_role = "manager" if target_uid in cfg.telegram.manager_user_ids else "user"
                next_role = "user" if str(role_changes.get(target_uid, current_role)) == "manager" else "manager"
                if next_role == base_role:
                    role_changes.pop(target_uid, None)
                else:
                    role_changes[target_uid] = next_role
                context.user_data["auth_role_changes"] = role_changes
                await query.answer("已切换，保存后生效")
                await show_callback_page(query, authorization_role_change_text_for_cfg(bot_ctx.cfg, bot_ctx.cache_path, context), authorization_role_change_keyboard_for_cfg(bot_ctx.cfg, bot_ctx.cache_path, context), parse_mode="HTML")
                return
            if data == "main_menu:auth:role_save":
                if not is_super_admin:
                    await query.answer("只有超级管理员可以设置普通管理员", show_alert=True)
                    return
                role_changes = context.user_data.get("auth_role_changes") or {}
                if not isinstance(role_changes, dict) or not role_changes:
                    await query.answer("没有待保存的权限变更", show_alert=True)
                    return
                promote_ids = {int(uid) for uid, role in role_changes.items() if role == "manager"}
                demote_ids = {int(uid) for uid, role in role_changes.items() if role == "user"}
                before_managers = sorted(cfg.telegram.manager_user_ids)
                before_users = sorted(cfg.telegram.authorized_user_ids)
                new_managers, new_users = await asyncio.to_thread(update_telegram_roles_in_cache_sync, cache_path, cfg.telegram.super_admin_user_ids, cfg.telegram.manager_user_ids, cfg.telegram.authorized_user_ids, promote_manager_user_ids=promote_ids, demote_manager_user_ids=demote_ids)
                cfg.telegram.manager_user_ids = new_managers
                cfg.telegram.authorized_user_ids = new_users
                after_managers = sorted(new_managers)
                after_users = sorted(new_users)
                await asyncio.to_thread(log_operation_from_query_with_cache, bot_ctx.cache_path, query, "auth", "权限变更", auth_change_detail(before_managers, after_managers, before_users, after_users))
                context.user_data.pop("auth_role_changes", None)
                await query.answer("权限变更已保存")
                await show_callback_page(query, "✅ 权限变更已保存。\n变更已保存。", authorization_manage_keyboard_for_cfg(is_super_admin), parse_mode="HTML")
                return
            if data == "main_menu:auth:delete":
                context.user_data["auth_delete_selected"] = set()
                await answer_callback_silently(query)
                delete_hint = "请选择要删除授权的用户。\n超级管理员不可通过 Bot 删除。" if is_super_admin else "请选择要删除授权的普通用户。\n普通管理员不可删除管理员。"
                await show_callback_page(query, "🔓 <b>删除授权</b>\n────────────\n" + delete_hint, authorization_delete_keyboard_for_cfg(bot_ctx.cfg, bot_ctx.cache_path, context, is_super_admin), parse_mode="HTML")
                return
            toggle_match = re.fullmatch(r"main_menu:auth:del_toggle:(\d+)", data)
            if toggle_match:
                target_uid = int(toggle_match.group(1))
                selected = context.user_data.get("auth_delete_selected") or set()
                if not isinstance(selected, set):
                    selected = set(selected or [])
                if target_uid in selected:
                    selected.remove(target_uid)
                else:
                    selected.add(target_uid)
                context.user_data["auth_delete_selected"] = selected
                await query.answer("已更新选择")
                delete_hint = "请选择要删除授权的用户。\n超级管理员不可通过 Bot 删除。" if is_super_admin else "请选择要删除授权的普通用户。\n普通管理员不可删除管理员。"
                await show_callback_page(query, "🔓 <b>删除授权</b>\n────────────\n" + delete_hint, authorization_delete_keyboard_for_cfg(bot_ctx.cfg, bot_ctx.cache_path, context, is_super_admin), parse_mode="HTML")
                return
            if data == "main_menu:auth:del_done":
                selected = context.user_data.get("auth_delete_selected") or set()
                if not selected:
                    await query.answer("请先选择要删除的用户", show_alert=True)
                    return
                user_ids = sorted(int(uid) for uid in selected)
                lines = ["⚠️ <b>确认删除授权</b>", "────────────", "将删除以下授权用户："]
                for target_uid in user_ids:
                    emoji = "👑" if target_uid in cfg.telegram.manager_user_ids else "🎩"
                    lines.append(f"{emoji} {html.escape(await resolve_telegram_user_label(target_uid))} (<code>{target_uid}</code>)")
                await answer_callback_silently(query)
                await show_callback_page(query, "\n".join(lines), authorization_delete_confirm_keyboard_for_cfg(), parse_mode="HTML")
                return
            if data == "main_menu:auth:del_confirm":
                selected = context.user_data.get("auth_delete_selected") or set()
                user_ids = {int(uid) for uid in selected}
                if not user_ids:
                    await query.answer("请先选择要删除的用户", show_alert=True)
                    return
                if (user_ids & cfg.telegram.manager_user_ids) and not is_super_admin:
                    await query.answer("普通管理员不可删除管理员", show_alert=True)
                    return
                before_managers = sorted(cfg.telegram.manager_user_ids)
                before_users = sorted(cfg.telegram.authorized_user_ids)
                remove_manager_ids = user_ids & cfg.telegram.manager_user_ids
                remove_user_ids = user_ids & cfg.telegram.authorized_user_ids
                new_managers, new_users = await asyncio.to_thread(update_telegram_roles_in_cache_sync, cache_path, cfg.telegram.super_admin_user_ids, cfg.telegram.manager_user_ids, cfg.telegram.authorized_user_ids, remove_authorized_user_ids=remove_user_ids, remove_manager_user_ids=remove_manager_ids)
                cfg.telegram.manager_user_ids = new_managers
                cfg.telegram.authorized_user_ids = new_users
                after_managers = sorted(new_managers)
                after_users = sorted(new_users)
                await asyncio.to_thread(log_operation_from_query_with_cache, bot_ctx.cache_path, query, "auth", "删除授权", auth_change_detail(before_managers, after_managers, before_users, after_users, deleted_user_ids=user_ids))
                context.user_data.pop("auth_delete_selected", None)
                await query.answer("授权已删除")
                await show_callback_page(query, "✅ 已删除所选授权用户。\n变更已保存。", authorization_manage_keyboard_for_cfg(is_super_admin), parse_mode="HTML")
                return

        if data == "main_menu:clear_history":
            await answer_callback_silently(query)
            await show_callback_page(
                query,
                "👋🏻 <b>清除对话记录</b>\n────────────\n将尝试清空当前对话记录。\n此操作不可恢复。\n\n⚠️ 确认要继续吗？",
                clear_history_confirm_keyboard(),
                parse_mode="HTML",
                auto_delete=False,
            )
            return

        if data == "main_menu:clear_history_confirm":
            chat_id = query.message.chat_id
            message_id = query.message.message_id
            await query.answer("正在后台清空历史记录，请稍候...")
            log.info("开始后台清空 Telegram 历史记录：chat=%s from_message_id=%s", chat_id, message_id)

            async def purge_chat_history_background() -> None:
                try:
                    deleted, failed = await purge_chat_history(chat_id, message_id)
                    log.info("后台清空 Telegram 历史记录完成：chat=%s deleted=%s failed=%s", chat_id, deleted, failed)
                except Exception as exc:
                    log.exception("后台清空 Telegram 历史记录失败：chat=%s error=%s", chat_id, exc)

            context.application.create_task(purge_chat_history_background())
            return

        if data in {"main_menu:system_check", "main_menu:system_check_refresh"}:
            is_refresh = data.endswith("_refresh")
            if not is_refresh:
                await query.answer("正在执行健康检查，请稍候...")
            text = await asyncio.to_thread(bot_health_overview_text_sync, cfg, cache_path, is_admin_user_id(query.from_user.id, cfg))
            if len(text) <= 3900:
                await show_callback_page(query, text, health_check_keyboard(), parse_mode="HTML")
            else:
                await show_callback_page(
                    query,
                    "🩺 <b>健康检查</b>\n────────────\n结果较长，已完整分段发送在下方。",
                    health_check_keyboard(),
                    parse_mode="HTML",
                )
                await reply_long_text(query.message, text, parse_mode="HTML", reply_markup=health_check_keyboard())
            if is_refresh:
                await query.answer("刷新成功")
            return

        if data in {"main_menu:notifications", "main_menu:status_notice"}:
            await answer_callback_silently(query)
            chat_id = str(query.message.chat_id)
            await show_callback_page(
                query,
                "💬 <b>通知推送</b>\n────────────\n流量报表生成时间：北京时间 00:00\n版本更新检查时间：北京时间 12:00\n\n",
                notification_push_keyboard(chat_id, is_admin_user_id(query.from_user.id, cfg)),
                parse_mode="HTML",
            )
            return

        notification_match = re.fullmatch(r"main_menu:notifications:(daily|weekly|monthly|collector|traffic_alert|ip_alert|version_update)", data)
        if notification_match:
            kind = notification_match.group(1)
            if kind == "version_update" and not is_admin_user_id(query.from_user.id, cfg):
                await query.answer("只有管理员可以设置版本更新推送", show_alert=True)
                return
            chat_id = str(query.message.chat_id)
            result = await asyncio.to_thread(notification_toggle_sync, cache_path, chat_id, kind)
            label = NOTIFICATION_KINDS[kind]
            if kind == "ip_alert":
                await query.answer(f"异地登录已切换为{notification_ip_alert_mode_label(str(result))}通知")
            else:
                await query.answer(f"{label}已{'开启' if result else '关闭'}推送")
            await show_callback_page(
                query,
                "💬 <b>通知推送</b>\n────────────\n流量报表生成时间：北京时间 00:00\n版本更新检查时间：北京时间 12:00\n\n",
                notification_push_keyboard(chat_id, is_admin_user_id(query.from_user.id, cfg)),
                parse_mode="HTML",
            )
            return

        if data == "main_menu:traffic_management":
            await answer_callback_silently(query)
            await show_callback_page(
                query,
                "🌊 <b>流量统计</b>\n────────────\n请选择功能。",
                traffic_management_keyboard(),
                parse_mode="HTML",
            )
            return

        if data == "main_menu:traffic_users":
            await query.answer("正在统计用户用量，请稍候...")
            await send_or_jump_traffic_dashboard(query.message, "users_preset_24h")
            return

        if data == "main_menu:traffic_nodes":
            await query.answer("正在统计节点用量，请稍候...")
            await send_or_jump_traffic_dashboard(query.message, "nodes_preset_24h")
            return

        if data == "main_menu:ip_monitor":
            await answer_callback_silently(query)
            await show_callback_page(
                query,
                "🌐 <b>IP 监控</b>\n────────────\n请选择功能。",
                ip_monitor_keyboard(),
                parse_mode="HTML",
            )
            return

        if data == "main_menu:ip_monitor:period":
            await query.answer("正在生成查询，请稍候...")
            await open_dashboard_card(query, "ip_1h")
            return

        if data == "main_menu:ip_monitor:ignore":
            await answer_callback_silently(query)
            await show_callback_page(
                query,
                "🚧 <b>忽略列表</b>\n────────────\n请选择维度。\n\nIPv4 段按 /24 统计；IPv6 暂不参与统计。",
                ip_ignore_menu_keyboard(),
                parse_mode="HTML",
            )
            return

        ignored_rules_match = re.fullmatch(r"main_menu:ip_monitor:ignored_rules:(\d+)", data)
        if ignored_rules_match:
            page = int(ignored_rules_match.group(1))
            await answer_callback_silently(query)
            await show_callback_page(
                query,
                ignored_rules_text_sync(cache_path),
                ignored_rules_keyboard(context, page),
                parse_mode="HTML",
            )
            return

        ignored_rule_toggle_match = re.fullmatch(r"main_menu:ip_monitor:ignored_rule_toggle:(\d+):([A-Za-z0-9]+)", data)
        if ignored_rule_toggle_match:
            page = int(ignored_rule_toggle_match.group(1))
            token = ignored_rule_toggle_match.group(2)
            token_map = context.user_data.get("ip_ignore_tokens") or {}
            token_data = token_map.get(token) if isinstance(token_map, dict) else None
            if not token_data:
                await query.answer("请求无效，请重新进入。", show_alert=True)
                return
            dimension = str(token_data.get("dimension") or "")
            value = str(token_data.get("value") or "")
            if dimension not in {"area", "asn", "cidr"} or not value:
                await query.answer("请求无效，请重新进入。", show_alert=True)
                return
            before_values = await asyncio.to_thread(ignored_rule_values_sync, cache_path, dimension)
            await asyncio.to_thread(ignored_rule_toggle_sync, cache_path, dimension, value)
            after_values = await asyncio.to_thread(ignored_rule_values_sync, cache_path, dimension)
            await asyncio.to_thread(log_operation_from_query_with_cache, bot_ctx.cache_path, query, "ip_ignore", "解除忽略", ip_ignore_detail(dimension, value, before_values, after_values))
            await query.answer("已解除忽略")
            await show_callback_page(
                query,
                ignored_rules_text_sync(cache_path),
                ignored_rules_keyboard(context, page),
                parse_mode="HTML",
            )
            return

        ignore_page_match = re.fullmatch(r"main_menu:ip_monitor:ignore:(area|asn|cidr):(\d+)", data)
        if ignore_page_match:
            dimension = ignore_page_match.group(1)
            page = int(ignore_page_match.group(2))
            title = {"area": "忽略地区", "asn": "忽略 ASN", "cidr": "忽略 IP"}[dimension]
            await answer_callback_silently(query)
            await show_callback_page(
                query,
                f"🚧 <b>{title}</b>\n────────────\n按已采集信息去重展示，并按最近出现时间排序。\n点击按钮可切换忽略状态；前缀 ✅ 表示已忽略。",
                ip_ignore_list_keyboard(context, dimension, page),
                parse_mode="HTML",
            )
            return

        ignore_toggle_match = re.fullmatch(r"main_menu:ip_monitor:ignore_toggle:(area|asn|cidr):(\d+):([A-Za-z0-9]+)", data)
        if ignore_toggle_match:
            dimension = ignore_toggle_match.group(1)
            page = int(ignore_toggle_match.group(2))
            token = ignore_toggle_match.group(3)
            token_map = context.user_data.get("ip_ignore_tokens") or {}
            token_data = token_map.get(token) if isinstance(token_map, dict) else None
            if not token_data or token_data.get("dimension") != dimension:
                await query.answer("请求无效，请重新进入。", show_alert=True)
                return
            value = str(token_data.get("value") or "")
            before_values = await asyncio.to_thread(ignored_rule_values_sync, cache_path, dimension)
            enabled = await asyncio.to_thread(ignored_rule_toggle_sync, cache_path, dimension, value)
            after_values = await asyncio.to_thread(ignored_rule_values_sync, cache_path, dimension)
            await asyncio.to_thread(log_operation_from_query_with_cache, bot_ctx.cache_path, query, "ip_ignore", "切换忽略", ip_ignore_detail(dimension, value, before_values, after_values))
            title = {"area": "忽略地区", "asn": "忽略 ASN", "cidr": "忽略 IP"}[dimension]
            await query.answer("已加入忽略" if enabled else "已取消忽略")
            await show_callback_page(
                query,
                f"🚧 <b>{title}</b>\n────────────\n按已采集信息去重展示，并按最近出现时间排序。\n点击按钮可切换忽略状态；前缀 ✅ 表示已忽略。",
                ip_ignore_list_keyboard(context, dimension, page),
                parse_mode="HTML",
            )
            return

        if data == "main_menu:noop":
            await answer_callback_silently(query)
            return

        if data == "main_menu:ip_monitor:user_query":
            context.user_data["awaiting_user_ip_query_id"] = True
            context.user_data.pop("user_ip_query_period", None)
            await answer_callback_silently(query)
            await show_callback_page(
                query,
                "🔎 <b>按用户 ID 查询 IP</b>\n────────────\n请输入要查询的用户 ID，例如：1",
                InlineKeyboardMarkup([back_close_row("main_menu:ip_monitor", "⬅️ 返回 IP 监控")]),
                parse_mode="HTML",
            )
            return

        if data == "main_menu:parameter_config":
            await answer_callback_silently(query)
            await show_callback_page(query, "🎨 参数配置\n────────────\n请选择要配置的面板参数。", parameter_config_keyboard())
            return

        if data == "main_menu:parameter_config:cache_retention":
            await answer_callback_silently(query)
            await show_callback_page(query, cache_retention_text_sync(), cache_retention_keyboard(), parse_mode="HTML")
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
                cache_retention_keyboard(days),
                parse_mode="HTML",
            )
            return

        if data == "main_menu:debug_tools":
            await answer_callback_silently(query)
            await show_callback_page(query, "🧪 调试功能\n────────────\n请选择要执行的调试操作。", debug_tools_keyboard(is_admin_user_id(query.from_user.id, cfg)))
            return

        if data.startswith("main_menu:debug:reset_cache"):
            if not is_admin_user_id(query.from_user.id, cfg):
                await query.answer("只有管理员可以使用重置缓存", show_alert=True)
                return

        if data == "main_menu:debug:reset_cache":
            context.user_data.pop("debug_reset_cache_mode", None)
            await answer_callback_silently(query)
            await show_callback_page(
                query,
                "🧹 重置缓存\n\n这是高风险操作，会清空本地缓存与采样数据。\n不会修改 XBoard / MySQL，只影响 Bot 本地 SQLite。\n\n请选择重置方式：",
                reset_cache_keyboard(),
            )
            return

        if data == "main_menu:debug:reset_cache_now":
            context.user_data["debug_reset_cache_mode"] = "now"
            await answer_callback_silently(query)
            await show_callback_page(
                query,
                "⚠️ 全部重置确认\n\n将清空：\n- 本地缓存\n- 采样数据\n- 活跃 IP 记录\n- 流量统计范围\n- 置顶仪表盘消息\n\n保留：\n- 自定题图\n- 自定昵称\n\n确认后将重新进入健康检查页，请在页面里观察重新采集情况。",
                reset_cache_confirm_keyboard(),
            )
            return

        if data == "main_menu:debug:reset_cache_now_confirm":
            mode = context.user_data.pop("debug_reset_cache_mode", None)
            if mode != "now":
                await query.answer("请先选择重置方式", show_alert=True)
                return
            stats = await asyncio.to_thread(reset_local_cache_sync, cache_path)
            await asyncio.to_thread(log_operation_from_query_with_cache, bot_ctx.cache_path, query, "reset_cache", "全部重置缓存", f"流量样本 {stats['traffic_delta_samples']} 条，活跃 IP {stats['active_ip_records']} 条")
            await query.answer("缓存已重置")
            await show_callback_page(
                query,
                "✅ 全部重置完成\n\n"
                f"已清空活跃 IP 记录：{stats['active_ip_records']} 条\n"
                f"已清空 IP 归属地缓存：{stats['ip_geo_cache']} 条\n"
                f"已清空用户信息缓存：{stats['users']} 个\n"
                f"已清空流量样本：{stats['traffic_delta_samples']} 条\n"
                f"已清空采样中断记录：{stats['traffic_sample_gaps']} 条\n"
                f"已清空自定义范围：{stats['traffic_ranges']} 条\n"
                f"已清空置顶消息记录：{stats['pinned_dashboard_messages']} 条\n\n"
                "现在请进入健康检查页查看重新采集与补全进度。",
                InlineKeyboardMarkup([[InlineKeyboardButton("🩺 前往健康检查", callback_data="main_menu:system_check")], [InlineKeyboardButton("⬅️ 返回主菜单", callback_data="main_menu")]]),
            )
            return

        if data == "main_menu:debug:reset_cache_floor":
            state = traffic_custom_state(context)
            state.clear()
            state.update({"mode": "floor", "phase": "floor", "step": "year", "debug": True})
            await answer_callback_silently(query)
            await show_callback_page(
                query,
                traffic_custom_prompt_text(state),
                traffic_custom_year_keyboard(str(state.get("mode") or "")),
            )
            return

        if data == "main_menu:debug:reset_user_ip":
            context.user_data["reset_user_ip_selected"] = set()
            await asyncio.to_thread(upsert_all_cache_users, cache_path, cfg.mysql)
            users = await asyncio.to_thread(list_all_cached_user_buttons_sync, cache_path)
            await answer_callback_silently(query)
            await show_callback_page(
                query,
                "👤 重置特定用户 IP 记录\n\n请选择要清理 IP 记录的用户；可多选。\n该操作会把所选用户相关的本地 IP 记录标记为忽略，不会修改 XBoard / MySQL。",
                reset_user_ip_select_keyboard(users, set(), 0),
            )
            return

        reset_user_ip_page_match = re.fullmatch(r"main_menu:debug:reset_user_ip_page:(\d+)", data)
        if reset_user_ip_page_match:
            page = int(reset_user_ip_page_match.group(1))
            await asyncio.to_thread(upsert_all_cache_users, cache_path, cfg.mysql)
            users = await asyncio.to_thread(list_all_cached_user_buttons_sync, cache_path)
            selected = context.user_data.setdefault("reset_user_ip_selected", set())
            if not isinstance(selected, set):
                selected = set(selected or [])
                context.user_data["reset_user_ip_selected"] = selected
            await answer_callback_silently(query)
            await show_callback_page(
                query,
                "👤 重置特定用户 IP 记录\n\n请选择要清理 IP 记录的用户；可多选。\n该操作会把所选用户相关的本地 IP 记录标记为忽略，不会修改 XBoard / MySQL。",
                reset_user_ip_select_keyboard(users, selected, page),
            )
            return

        reset_user_ip_toggle_match = re.fullmatch(r"main_menu:debug:reset_user_ip_toggle:(\d+):(\d+)", data)
        if reset_user_ip_toggle_match:
            page = int(reset_user_ip_toggle_match.group(1))
            xboard_user_id = int(reset_user_ip_toggle_match.group(2))
            selected = context.user_data.setdefault("reset_user_ip_selected", set())
            if not isinstance(selected, set):
                selected = set(selected or [])
                context.user_data["reset_user_ip_selected"] = selected
            if xboard_user_id in selected:
                selected.remove(xboard_user_id)
            else:
                selected.add(xboard_user_id)
            await asyncio.to_thread(upsert_all_cache_users, cache_path, cfg.mysql)
            users = await asyncio.to_thread(list_all_cached_user_buttons_sync, cache_path)
            await query.answer(f"已选择 {len(selected)} 个用户")
            await show_callback_page(
                query,
                "👤 重置特定用户 IP 记录\n\n请选择要清理 IP 记录的用户；可多选。\n该操作会把所选用户相关的本地 IP 记录标记为忽略，不会修改 XBoard / MySQL。",
                reset_user_ip_select_keyboard(users, selected, page),
            )
            return

        if data == "main_menu:debug:reset_user_ip_done":
            selected = context.user_data.get("reset_user_ip_selected") or set()
            if not isinstance(selected, set):
                selected = set(selected or [])
            if not selected:
                await query.answer("请至少选择一个用户", show_alert=True)
                return
            preview = await asyncio.to_thread(preview_clear_user_ip_records_multi_sync, cache_path, list(selected))
            label_lines = "\n".join(f"• {html.escape(label)}" for label in preview.get("labels", [])[:20])
            if len(preview.get("labels", [])) > 20:
                label_lines += f"\n… 另 {len(preview['labels']) - 20} 个用户"
            await answer_callback_silently(query)
            await show_callback_page(
                query,
                "⚠️ 请再次确认是否忽略所选用户的 IP 记录。\n\n"
                f"选择用户：{preview['users']} 个\n"
                f"IP 记录：{preview['records']} 条\n"
                f"涉及 IP：{preview['ips']} 个\n"
                f"最早记录：{format_timestamp(preview['first_seen']) if preview['first_seen'] else '未知'}\n"
                f"最新记录：{format_timestamp(preview['last_seen']) if preview['last_seen'] else '未知'}\n\n"
                f"{label_lines}",
                reset_user_ip_multi_confirm_keyboard(list(selected)),
                parse_mode="HTML",
            )
            return

        if data == "main_menu:debug:reset_user_ip_multi_confirm":
            selected = context.user_data.get("reset_user_ip_selected") or set()
            if not isinstance(selected, set):
                selected = set(selected or [])
            user_ids = sorted(int(x) for x in selected if int(x) > 0)
            if not user_ids:
                await query.answer("选择状态已过期，请重新选择用户", show_alert=True)
                return
            stats = await asyncio.to_thread(clear_user_ip_records_multi_sync, cache_path, user_ids)
            await asyncio.to_thread(log_operation_from_query_with_cache, bot_ctx.cache_path, query, "reset_ip", "重置特定用户 IP 记录", f"用户 {', '.join(str(uid) for uid in user_ids)}；记录 {stats['records']} 条")
            context.user_data.pop("reset_user_ip_selected", None)
            await query.answer("已标记忽略所选用户 IP 记录")
            await show_callback_page(
                query,
                "✅ 所选用户 IP 记录已标记忽略\n\n"
                f"用户数：{stats['users']} 个\n"
                f"已清理记录：{stats['records']} 条\n"
                f"涉及 IP：{stats['ips']} 个\n"
                f"已标记忽略记录：{stats.get('ignored', 0)} 条\n"
                f"剩余计入统计 IP 记录：{stats.get('remaining_active_ips', 0)} 条\n\n"
                "本次调试清理不会触发异地登录恢复通知；被标记忽略的记录仍会正常采集更新，但不会计入统计、详情和告警。\n\n"
                "你可以继续在调试功能中查看其它项。",
                debug_tools_keyboard(is_admin_user_id(query.from_user.id, cfg)),
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

        if data in sections:
            await answer_callback_silently(query)
            await show_callback_page(query, f"{sections[data]}\n\n此功能入口已预留，等待下一步配置。", empty_section_keyboard())
            return

        await query.answer("该入口暂未开放", show_alert=True)


    async def active_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_message:
            return
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                return
            await reply_connection_status(update, cfg)
            return
        init_status = await asyncio.to_thread(initialization_status_sync, cache_path, cfg.ip_geo_queries_per_minute)
        if init_status.get("initializing"):
            sent = await update.effective_message.reply_text(
                await asyncio.to_thread(initialization_progress_text_sync, cache_path, cfg),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 刷新初始化进度", callback_data="main_menu")]]),
            )
            await track_auto_delete_message(sent)
            return
        sent = await update.effective_message.reply_text("🌐 请选择在线记录统计周期：", reply_markup=active_users_keyboard())
        await track_auto_delete_message(sent)

    async def active_users_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

        periods = {
            "1h": ("近 1 小时", timedelta(hours=1)),
            "24h": ("近 24 小时", timedelta(hours=24)),
            "7d": ("近 7 天", timedelta(days=7)),
            "30d": ("近 30 天", timedelta(days=30)),
        }
        data = query.data or ""

        scoped_query_match = re.fullmatch(r"ip_user_query:(?:(1h|24h|7d|30d)|custom:(\d+):(\d+))", data)
        if scoped_query_match:
            period_key = scoped_query_match.group(1)
            context.user_data["awaiting_user_ip_query_id"] = True
            if period_key:
                context.user_data["user_ip_query_period"] = period_key
            else:
                start_ts = int(scoped_query_match.group(2))
                end_ts = int(scoped_query_match.group(3))
                context.user_data["user_ip_query_period"] = f"custom:{start_ts}:{end_ts}"
            await answer_callback_silently(query)
            await show_callback_page(
                query,
                "🔎 <b>按用户 ID 查询 IP</b>\n────────────\n请输入要查询的用户 ID，例如：1",
                InlineKeyboardMarkup([back_close_row("main_menu:ip_monitor", "⬅️ 返回 IP 监控")]),
                parse_mode="HTML",
            )
            return

        query_match = re.fullmatch(r"active_users_query:(1h|24h|7d|30d)(?::(\d+))?", data)
        if query_match:
            period_key = query_match.group(1)
            page = int(query_match.group(2) or 0)
            label, window = periods[period_key]
            await query.answer("正在生成用户按钮，请稍候...")
            result, user_buttons = await asyncio.gather(
                asyncio.to_thread(list_user_ips_from_cache_sync, cache_path, label, window),
                asyncio.to_thread(active_user_button_items_from_cache_sync, cache_path, window),
            )
            await show_callback_page(
                query,
                result,
                active_users_keyboard(period_key, user_buttons, page),
                parse_mode="HTML",
            )
            return

        page_match = re.fullmatch(r"user_ip_page:(\d+):(\d+):(.+)", data)
        if page_match:
            xboard_user_id = int(page_match.group(1))
            page = int(page_match.group(2))
            period_spec = page_match.group(3)
            period_key = None if period_spec == "all" else period_spec
            label = window = start_ts = end_ts = None
            if period_key in periods:
                label, window = periods[period_key]
            elif period_key and period_key.startswith("custom:"):
                _, start_text, end_text = period_key.split(":", 2)
                start_ts = int(start_text)
                end_ts = int(end_text)
                label = "自定区间"
            await query.answer("正在翻页，请稍候...")
            result = await asyncio.to_thread(query_user_ips_from_cache_sync, cache_path, xboard_user_id, label, window, start_ts, end_ts, page, 10)
            total_ips = await asyncio.to_thread(count_user_ips_from_cache_sync, cache_path, xboard_user_id, window, start_ts, end_ts)
            await show_callback_page(query, result, user_ip_query_page_keyboard(period_key, xboard_user_id, total_ips, page), parse_mode="HTML")
            return

        cancel_match = re.fullmatch(r"active_users_cancel:(1h|24h|7d|30d)", data)
        if cancel_match:
            period_key = cancel_match.group(1)
            label, window = periods[period_key]
            await query.answer("已取消")
            result = await asyncio.to_thread(list_user_ips_from_cache_sync, cache_path, label, window)
            await show_callback_page(query, result, active_users_keyboard(period_key), parse_mode="HTML")
            return

        if data == "noop":
            await answer_callback_silently(query)
            return

        detail_match = re.fullmatch(r"active_user_detail:(1h|24h|7d|30d):(\d+)", data)
        if detail_match:
            period_key = detail_match.group(1)
            xboard_user_id = int(detail_match.group(2))
            label, window = periods[period_key]
            await query.answer("正在查询 IP，请稍候...")
            result = await asyncio.to_thread(
                query_user_ips_from_cache_sync,
                cache_path,
                xboard_user_id,
                label,
                window,
            )
            await show_callback_page(query, result, detail_keyboard(period_key), parse_mode="HTML")
            return

        key = data.split(":", 1)[-1]
        if key not in periods:
            await query.answer("请求无效，请重新进入。", show_alert=True)
            return

        await query.answer("正在生成查询，请稍候...")
        await open_dashboard_card(query, f"ip_{key}")

    async def ip_detail_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
                user_ip_ignore_list_keyboard(context, dimension, kind, xboard_user_id, detail_page, list_page, source),
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
                user_ip_ignore_list_keyboard(context, dimension, kind, xboard_user_id, detail_page, list_page, source),
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
                user_ip_ignore_list_keyboard(context, dimension, kind, xboard_user_id, detail_page, list_page, source),
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


    async def alert_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
            await show_callback_page(query, text, alert_user_setting_keyboard_for_source(alert_type, xboard_user_id, source), parse_mode="HTML", auto_delete=(source != "alert"))
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
            await show_callback_page(query, text, alert_global_keyboard(alert_type), parse_mode="HTML")
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
            await show_callback_page(query, text, alert_global_keyboard(alert_type), parse_mode="HTML")
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
            await show_callback_page(query, text, alert_user_setting_keyboard(alert_type, xboard_user_id), parse_mode="HTML")
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
            await show_callback_page(query, text, alert_user_setting_keyboard(alert_type, xboard_user_id), parse_mode="HTML")
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
            await show_callback_page(query, text, alert_user_setting_keyboard(alert_type, xboard_user_id), parse_mode="HTML")
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
            await show_callback_page(query, text, alert_user_setting_keyboard(alert_type, xboard_user_id), parse_mode="HTML")
            return

        await query.answer("请求无效，请重新进入。", show_alert=True)

    async def close_message_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.message:
            return
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                return
            await query.answer("未授权", show_alert=True)
            return
        await query.answer("已关闭")
        try:
            await query.message.delete()
        except BadRequest:
            pass

    async def detail_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.message:
            return
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                return
            await query.answer("未授权", show_alert=True)
            return
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
            result = await asyncio.to_thread(list_user_ips_from_cache_sync, cache_path, label, window)
            await show_callback_page(query, result, active_users_keyboard(target), parse_mode="HTML")
            return
        await show_callback_page(query, "🌐 请选择在线记录统计周期：", active_users_keyboard())

    async def user_ip_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_message:
            return
        if not is_allowed(update, cfg):
            if is_bot_self_update(update, cfg):
                return
            await reply_connection_status(update, cfg)
            return
        context.user_data["awaiting_user_ip_query_id"] = True
        context.user_data.pop("user_ip_query_period", None)
        sent = await update.effective_message.reply_text("🔎 请输入要查询的用户 ID，例如：1")
        await track_auto_delete_message(sent)

    async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
            await edit_global_alert_prompt(result, alert_global_keyboard(alert_type), "HTML")
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
            await edit_alert_prompt(result, alert_user_setting_keyboard(alert_type, xboard_user_id), "HTML")
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

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear_history", clear_history_command))
    app.add_handler(CommandHandler(
        "version",
        lambda update, context: handle_version_command(
            update,
            context,
            bot_ctx,
            reply_cover_card,
            edit_or_replace_status_any,
            delete_trigger_command_message,
            reply_connection_status,
        ),
    ))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("health", health_command))
    app.add_handler(CommandHandler("active_users", active_users))
    app.add_handler(CommandHandler("user_ip_query", user_ip_query))
    app.add_handler(CommandHandler("traffic_daily", traffic_daily))
    app.add_handler(CommandHandler("traffic_users", traffic_users))
    app.add_handler(CommandHandler("traffic_nodes", traffic_nodes))
    app.add_handler(CallbackQueryHandler(
        lambda update, context: handle_version_update_callback(
            update,
            context,
            bot_ctx,
            show_callback_page,
            answer_callback_silently,
        ),
        pattern=r"^version_update:(?:start|confirm):v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$|^version_update:cancel$",
    ))
    app.add_handler(CallbackQueryHandler(main_menu_callback, pattern=r"^main_menu(?::(init_ack|clear_history|clear_history_confirm|system_check|system_check_refresh|status_notice|traffic_management|traffic_users|traffic_nodes|traffic_alerts|op_logs(?::(?:traffic_alert|ip_alert|ip_ignore|reset_cache|reset_ip|parameter_config|auth)(?::\d+)?)?|auth(?::(?:add|delete|del_done|del_toggle:\d+|del_confirm|roles|role_toggle:\d+|role_save))?|ip_monitor(?::(?:period|user_query|ignore|ignored_rules:\d+|ignored_rule_toggle:\d+:[A-Za-z0-9]+|ignore:(?:area|asn|cidr):\d+|ignore_toggle:(?:area|asn|cidr):\d+:[A-Za-z0-9]+))?|noop|parameter_config(?::(?:cover|cover_reset|nickname|nickname_reset|cache_retention|cache_retention_select:(?:1m|1q|1y|all)|cache_retention_confirm:(?:1m|1q|1y|all)))?|notifications(?::(?:daily|weekly|monthly|collector|traffic_alert|ip_alert|version_update))?|debug_tools|debug:reset_cache|debug:reset_cache_now|debug:reset_cache_now_confirm|debug:reset_cache_floor|debug:reset_user_ip|debug:reset_user_ip_page:\d+|debug:reset_user_ip_toggle:\d+:\d+|debug:reset_user_ip_done|debug:reset_user_ip_multi_confirm))?$"))
    app.add_handler(CallbackQueryHandler(alert_callback, pattern=r"^(alert_menu:(?:traffic|ip)|alert_period_page:(?:traffic|ip):\d+|alert_global_period_page:(?:traffic|ip)|alert_global:(?:traffic|ip)(?::(?:custom|period:(?:1h|24h|7d|today|week)))?|alert_users:(?:traffic|ip):\d+|alert_user:(?:traffic|ip):\d+(?::alert)?|alert_set:(?:traffic|ip):(?:custom:\d+|period:(?:1h|24h|7d|today|week):\d+|threshold:\d+:\d+|whitelist:\d+|reset:\d+))$"))
    app.add_handler(CallbackQueryHandler(traffic_daily_callback, pattern=r"^(traffic_menu(?::[A-Za-z0-9_]+)?|traffic_back:[A-Za-z0-9_]+|traffic_(?:period|switch):(preset_1h|preset_24h|preset_7d|preset_30d|today|yesterday|this_week|this_month)(?::(?:users|nodes))?|ip_custom:start|traffic_custom:(start(?::(?:combined|users|nodes))?|now|(year|month|day|hour|minute):\d+|back:(year|month|day|hour))|traffic_floor:(start|confirm:\d+)|traffic_dashboard:(pin|unpin|delete):[A-Za-z0-9_]+)$"))
    app.add_handler(CallbackQueryHandler(active_users_callback, pattern=r"^(active_users(?::|_query:)(1h|24h|7d|30d)(?::\d+)?|ip_user_query:(?:(1h|24h|7d|30d)|custom:\d+:\d+)|user_ip_page:\d+:\d+:(?:all|(?:1h|24h|7d|30d)|custom:\d+:\d+)|active_user_detail:(1h|24h|7d|30d):\d+|active_users_cancel:(1h|24h|7d|30d)|noop)$"))
    app.add_handler(CallbackQueryHandler(ip_detail_callback, pattern=r"^(?:ip_(?:detail_list|active_user_detail):(ip_(?:1h|24h|7d|30d)|iprange_\d+_\d+):(\d+)(?::\d+)?(?::alert)?|ip_alert_notice:\d+|ip_ignore_menu:(ip_(?:1h|24h|7d|30d)|iprange_\d+_\d+):\d+:\d+(?::alert)?|ip_ignore_page:(?:area|asn|cidr):(ip_(?:1h|24h|7d|30d)|iprange_\d+_\d+):\d+:\d+:\d+(?::alert)?|ip_ig_t:[A-Za-z0-9]+|ip_ignore_toggle:(?:area|asn|cidr):(ip_(?:1h|24h|7d|30d)|iprange_\d+_\d+):\d+:\d+:\d+:[A-Za-z0-9]+(?::alert)?)$"))
    app.add_handler(CallbackQueryHandler(detail_back_callback, pattern=r"^detail_back:(1h|24h|7d|30d|menu)$"))
    app.add_handler(CallbackQueryHandler(close_message_callback, pattern=r"^close_message$"))
    app.add_handler(MessageHandler(filters.ALL, fallback))
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

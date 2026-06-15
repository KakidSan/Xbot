from __future__ import annotations

# Transitional application entrypoint. Most helpers are still imported from the
# legacy module while they are being split into runtime/message/permission
# modules. Keeping build_application/main here prevents legacy.py from remaining
# the startup core.
from .handlers.legacy import *  # noqa: F403
from .context import BotContext, BotRuntime


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














    from .router import register_handlers

    runtime = BotRuntime(
        bot_ctx=bot_ctx,
        reply_main_menu=reply_main_menu,
        delete_trigger_command_message=delete_trigger_command_message,
        track_auto_delete_message=track_auto_delete_message,
        reply_cover_card=reply_cover_card,
        edit_or_replace_status_any=edit_or_replace_status_any,
        reply_connection_status=reply_connection_status,
        reply_long_text=reply_long_text,
        send_or_jump_traffic_dashboard=send_or_jump_traffic_dashboard,
        traffic_custom_state=traffic_custom_state,
        traffic_custom_prompt_text=traffic_custom_prompt_text,
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

from __future__ import annotations

from functools import partial

from ..common import (
    Any,
    BadRequest,
    CACHE_RETENTION_OPTIONS,
    ContextTypes,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Path,
    ReplyKeyboardRemove,
    Update,
    asyncio,
    datetime,
    html,
    is_admin_user_id,
    log,
    timedelta,
)
from ..config import AppConfig
from ..db.cache import (
    auto_delete_message_delete_sync,
    auto_delete_message_is_pinned_sync,
    auto_delete_message_set_sync,
    cache_retention_days_sync,
    cache_retention_label,
    clear_message_tracking_for_chat_sync,
    format_timestamp,
    initialization_progress_text_sync,
    initialization_status_sync,
    pinned_dashboard_delete_message_sync,
    pinned_dashboard_set_sync,
    ui_pref_get_sync,
    ui_pref_set_sync,
)
from .formatters import traffic_dashboard_text_from_kind_sync, user_display
from .keyboards import (
    traffic_custom_available_bounds,
    traffic_custom_single_year,
    traffic_dashboard_keyboard,
)
from .context import user_data_of
from .menus import main_menu_keyboard
from .permissions import is_allowed, is_bot_self_update, user_id


def cache_retention_text_sync(cache_path: Path) -> str:
    days = cache_retention_days_sync(cache_path)
    return "\n".join(
        [
            "🗄 <b>缓存保留时间</b>",
            "────────────",
            f"当前设置：<b>{html.escape(cache_retention_label(days))}</b>",
            "",
            "说明：超过保留时间的 Bot 本地缓存会自动清理；选择新周期并确认后，会立即删除超出期限的老缓存记录。",
            "不会修改 XBoard / MySQL / Redis。",
        ]
    )


def cache_retention_preview_text(option_key: str, preview: dict[str, int]) -> str:
    _days, label = CACHE_RETENTION_OPTIONS[option_key]
    cutoff = int(preview.get("cutoff_ts") or 0)
    cutoff_text = "不限制，保留全部历史" if cutoff <= 0 else format_timestamp(cutoff)
    return "\n".join(
        [
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
        ]
    )


def start_menu_text(
    update: Update, cfg: AppConfig, custom_name: str | None = None
) -> str:
    user = update.effective_user
    tg_name = (user.full_name or user.username or str(user.id)) if user else "用户"
    display_name = html.escape(str(custom_name or tg_name))
    uid = user.id if user else None
    role_emoji = "👑" if is_admin_user_id(uid, cfg) else "🎩"
    return f"{role_emoji} {display_name}，<b>请选择功能</b>"


def mark_no_auto_delete_message(
    no_auto_delete_message_keys: set[tuple[str, int]], message: Any | None
) -> None:
    if not message:
        return
    try:
        no_auto_delete_message_keys.add((str(message.chat_id), int(message.message_id)))
    except Exception as exc:
        log.debug("记录免自动删除消息失败：%s", exc)


async def track_auto_delete_message(
    cache_path: Path, message: Any | None, is_pinned: bool = False
) -> None:
    if not message:
        return
    try:
        await asyncio.to_thread(
            auto_delete_message_set_sync,
            cache_path,
            str(message.chat_id),
            message.message_id,
            is_pinned,
        )
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
                chunks.append(line[start : start + limit].rstrip("\n"))
            continue
        if len(current) + len(line) > limit:
            chunks.append(current.rstrip("\n"))
            current = line
        else:
            current += line
    if current:
        chunks.append(current.rstrip("\n"))
    return chunks


async def reply_long_text(
    track_auto_delete_message_func: Any,
    message: Any,
    text: str,
    parse_mode: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    chunks = split_telegram_text(text)
    for index, chunk in enumerate(chunks):
        markup = reply_markup if index == len(chunks) - 1 else None
        sent = await message.reply_text(
            chunk, parse_mode=parse_mode, reply_markup=markup
        )
        await track_auto_delete_message_func(sent)


async def show_callback_page(
    cache_path: Path,
    no_auto_delete_message_keys: set[tuple[str, int]],
    track_auto_delete_message_func: Any,
    query: Any,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = None,
    auto_delete: bool = True,
) -> None:
    if not query.message:
        return
    try:
        if (
            str(query.message.chat_id),
            int(query.message.message_id),
        ) in no_auto_delete_message_keys:
            auto_delete = False
    except Exception as exc:
        log.debug("读取自动删除状态失败：%s", exc)
    try:
        if query.message.text:
            await query.message.edit_text(
                text, parse_mode=parse_mode, reply_markup=reply_markup
            )
        elif query.message.caption:
            await query.message.edit_caption(
                caption=text, parse_mode=parse_mode, reply_markup=reply_markup
            )
        else:
            sent = await query.message.reply_text(
                text, parse_mode=parse_mode, reply_markup=reply_markup
            )
            if auto_delete:
                await track_auto_delete_message_func(sent)
            return
        if auto_delete:
            is_pinned = await asyncio.to_thread(
                auto_delete_message_is_pinned_sync,
                cache_path,
                str(query.message.chat_id),
                query.message.message_id,
            )
            await track_auto_delete_message_func(query.message, is_pinned=is_pinned)
    except Exception as exc:
        log.warning("编辑菜单消息失败，改为发送新消息：%s", exc)
        sent = await query.message.reply_text(
            text, parse_mode=parse_mode, reply_markup=reply_markup
        )
        if auto_delete:
            await track_auto_delete_message_func(sent)


async def answer_callback_silently(query: Any) -> None:
    try:
        await query.answer()
    except BadRequest as exc:
        if "Query is too old" in str(exc) or "query id is invalid" in str(exc):
            log.debug("忽略已过期 callback 确认：%s", exc)
            return
        raise


async def show_initialization_gate(
    cache_path: Path,
    cfg: AppConfig,
    answer_callback_silently_func: Any,
    show_callback_page_func: Any,
    query: Any,
) -> bool:
    init_status = await asyncio.to_thread(
        initialization_status_sync, cache_path, cfg.ip_geo_queries_per_minute
    )
    if not init_status.get("initializing"):
        return False
    await answer_callback_silently_func(query)
    text = await asyncio.to_thread(initialization_progress_text_sync, cache_path, cfg)
    if init_status.get("awaiting_ack"):
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🏠 进入主菜单", callback_data="main_menu:init_ack"
                    )
                ]
            ]
        )
    else:
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔄 刷新初始化进度", callback_data="main_menu")]]
        )
    await show_callback_page_func(query, text, keyboard, parse_mode="HTML")
    return True


async def context_bot_delete_message(
    app: Any, chat_id: int | str, message_id: int
) -> bool:
    try:
        await app.bot.delete_message(chat_id=chat_id, message_id=message_id)
        return True
    except Exception:
        return False


async def purge_chat_history(
    cache_path: Path,
    context_bot_delete_message_func: Any,
    chat_id: int | str,
    from_message_id: int,
) -> tuple[int, int]:
    deleted = 0
    failed = 0
    start_id = max(1, int(from_message_id))
    batch_size = 25
    for batch_start in range(start_id, 0, -batch_size):
        tasks = [
            context_bot_delete_message_func(chat_id, message_id)
            for message_id in range(batch_start, max(0, batch_start - batch_size), -1)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for ok in results:
            if ok is True:
                deleted += 1
            else:
                failed += 1
        await asyncio.sleep(0.08)
    await asyncio.to_thread(
        clear_message_tracking_for_chat_sync, cache_path, str(chat_id)
    )
    return deleted, failed


async def delete_trigger_command_message(
    context_bot_delete_message_func: Any, update: Update
) -> None:
    message = update.effective_message
    if not message:
        return
    try:
        await context_bot_delete_message_func(message.chat_id, message.message_id)
    except Exception as exc:
        log.debug("删除触发命令消息失败：%s", exc)


async def send_start_menu(
    cache_path: Path,
    cfg: AppConfig,
    track_auto_delete_message_func: Any,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not update.effective_message:
        return
    uid = user_id(update)
    init_status = await asyncio.to_thread(
        initialization_status_sync, cache_path, cfg.ip_geo_queries_per_minute
    )
    if init_status.get("initializing"):
        text = await asyncio.to_thread(
            initialization_progress_text_sync, cache_path, cfg
        )
        sent = await update.effective_message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🏠 进入主菜单", callback_data="main_menu:init_ack"
                        )
                    ]
                ]
                if init_status.get("awaiting_ack")
                else [
                    [
                        InlineKeyboardButton(
                            "🔄 刷新初始化进度", callback_data="main_menu"
                        )
                    ]
                ]
            ),
        )
        await track_auto_delete_message_func(sent)
        return
    custom_name = (
        await asyncio.to_thread(ui_pref_get_sync, cache_path, uid, "nickname")
        if uid is not None
        else None
    )
    custom_cover = (
        await asyncio.to_thread(ui_pref_get_sync, cache_path, uid, "cover_file_id")
        if uid is not None
        else None
    )
    text = start_menu_text(update, cfg, custom_name)
    is_admin = is_admin_user_id(uid, cfg)
    if custom_cover:
        try:
            sent = await update.effective_message.reply_photo(
                photo=custom_cover,
                caption=text,
                parse_mode="HTML",
                reply_markup=main_menu_keyboard(is_admin),
            )
            await track_auto_delete_message_func(sent)
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
            await track_auto_delete_message_func(sent)
            return
    except Exception as exc:
        log.warning("读取 Bot 头像失败，改为发送文本菜单：%s", exc)
    sent = await update.effective_message.reply_text(
        text, parse_mode="HTML", reply_markup=main_menu_keyboard(is_admin)
    )
    await track_auto_delete_message_func(sent)


async def reply_cover_card(
    cache_path: Path,
    track_auto_delete_message_func: Any,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
):
    if not update.effective_message:
        return None
    uid = user_id(update)
    custom_cover = (
        await asyncio.to_thread(ui_pref_get_sync, cache_path, uid, "cover_file_id")
        if uid is not None
        else None
    )
    if custom_cover:
        try:
            sent = await update.effective_message.reply_photo(
                photo=custom_cover,
                caption=text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
            await track_auto_delete_message_func(sent)
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
            await track_auto_delete_message_func(sent)
            return sent
    except Exception as exc:
        log.warning("读取 Bot 头像失败，改为发送文本状态卡片：%s", exc)
    sent = await update.effective_message.reply_text(
        text, parse_mode="HTML", reply_markup=reply_markup
    )
    await track_auto_delete_message_func(sent)
    return sent


async def reply_main_menu(
    cache_path: Path,
    cfg: AppConfig,
    send_start_menu_func: Any,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    _cfg: AppConfig | None = None,
) -> None:
    if not update.effective_message:
        return
    uid = user_id(update)
    if not is_allowed(update, cfg):
        if is_bot_self_update(update, cfg):
            log.info("忽略 Bot 自身更新：%s", user_display(update))
            return
        log.warning("拒绝未授权 Telegram 用户：%s", user_display(update))
        await update.effective_message.reply_html(
            f"Telegram 用户 <code>{uid or 'unknown'}</code> 不在授权名单中。\n请联系管理员授权后再使用。",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    if uid is not None and update.effective_user:
        label_parts = [
            update.effective_user.full_name
            or update.effective_user.username
            or str(uid)
        ]
        if update.effective_user.username:
            label_parts.append(f"@{update.effective_user.username}")
        await asyncio.to_thread(
            ui_pref_set_sync,
            cache_path,
            uid,
            "telegram_label",
            " ".join(dict.fromkeys(label_parts)),
        )
    await send_start_menu_func(update, context)


async def resolve_telegram_user_label(app: Any, cache_path: Path, uid: int) -> str:
    try:
        chat = await app.bot.get_chat(uid)
        name = (
            getattr(chat, "full_name", None)
            or getattr(chat, "username", None)
            or str(uid)
        )
        username = getattr(chat, "username", None)
        if username and username not in str(name):
            name = f"{name} (@{username})"
        await asyncio.to_thread(
            ui_pref_set_sync, cache_path, uid, "telegram_label", str(name)
        )
        return str(name)
    except Exception:
        cached = await asyncio.to_thread(
            ui_pref_get_sync, cache_path, uid, "telegram_label"
        )
        return str(cached or f"用户 {uid}")


def traffic_dashboard_text(cache_path: Path, kind: str) -> str:
    return traffic_dashboard_text_from_kind_sync(cache_path, kind)


async def auto_delete_unpinned_dashboard(
    app: Any, cache_path: Path, chat_id: str, message_id: int, kind: str
) -> None:
    await asyncio.sleep(180)
    is_pinned = await asyncio.to_thread(
        auto_delete_message_is_pinned_sync, cache_path, chat_id, message_id
    )
    if is_pinned:
        return
    try:
        await app.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except BadRequest as exc:
        log.debug(
            "删除临时面板消息失败 chat=%s message=%s：%s", chat_id, message_id, exc
        )
    await asyncio.to_thread(
        pinned_dashboard_delete_message_sync, cache_path, chat_id, message_id
    )
    await asyncio.to_thread(
        auto_delete_message_delete_sync, cache_path, chat_id, message_id
    )


async def send_dashboard_card(
    app: Any, cache_path: Path, message: Any, kind: str, user_pref_id: int | None = None
) -> None:
    chat_id = str(message.chat_id)
    text = await asyncio.to_thread(traffic_dashboard_text, cache_path, kind)
    reply_markup = traffic_dashboard_keyboard(kind, is_pinned=False)
    custom_cover = None
    try:
        sender = getattr(message, "from_user", None)
        pref_id = user_pref_id or (sender.id if sender else None)
        if pref_id:
            custom_cover = await asyncio.to_thread(
                ui_pref_get_sync, cache_path, pref_id, "cover_file_id"
            )
    except Exception:
        custom_cover = None
    sent = None
    if custom_cover:
        try:
            sent = await message.reply_photo(
                photo=custom_cover,
                caption=text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
        except Exception as exc:
            log.warning("发送自定义题图结果失败，改为文本消息：%s", exc)
    if sent is None:
        try:
            me = await app.bot.get_me()
            photos = await app.bot.get_user_profile_photos(me.id, limit=1)
            if photos.total_count > 0 and photos.photos:
                sent = await message.reply_photo(
                    photo=photos.photos[0][-1].file_id,
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                )
        except Exception as exc:
            log.warning("发送 Bot 头像结果失败，改为文本消息：%s", exc)
    if sent is None:
        sent = await message.reply_text(
            text, parse_mode="HTML", reply_markup=reply_markup
        )
    await asyncio.to_thread(
        pinned_dashboard_set_sync, cache_path, kind, chat_id, sent.message_id, False
    )
    await asyncio.to_thread(
        auto_delete_message_set_sync, cache_path, chat_id, sent.message_id, False
    )
    asyncio.create_task(
        auto_delete_unpinned_dashboard(app, cache_path, chat_id, sent.message_id, kind)
    )


async def edit_dashboard_card(
    cache_path: Path, show_callback_page_func: Any, query: Any, kind: str
) -> None:
    chat_id = str(query.message.chat_id)
    text = await asyncio.to_thread(traffic_dashboard_text, cache_path, kind)
    is_pinned = await asyncio.to_thread(
        auto_delete_message_is_pinned_sync,
        cache_path,
        chat_id,
        query.message.message_id,
    )
    reply_markup = traffic_dashboard_keyboard(kind, is_pinned=is_pinned)
    await show_callback_page_func(query, text, reply_markup, parse_mode="HTML")
    await asyncio.to_thread(
        pinned_dashboard_set_sync,
        cache_path,
        kind,
        chat_id,
        query.message.message_id,
        is_pinned,
    )
    await asyncio.to_thread(
        auto_delete_message_set_sync,
        cache_path,
        chat_id,
        query.message.message_id,
        is_pinned,
    )


async def open_dashboard_card(
    send_dashboard_card_func: Any, query: Any, kind: str
) -> None:
    if not query.message:
        return
    sender = getattr(query, "from_user", None)
    await send_dashboard_card_func(query.message, kind, sender.id if sender else None)


def traffic_custom_state(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
    state = user_data_of(context).setdefault("traffic_custom", {})
    return state if isinstance(state, dict) else {}


def traffic_custom_enter_initial_step(cache_path: Path, state: dict[str, Any]) -> None:
    if state.get("mode") in {"custom", "ip_custom"} and traffic_custom_single_year(
        cache_path
    ):
        _, now_ts = traffic_custom_available_bounds(cache_path)
        state["year"] = datetime.fromtimestamp(now_ts).year
        state["step"] = "month"
    else:
        state.pop("year", None)
        state["step"] = "year"


def traffic_custom_prompt_text(cache_path: Path, state: dict[str, Any]) -> str:
    first_ts, _ = traffic_custom_available_bounds(cache_path)
    step = str(state.get("step") or "year")
    step_label = {
        "year": "年份",
        "month": "月份",
        "day": "日期",
        "hour": "小时",
        "minute": "分钟",
    }.get(step, "时间")

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
        return "\n".join(
            [
                "⚙️ 调整起始点",
                f"请选择新的统计起始点的{step_label}。",
                f"（当前可选择的最早时间：{format_timestamp(first_ts)}）",
                f"已选起始点：{selected_combo_text()}",
                "确认后会删除该时间之前的本地统计缓存，后续周期统计也不会再使用这些旧数据。",
            ]
        )
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
        return int(start.timestamp()), int(now.timestamp()), "今天"
    if kind == "yesterday":
        start = (now - timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end = start.replace(hour=23, minute=59, second=59)
        return int(start.timestamp()), int(end.timestamp()), "昨天"
    if kind == "this_week":
        start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return int(start.timestamp()), int(now.timestamp()), "本周"
    if kind == "this_month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return int(start.timestamp()), int(now.timestamp()), "本月"
    return None


async def send_or_jump_traffic_dashboard(
    send_dashboard_card_func: Any, message: Any, kind: str
) -> None:
    sender = getattr(message, "from_user", None)
    await send_dashboard_card_func(message, kind, sender.id if sender else None)


async def open_traffic_dashboard_message(
    send_or_jump_traffic_dashboard_func: Any, query: Any, kind: str
) -> None:
    if not query.message:
        return
    await query.answer("正在生成查询，请稍候...")
    await send_or_jump_traffic_dashboard_func(query.message, kind)


async def switch_traffic_dashboard_message(
    edit_dashboard_card_func: Any, query: Any, kind: str
) -> None:
    if not query.message:
        return
    await query.answer("正在切换周期，请稍候...")
    await edit_dashboard_card_func(query, kind)


async def edit_message_prompt(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int | str | None,
    message_id: int | None,
    message_text: str,
    keyboard: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = "HTML",
) -> None:
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
            log.warning("编辑原文本消息失败：%s", exc)
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
            log.warning("编辑原图片说明失败：%s", exc)


def build_runtime_services(
    app: Any, cfg: AppConfig, cache_path: Path
) -> dict[str, Any]:
    no_auto_delete_message_keys: set[tuple[str, int]] = set()
    track = partial(track_auto_delete_message, cache_path)
    context_delete = partial(context_bot_delete_message, app)
    show_page = partial(
        show_callback_page, cache_path, no_auto_delete_message_keys, track
    )
    send_start = partial(send_start_menu, cache_path, cfg, track)
    send_dashboard = partial(send_dashboard_card, app, cache_path)
    edit_dashboard = partial(edit_dashboard_card, cache_path, show_page)
    send_or_jump = partial(send_or_jump_traffic_dashboard, send_dashboard)
    return {
        "reply_main_menu": partial(reply_main_menu, cache_path, cfg, send_start),
        "delete_trigger_command_message": partial(
            delete_trigger_command_message, context_delete
        ),
        "track_auto_delete_message": track,
        "reply_cover_card": partial(reply_cover_card, cache_path, track),
        "reply_long_text": partial(reply_long_text, track),
        "send_or_jump_traffic_dashboard": send_or_jump,
        "traffic_custom_state": traffic_custom_state,
        "traffic_custom_prompt_text": partial(traffic_custom_prompt_text, cache_path),
        "show_callback_page": show_page,
        "answer_callback_silently": answer_callback_silently,
        "cache_retention_text_sync": partial(cache_retention_text_sync, cache_path),
        "cache_retention_preview_text": cache_retention_preview_text,
        "show_initialization_gate": partial(
            show_initialization_gate,
            cache_path,
            cfg,
            answer_callback_silently,
            show_page,
        ),
        "send_start_menu": send_start,
        "open_dashboard_card": partial(open_dashboard_card, send_dashboard),
        "purge_chat_history": partial(purge_chat_history, cache_path, context_delete),
        "resolve_telegram_user_label": partial(
            resolve_telegram_user_label, app, cache_path
        ),
        "mark_no_auto_delete_message": partial(
            mark_no_auto_delete_message, no_auto_delete_message_keys
        ),
        "send_dashboard_card": send_dashboard,
        "edit_dashboard_card": edit_dashboard,
        "open_traffic_dashboard_message": partial(
            open_traffic_dashboard_message, send_or_jump
        ),
        "switch_traffic_dashboard_message": partial(
            switch_traffic_dashboard_message, edit_dashboard
        ),
        "context_bot_delete_message": context_delete,
        "edit_global_alert_prompt": edit_message_prompt,
        "edit_alert_prompt": edit_message_prompt,
    }

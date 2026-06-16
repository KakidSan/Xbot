from __future__ import annotations

from ...common import (
    BadRequest,
    ContextTypes,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Path,
    Update,
    asyncio,
    datetime,
    field,
    ip_range_kind,
    re,
    timedelta,
)
from ...config import AppConfig
from ...db.cache import (
    active_user_button_items_from_cache_sync,
    auto_delete_message_delete_sync,
    auto_delete_message_set_sync,
    count_user_ips_from_cache_sync,
    format_timestamp,
    pinned_dashboard_delete_sync,
    pinned_dashboard_set_sync,
    preview_prune_stats_before_sync,
    prune_stats_before_sync,
    query_user_ips_from_cache_sync,
    save_traffic_range_sync,
    traffic_dimension_from_kind,
    traffic_kind_for_dimension,
    list_user_ips_from_cache_sync,
)
from ..context import BotContext
from ..keyboards import (
    active_users_keyboard,
    detail_keyboard,
    traffic_custom_keyboard_for_state,
    traffic_custom_year_keyboard,
    traffic_dashboard_keyboard,
    traffic_floor_confirm_keyboard,
    traffic_period_keyboard,
    user_ip_query_page_keyboard,
)
from ..menus import back_close_row
from ..messaging import traffic_custom_enter_initial_step, traffic_fixed_range
from ..operation_logs import log_operation_from_query as log_operation_from_query_with_cache
from ...db.cache import make_range_kind
from ..permissions import is_allowed, is_bot_self_update


async def handle_active_users_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, *, cfg: AppConfig, cache_path: Path, show_initialization_gate, answer_callback_silently, show_callback_page, open_dashboard_card) -> None:
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

async def handle_traffic_daily_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, *, cfg: AppConfig, bot_ctx: BotContext, cache_path: Path, show_initialization_gate, answer_callback_silently, show_callback_page, send_dashboard_card, edit_dashboard_card, open_traffic_dashboard_message, switch_traffic_dashboard_message) -> None:
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
        traffic_custom_enter_initial_step(cache_path, state)
        await answer_callback_silently(query)
        await show_callback_page(query, traffic_custom_prompt_text(state), traffic_custom_keyboard_for_state(cache_path, state))
        return

    traffic_custom_start_match = re.fullmatch(r"traffic_custom:start(?::(combined|users|nodes))?", data)
    if traffic_custom_start_match:
        dimension = traffic_custom_start_match.group(1) or "combined"
        state = traffic_custom_state(context)
        state.clear()
        state.update({"mode": "custom", "dimension": dimension, "phase": "start"})
        traffic_custom_enter_initial_step(cache_path, state)
        await answer_callback_silently(query)
        await show_callback_page(query, traffic_custom_prompt_text(state), traffic_custom_keyboard_for_state(cache_path, state))
        return

    if data == "traffic_floor:start":
        state = traffic_custom_state(context)
        state.clear()
        state.update({"mode": "floor", "phase": "floor", "step": "year"})
        await answer_callback_silently(query)
        await show_callback_page(query, traffic_custom_prompt_text(state), traffic_custom_year_keyboard(cache_path, str(state.get("mode") or "")))
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
            await show_callback_page(query, traffic_custom_prompt_text(state), traffic_custom_keyboard_for_state(cache_path, state))
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
            traffic_custom_enter_initial_step(cache_path, state)
            await query.answer("开始时间已选择")
            await show_callback_page(query, traffic_custom_prompt_text(state), traffic_custom_keyboard_for_state(cache_path, state))
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
        await show_callback_page(query, traffic_custom_prompt_text(state), traffic_custom_keyboard_for_state(cache_path, state))
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

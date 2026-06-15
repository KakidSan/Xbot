from __future__ import annotations

from ...common import ContextTypes, InlineKeyboardButton, InlineKeyboardMarkup, Path, Update, asyncio, html, is_admin_user_id, re
from ...config import AppConfig
from ...db.cache import (
    clear_user_ip_records_multi_sync,
    format_timestamp,
    list_all_cached_user_buttons_sync,
    preview_clear_user_ip_records_multi_sync,
    reset_local_cache_sync,
    upsert_all_cache_users,
)
from ..context import BotContext
from ..keyboards import reset_user_ip_select_keyboard, traffic_custom_year_keyboard
from ..menus import debug_tools_keyboard, reset_cache_confirm_keyboard, reset_cache_keyboard, reset_user_ip_multi_confirm_keyboard
from ..operation_logs import log_operation_from_query as log_operation_from_query_with_cache


async def debug_callback(
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
    traffic_custom_state,
    traffic_custom_prompt_text,
) -> bool:
    if not data.startswith("main_menu:debug"):
        return False
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
            traffic_custom_year_keyboard(cache_path, str(state.get("mode") or "")),
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
    return True

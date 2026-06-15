from __future__ import annotations

from ...common import ContextTypes, InlineKeyboardMarkup, Update, asyncio, html, is_admin_user_id, is_super_admin_user_id, re
from ...config import AppConfig
from ...db.cache import update_telegram_roles_in_cache_sync
from ..authorization import (
    authorization_delete_confirm_keyboard as authorization_delete_confirm_keyboard_for_cfg,
    authorization_delete_keyboard as authorization_delete_keyboard_for_cfg,
    authorization_manage_keyboard as authorization_manage_keyboard_for_cfg,
    authorization_role_change_keyboard as authorization_role_change_keyboard_for_cfg,
    authorization_role_change_text as authorization_role_change_text_for_cfg,
    telegram_authorization_list_text_sync as telegram_authorization_list_text_for_cfg,
)
from ..context import BotContext
from ..menus import back_close_row
from ..operation_details import auth_change_detail
from ..operation_logs import log_operation_from_query_with_cache


async def auth_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    cfg: AppConfig,
    bot_ctx: BotContext,
    cache_path,
    data: str,
    query,
    answer_callback_silently,
    show_callback_page,
    resolve_telegram_user_label,
) -> bool:
    if not data.startswith("main_menu:auth"):
        return False
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
    return True

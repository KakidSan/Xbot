from __future__ import annotations

from .context import user_data_of
from ..common import ContextTypes, InlineKeyboardButton, InlineKeyboardMarkup, html
from ..config import AppConfig
from ..db.cache import ui_pref_get_sync


def telegram_user_label_sync(cache_path, uid: int) -> str:
    cached = ui_pref_get_sync(cache_path, uid, "telegram_label")
    return str(cached or f"用户 {uid}")


def telegram_authorization_list_text_sync(cfg: AppConfig, cache_path) -> str:
    lines = ["🔑 <b>授权管理</b>", "────────────", "当前用户列表："]
    super_admin_ids = sorted(cfg.telegram.super_admin_user_ids)
    for uid in super_admin_ids:
        lines.append(
            f"• 👑 <b>{html.escape(telegram_user_label_sync(cache_path, uid))}</b> (<code>{uid}</code>)"
        )
    for uid in sorted(cfg.telegram.manager_user_ids):
        lines.append(
            f"• 👑 {html.escape(telegram_user_label_sync(cache_path, uid))} (<code>{uid}</code>)"
        )
    for uid in sorted(cfg.telegram.authorized_user_ids):
        lines.append(
            f"• 🎩 {html.escape(telegram_user_label_sync(cache_path, uid))} (<code>{uid}</code>)"
        )
    if (
        not super_admin_ids
        and not cfg.telegram.manager_user_ids
        and not cfg.telegram.authorized_user_ids
    ):
        lines.append("暂无授权用户。")
    lines.extend(["", "说明：超级管理员只能通过环境变量修改。"])
    return "\n".join(lines)


def auth_user_ids_to_labels(cache_path, value: str) -> str:
    raw = value.strip()
    if not raw or raw == "空":
        return raw or "空"
    labels: list[str] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if item.isdigit():
            labels.append(telegram_user_label_sync(cache_path, int(item)))
        else:
            labels.append(item)
    return ", ".join(labels) or "空"


def authorization_manage_keyboard(super_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("🔐 增加授权", callback_data="main_menu:auth:add"),
            InlineKeyboardButton("🔓 删除授权", callback_data="main_menu:auth:delete"),
        ]
    ]
    if super_admin:
        rows.append(
            [InlineKeyboardButton("🎭 权限变更", callback_data="main_menu:auth:roles")]
        )
    rows.append(
        [
            InlineKeyboardButton("⬅️ 返回主菜单", callback_data="main_menu"),
            InlineKeyboardButton("❌ 关闭", callback_data="close_message"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def authorization_delete_keyboard(
    cfg: AppConfig,
    cache_path,
    context: ContextTypes.DEFAULT_TYPE,
    super_admin: bool = False,
) -> InlineKeyboardMarkup:
    selected = user_data_of(context).get("auth_delete_selected") or set()
    if not isinstance(selected, set):
        selected = set(selected or [])
    rows = []
    deletable: list[tuple[int, str]] = [
        (uid, "🎩") for uid in sorted(cfg.telegram.authorized_user_ids)
    ]
    if super_admin:
        deletable = [
            (uid, "👑") for uid in sorted(cfg.telegram.manager_user_ids)
        ] + deletable
    for uid, emoji in deletable:
        mark = "✅ " if uid in selected else ""
        label = f"{mark}{emoji} {telegram_user_label_sync(cache_path, uid)} ({uid})"
        rows.append(
            [
                InlineKeyboardButton(
                    label[:64], callback_data=f"main_menu:auth:del_toggle:{uid}"
                )
            ]
        )
    if rows:
        rows.append(
            [
                InlineKeyboardButton(
                    "✅ 完成选择", callback_data="main_menu:auth:del_done"
                )
            ]
        )
    else:
        rows.append(
            [InlineKeyboardButton("暂无可删除授权用户", callback_data="main_menu:noop")]
        )
    rows.append(
        [
            InlineKeyboardButton("⬅️ 返回授权管理", callback_data="main_menu:auth"),
            InlineKeyboardButton("❌ 关闭", callback_data="close_message"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def authorization_delete_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ 确认删除", callback_data="main_menu:auth:del_confirm"
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ 返回选择", callback_data="main_menu:auth:delete"
                ),
                InlineKeyboardButton("❌ 关闭", callback_data="close_message"),
            ],
        ]
    )


def authorization_role_change_text(
    cfg: AppConfig, cache_path, context: ContextTypes.DEFAULT_TYPE
) -> str:
    role_changes = user_data_of(context).get("auth_role_changes") or {}
    if not isinstance(role_changes, dict):
        role_changes = {}
    lines = [
        "🎭 <b>权限变更</b>",
        "────────────",
        "点击用户即可在 🎩 普通用户 / 👑 普通管理员之间切换。",
        "未确认的变更会在下方显示；点击保存后才生效。",
    ]
    pending_lines = []
    for uid, role in sorted(role_changes.items(), key=lambda item: int(item[0])):
        target_role = str(role)
        current_role = (
            "manager" if int(uid) in cfg.telegram.manager_user_ids else "user"
        )
        if target_role == current_role:
            continue
        emoji = "👑" if target_role == "manager" else "🎩"
        role_label = "普通管理员" if target_role == "manager" else "普通用户"
        pending_lines.append(
            f"{emoji} {html.escape(telegram_user_label_sync(cache_path, int(uid)))} (<code>{int(uid)}</code>) → {role_label}"
        )
    if pending_lines:
        lines.extend(["", "待保存变更：", *pending_lines])
    return "\n".join(lines)


def authorization_role_change_keyboard(
    cfg: AppConfig, cache_path, context: ContextTypes.DEFAULT_TYPE
) -> InlineKeyboardMarkup:
    role_changes = user_data_of(context).get("auth_role_changes") or {}
    if not isinstance(role_changes, dict):
        role_changes = {}
    rows: list[list[InlineKeyboardButton]] = []
    candidates = [(uid, "manager") for uid in sorted(cfg.telegram.manager_user_ids)] + [
        (uid, "user") for uid in sorted(cfg.telegram.authorized_user_ids)
    ]
    for uid, current_role in candidates:
        target_role = str(role_changes.get(uid, current_role))
        emoji = "👑" if target_role == "manager" else "🎩"
        rows.append(
            [
                InlineKeyboardButton(
                    f"{emoji} {telegram_user_label_sync(cache_path, uid)} ({uid})"[:64],
                    callback_data=f"main_menu:auth:role_toggle:{uid}",
                )
            ]
        )
    if candidates:
        rows.append(
            [
                InlineKeyboardButton(
                    "💾 保存变更", callback_data="main_menu:auth:role_save"
                )
            ]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    "暂无可变更权限的用户", callback_data="main_menu:noop"
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton("⬅️ 返回授权管理", callback_data="main_menu:auth"),
            InlineKeyboardButton("❌ 关闭", callback_data="close_message"),
        ]
    )
    return InlineKeyboardMarkup(rows)

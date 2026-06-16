from __future__ import annotations

from ...common import (
    Any,
    CACHE_RETENTION_OPTIONS,
    ContextTypes,
    Path,
    Update,
    asyncio,
    html,
    re,
)
from ...config import AppConfig
from ...db.cache import (
    cache_retention_preview_sync,
    cache_retention_set_and_prune_sync,
    speedtest_jump_target_delete_sync,
    speedtest_jump_targets_sync,
    ui_pref_delete_sync,
)
from ..callback_data import cb_params, normalize_main_menu_callback
from ..context import BotContext, user_data_of
from ..keyboards import cache_retention_confirm_keyboard, cache_retention_keyboard
from ..menus import (
    back_close_row,
    cover_config_keyboard,
    nickname_config_keyboard,
    parameter_config_keyboard,
)
from ..operation_logs import (
    log_operation_from_query as log_operation_from_query_with_cache,
)
from ..permissions import is_allowed, is_bot_self_update


SPEEDTEST_JUMP_PAGE_SIZE = 5


async def handle_parameters_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    cfg: AppConfig,
    bot_ctx: BotContext,
    cache_path: Path,
    answer_callback_silently,
    show_callback_page,
    cache_retention_text_sync,
    cache_retention_preview_text,
) -> None:
    query = update.callback_query
    if not query or not query.message:
        return None
    if not is_allowed(update, cfg):
        if is_bot_self_update(update, cfg):
            return None
        await query.answer("未授权，无法使用该功能", show_alert=True)
        return None
    await parameter_callback(
        update,
        context,
        cfg=cfg,
        bot_ctx=bot_ctx,
        cache_path=cache_path,
        data=normalize_main_menu_callback(query.data or ""),
        query=query,
        answer_callback_silently=answer_callback_silently,
        show_callback_page=show_callback_page,
        cache_retention_text_sync=cache_retention_text_sync,
        cache_retention_preview_text=cache_retention_preview_text,
    )


def speedtest_jump_text(cache_path: Path, owner_user_id: int) -> str:
    targets = speedtest_jump_targets_sync(cache_path, owner_user_id)
    lines = ["🤖 <b>测试工具</b>", "────────────", "当前已添加的测速工具："]
    if targets:
        lines.extend(
            f"• {html.escape(str(row['nickname']))} (<code>{html.escape('@' + str(row.get('username')) if int(row['telegram_id']) < 0 and row.get('username') else str(int(row['telegram_id'])))}</code>)"
            for row in targets
        )
    else:
        lines.append("• 暂无")
    return "\n".join(lines)


def speedtest_jump_keyboard() -> Any:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "➕ 添加工具",
                    callback_data=cb_params("speedtest_jump", "add"),
                )
            ],
            [
                InlineKeyboardButton(
                    "➖ 删除工具",
                    callback_data=cb_params("speedtest_jump", "delete", 0),
                )
            ],
            back_close_row("params", "⬅️ 返回个人设置"),
        ]
    )


def speedtest_jump_delete_keyboard(
    cache_path: Path, owner_user_id: int, page: int = 0
) -> Any:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    targets = speedtest_jump_targets_sync(cache_path, owner_user_id)
    total = len(targets)
    pages = max(1, (total + SPEEDTEST_JUMP_PAGE_SIZE - 1) // SPEEDTEST_JUMP_PAGE_SIZE)
    page = max(0, min(int(page), pages - 1))
    rows: list[list[InlineKeyboardButton]] = []
    for row in targets[
        page * SPEEDTEST_JUMP_PAGE_SIZE : page * SPEEDTEST_JUMP_PAGE_SIZE
        + SPEEDTEST_JUMP_PAGE_SIZE
    ]:
        rows.append(
            [
                InlineKeyboardButton(
                    str(row["nickname"])[:60],
                    callback_data=cb_params(
                        "speedtest_jump", "delete_target", int(row["telegram_id"]), page
                    ),
                )
            ]
        )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                "⬅️ 上一页",
                callback_data=cb_params("speedtest_jump", "delete", page - 1),
            )
        )
    nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="noop"))
    if page + 1 < pages:
        nav.append(
            InlineKeyboardButton(
                "下一页 ➡️",
                callback_data=cb_params("speedtest_jump", "delete", page + 1),
            )
        )
    rows.append(nav)
    rows.append(back_close_row("params:speedtest_jump", "⬅️ 返回测试工具"))
    return InlineKeyboardMarkup(rows)


async def parameter_callback(
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
    cache_retention_text_sync,
    cache_retention_preview_text,
) -> bool | None:
    if not data.startswith("params"):
        return False
    if data == "params":
        await answer_callback_silently(query)
        await show_callback_page(
            query,
            "🎨 个人设置\n────────────\n请选择要配置的项目。",
            parameter_config_keyboard(),
        )
        return None

    if data == "params:speedtest_jump":
        await answer_callback_silently(query)
        await show_callback_page(
            query,
            await asyncio.to_thread(
                speedtest_jump_text, cache_path, query.from_user.id
            ),
            speedtest_jump_keyboard(),
            parse_mode="HTML",
        )
        return None

    if data == "params:speedtest_jump:add":
        user_data_of(context)["awaiting_speedtest_jump_id"] = True
        user_data_of(context).pop("awaiting_custom_cover", None)
        user_data_of(context).pop("awaiting_custom_nickname", None)
        await answer_callback_silently(query)
        await show_callback_page(
            query,
            "🤖 <b>添加测试工具</b>\n────────────\n请发送测试工具的 Telegram ID、@用户名 或 t.me 链接。",
            speedtest_jump_keyboard(),
            parse_mode="HTML",
        )
        return None

    delete_match = re.fullmatch(r"params:speedtest_jump:delete:(\d+)", data)
    if delete_match:
        page = int(delete_match.group(1))
        await answer_callback_silently(query)
        await show_callback_page(
            query,
            await asyncio.to_thread(
                speedtest_jump_text, cache_path, query.from_user.id
            ),
            await asyncio.to_thread(
                speedtest_jump_delete_keyboard, cache_path, query.from_user.id, page
            ),
            parse_mode="HTML",
        )
        return None

    delete_target_match = re.fullmatch(
        r"params:speedtest_jump:delete_target:(-?\d+):(\d+)", data
    )
    if delete_target_match:
        target_id = int(delete_target_match.group(1))
        page = int(delete_target_match.group(2))
        deleted = await asyncio.to_thread(
            speedtest_jump_target_delete_sync, cache_path, query.from_user.id, target_id
        )
        await query.answer("已删除" if deleted else "该跳转不存在")
        await show_callback_page(
            query,
            await asyncio.to_thread(
                speedtest_jump_text, cache_path, query.from_user.id
            ),
            await asyncio.to_thread(
                speedtest_jump_delete_keyboard, cache_path, query.from_user.id, page
            ),
            parse_mode="HTML",
        )
        return None

    if data == "params:cache_retention":
        await answer_callback_silently(query)
        await show_callback_page(
            query,
            cache_retention_text_sync(),
            cache_retention_keyboard(bot_ctx.cache_path),
            parse_mode="HTML",
        )
        return None

    retention_select_match = re.fullmatch(
        r"params:cache_retention_select:(1m|1y|all)", data
    )
    if retention_select_match:
        option_key = retention_select_match.group(1)
        days, _ = CACHE_RETENTION_OPTIONS[option_key]
        preview = await asyncio.to_thread(
            cache_retention_preview_sync, cache_path, days
        )
        await answer_callback_silently(query)
        await show_callback_page(
            query,
            cache_retention_preview_text(option_key, preview),
            cache_retention_confirm_keyboard(option_key),
            parse_mode="HTML",
        )
        return None

    retention_confirm_match = re.fullmatch(
        r"params:cache_retention_confirm:(1m|1y|all)", data
    )
    if retention_confirm_match:
        option_key = retention_confirm_match.group(1)
        days, label = CACHE_RETENTION_OPTIONS[option_key]
        stats = await asyncio.to_thread(
            cache_retention_set_and_prune_sync, cache_path, days
        )
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
            cache_retention_keyboard(bot_ctx.cache_path, days),
            parse_mode="HTML",
        )
        return None

    if data == "params:cover":
        user_data_of(context)["awaiting_custom_cover"] = True
        user_data_of(context).pop("awaiting_custom_nickname", None)
        await answer_callback_silently(query)
        await show_callback_page(
            query,
            "🖼 自定题图\n\n请直接发送一张图片。\n收到后，我会把它设为你打开 /start 时显示的题图。",
            cover_config_keyboard(),
        )
        return None

    if data == "params:cover_reset":
        user_data_of(context).pop("awaiting_custom_cover", None)
        await asyncio.to_thread(
            ui_pref_delete_sync, cache_path, query.from_user.id, "cover_file_id"
        )
        await query.answer("已重置为 Bot 头像", show_alert=True)
        await show_callback_page(
            query,
            "🖼 自定题图\n\n已重置：之后 /start 会继续使用 Bot 头像。",
            parameter_config_keyboard(),
        )
        return None

    if data == "params:nickname":
        user_data_of(context)["awaiting_custom_nickname"] = True
        user_data_of(context).pop("awaiting_custom_cover", None)
        await answer_callback_silently(query)
        await show_callback_page(
            query,
            "🏷 自定昵称\n\n请发送要显示在 /start 欢迎语里的昵称。",
            nickname_config_keyboard(),
        )
        return None

    if data == "params:nickname_reset":
        user_data_of(context).pop("awaiting_custom_nickname", None)
        await asyncio.to_thread(
            ui_pref_delete_sync, cache_path, query.from_user.id, "nickname"
        )
        await query.answer("已重置为 Telegram 名称", show_alert=True)
        await show_callback_page(
            query,
            "🏷 自定昵称\n\n已重置：之后 /start 会继续使用你的 Telegram 名称。",
            parameter_config_keyboard(),
        )
        return None
    return True

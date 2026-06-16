from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..config import AppConfig

TrafficCustomState = Callable[[Any], dict[str, Any]]


@dataclass(slots=True, frozen=True)
class BotContext:
    """Shared immutable runtime dependencies for bot handlers.

    Telegram's per-update ContextTypes.DEFAULT_TYPE is still passed separately by
    python-telegram-bot; this object only carries application-level dependencies
    that were previously captured directly from build_application closures.
    """

    cfg: AppConfig
    cache_path: Path


@dataclass(slots=True, frozen=True)
class BotRuntime:
    """Application runtime services shared by router and handlers.

    This keeps ``router.register_handlers`` stable while the application
    factory is split into smaller runtime/message/permission modules.
    """

    bot_ctx: BotContext
    reply_main_menu: Callable[..., Any]
    delete_trigger_command_message: Callable[..., Any]
    track_auto_delete_message: Callable[..., Any]
    reply_cover_card: Callable[..., Any]
    edit_or_replace_status_any: Callable[..., Any]
    reply_connection_status: Callable[..., Any]
    reply_long_text: Callable[..., Any]
    send_or_jump_traffic_dashboard: Callable[..., Any]
    get_traffic_custom_state: TrafficCustomState
    traffic_custom_prompt_text: Callable[..., Any]
    show_callback_page: Callable[..., Any]
    answer_callback_silently: Callable[..., Any]
    cache_retention_text_sync: Callable[..., Any]
    cache_retention_preview_text: Callable[..., Any]
    show_initialization_gate: Callable[..., Any]
    send_start_menu: Callable[..., Any]
    open_dashboard_card: Callable[..., Any]
    purge_chat_history: Callable[..., Any]
    resolve_telegram_user_label: Callable[..., Any]
    mark_no_auto_delete_message: Callable[..., Any]
    send_dashboard_card: Callable[..., Any]
    edit_dashboard_card: Callable[..., Any]
    open_traffic_dashboard_message: Callable[..., Any]
    switch_traffic_dashboard_message: Callable[..., Any]
    context_bot_delete_message: Callable[..., Any]
    edit_global_alert_prompt: Callable[..., Any]
    edit_alert_prompt: Callable[..., Any]


def user_data_of(context: Any) -> dict[Any, Any]:
    if context.user_data is None:
        context.user_data = {}
    return context.user_data

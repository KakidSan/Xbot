from __future__ import annotations

from functools import partial

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from .context import BotRuntime
from .handlers.commands import (
    handle_active_users_command,
    handle_clear_history_command,
    handle_health_command,
    handle_start_command,
    handle_status_command,
    handle_traffic_daily_command,
    handle_traffic_nodes_command,
    handle_traffic_users_command,
    handle_user_ip_query_command,
)
from .handlers.legacy import (
    handle_alert_callback,
    handle_fallback_message,
    handle_ip_detail_callback,
)
from .handlers.main_menu import handle_close_message_callback, handle_detail_back_callback, handle_main_menu_callback
from .handlers.traffic import handle_active_users_callback, handle_traffic_daily_callback
from .handlers.version import version_command as handle_version_command, version_update_callback as handle_version_update_callback


def register_handlers(app: Application, runtime: BotRuntime) -> None:
    """Register Telegram command, callback, and message handlers."""

    bot_ctx = runtime.bot_ctx
    cfg = bot_ctx.cfg
    cache_path = bot_ctx.cache_path
    app.bot_data["xbot_context"] = bot_ctx

    reply_main_menu = runtime.reply_main_menu
    delete_trigger_command_message = runtime.delete_trigger_command_message
    track_auto_delete_message = runtime.track_auto_delete_message
    reply_cover_card = runtime.reply_cover_card
    edit_or_replace_status_any = runtime.edit_or_replace_status_any
    reply_connection_status = runtime.reply_connection_status
    reply_long_text = runtime.reply_long_text
    send_or_jump_traffic_dashboard = runtime.send_or_jump_traffic_dashboard
    show_callback_page = runtime.show_callback_page
    answer_callback_silently = runtime.answer_callback_silently
    cache_retention_text_sync = runtime.cache_retention_text_sync
    cache_retention_preview_text = runtime.cache_retention_preview_text
    show_initialization_gate = runtime.show_initialization_gate
    send_start_menu = runtime.send_start_menu
    open_dashboard_card = runtime.open_dashboard_card
    purge_chat_history = runtime.purge_chat_history
    resolve_telegram_user_label = runtime.resolve_telegram_user_label
    mark_no_auto_delete_message = runtime.mark_no_auto_delete_message
    send_dashboard_card = runtime.send_dashboard_card
    edit_dashboard_card = runtime.edit_dashboard_card
    open_traffic_dashboard_message = runtime.open_traffic_dashboard_message
    switch_traffic_dashboard_message = runtime.switch_traffic_dashboard_message
    context_bot_delete_message = runtime.context_bot_delete_message
    edit_global_alert_prompt = runtime.edit_global_alert_prompt
    edit_alert_prompt = runtime.edit_alert_prompt

    app.add_handler(CommandHandler(
        "start",
        partial(
            handle_start_command,
            cfg=cfg,
            reply_main_menu=reply_main_menu,
            delete_trigger_command_message=delete_trigger_command_message,
        ),
    ))
    app.add_handler(CommandHandler(
        "clear_history",
        partial(handle_clear_history_command, cfg=cfg, track_auto_delete_message=track_auto_delete_message),
    ))
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
    app.add_handler(CommandHandler(
        "status",
        partial(handle_status_command, cfg=cfg, cache_path=cache_path, track_auto_delete_message=track_auto_delete_message),
    ))
    app.add_handler(CommandHandler(
        "health",
        partial(
            handle_health_command,
            cfg=cfg,
            cache_path=cache_path,
            track_auto_delete_message=track_auto_delete_message,
            reply_long_text=reply_long_text,
        ),
    ))
    app.add_handler(CommandHandler(
        "active_users",
        partial(handle_active_users_command, cfg=cfg, cache_path=cache_path, track_auto_delete_message=track_auto_delete_message),
    ))
    app.add_handler(CommandHandler(
        "user_ip_query",
        partial(handle_user_ip_query_command, cfg=cfg, track_auto_delete_message=track_auto_delete_message),
    ))
    app.add_handler(CommandHandler(
        "traffic_daily",
        partial(handle_traffic_daily_command, cfg=cfg, track_auto_delete_message=track_auto_delete_message),
    ))
    app.add_handler(CommandHandler(
        "traffic_users",
        partial(handle_traffic_users_command, cfg=cfg, send_or_jump_traffic_dashboard=send_or_jump_traffic_dashboard),
    ))
    app.add_handler(CommandHandler(
        "traffic_nodes",
        partial(handle_traffic_nodes_command, cfg=cfg, send_or_jump_traffic_dashboard=send_or_jump_traffic_dashboard),
    ))
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
    main_menu_handler = partial(
        handle_main_menu_callback,
        cfg=cfg,
        bot_ctx=bot_ctx,
        cache_path=cache_path,
        cache_retention_text_sync=cache_retention_text_sync,
        cache_retention_preview_text=cache_retention_preview_text,
        show_initialization_gate=show_initialization_gate,
        answer_callback_silently=answer_callback_silently,
        show_callback_page=show_callback_page,
        send_start_menu=send_start_menu,
        open_dashboard_card=open_dashboard_card,
        purge_chat_history=purge_chat_history,
        resolve_telegram_user_label=resolve_telegram_user_label,
        reply_long_text=reply_long_text,
        send_or_jump_traffic_dashboard=send_or_jump_traffic_dashboard,
        traffic_custom_state=runtime.traffic_custom_state,
        traffic_custom_prompt_text=runtime.traffic_custom_prompt_text,
    )
    app.add_handler(CallbackQueryHandler(main_menu_handler, pattern=r"^main_menu(?::(?:init_ack|clear_history|clear_history_confirm|system_check|system_check_refresh|status_notice|traffic_management|traffic_users|traffic_nodes|traffic_alerts))?$"))
    app.add_handler(CallbackQueryHandler(main_menu_handler, pattern=r"^main_menu:op_logs(?::(?:traffic_alert|ip_alert|ip_ignore|reset_cache|reset_ip|parameter_config|auth)(?::\d+)?)?$"))
    app.add_handler(CallbackQueryHandler(main_menu_handler, pattern=r"^main_menu:auth(?::(?:add|delete|del_done|del_toggle:\d+|del_confirm|roles|role_toggle:\d+|role_save))?$"))
    app.add_handler(CallbackQueryHandler(main_menu_handler, pattern=r"^main_menu:ip_monitor(?::(?:period|user_query|ignore|ignored_rules:\d+|ignored_rule_toggle:\d+:[A-Za-z0-9]+|ignore:(?:area|asn|cidr):\d+|ignore_toggle:(?:area|asn|cidr):\d+:[A-Za-z0-9]+))?$"))
    app.add_handler(CallbackQueryHandler(main_menu_handler, pattern=r"^main_menu:noop$"))
    app.add_handler(CallbackQueryHandler(main_menu_handler, pattern=r"^main_menu:parameter_config(?::(?:cover|cover_reset|nickname|nickname_reset|cache_retention|cache_retention_select:(?:1m|1q|1y|all)|cache_retention_confirm:(?:1m|1q|1y|all)))?$"))
    app.add_handler(CallbackQueryHandler(main_menu_handler, pattern=r"^main_menu:notifications(?::(?:daily|weekly|monthly|collector|traffic_alert|ip_alert|version_update))?$"))
    app.add_handler(CallbackQueryHandler(main_menu_handler, pattern=r"^main_menu:debug(?:_tools|:reset_cache|:reset_cache_now|:reset_cache_now_confirm|:reset_cache_floor|:reset_user_ip|:reset_user_ip_page:\d+|:reset_user_ip_toggle:\d+:\d+|:reset_user_ip_done|:reset_user_ip_multi_confirm)$"))
    app.add_handler(CallbackQueryHandler(partial(handle_alert_callback, cfg=cfg, bot_ctx=bot_ctx, cache_path=cache_path, show_initialization_gate=show_initialization_gate, answer_callback_silently=answer_callback_silently, show_callback_page=show_callback_page, mark_no_auto_delete_message=mark_no_auto_delete_message), pattern=r"^(alert_menu:(?:traffic|ip)|alert_period_page:(?:traffic|ip):\d+|alert_global_period_page:(?:traffic|ip)|alert_global:(?:traffic|ip)(?::(?:custom|period:(?:1h|24h|7d|today|week)))?|alert_users:(?:traffic|ip):\d+|alert_user:(?:traffic|ip):\d+(?::alert)?|alert_set:(?:traffic|ip):(?:custom:\d+|period:(?:1h|24h|7d|today|week):\d+|threshold:\d+:\d+|whitelist:\d+|reset:\d+))$"))
    app.add_handler(CallbackQueryHandler(partial(handle_traffic_daily_callback, cfg=cfg, bot_ctx=bot_ctx, cache_path=cache_path, show_initialization_gate=show_initialization_gate, answer_callback_silently=answer_callback_silently, show_callback_page=show_callback_page, send_dashboard_card=send_dashboard_card, edit_dashboard_card=edit_dashboard_card, open_traffic_dashboard_message=open_traffic_dashboard_message, switch_traffic_dashboard_message=switch_traffic_dashboard_message), pattern=r"^(traffic_menu(?::[A-Za-z0-9_]+)?|traffic_back:[A-Za-z0-9_]+|traffic_(?:period|switch):(preset_1h|preset_24h|preset_7d|preset_30d|today|yesterday|this_week|this_month)(?::(?:users|nodes))?|ip_custom:start|traffic_custom:(start(?::(?:combined|users|nodes))?|now|(year|month|day|hour|minute):\d+|back:(year|month|day|hour))|traffic_floor:(start|confirm:\d+)|traffic_dashboard:(pin|unpin|delete):[A-Za-z0-9_]+)$"))
    app.add_handler(CallbackQueryHandler(partial(handle_active_users_callback, cfg=cfg, cache_path=cache_path, show_initialization_gate=show_initialization_gate, answer_callback_silently=answer_callback_silently, show_callback_page=show_callback_page, open_dashboard_card=open_dashboard_card), pattern=r"^(active_users(?::|_query:)(1h|24h|7d|30d)(?::\d+)?|ip_user_query:(?:(1h|24h|7d|30d)|custom:\d+:\d+)|user_ip_page:\d+:\d+:(?:all|(?:1h|24h|7d|30d)|custom:\d+:\d+)|active_user_detail:(1h|24h|7d|30d):\d+|active_users_cancel:(1h|24h|7d|30d)|noop)$"))
    app.add_handler(CallbackQueryHandler(partial(handle_ip_detail_callback, cfg=cfg, bot_ctx=bot_ctx, cache_path=cache_path, show_initialization_gate=show_initialization_gate, answer_callback_silently=answer_callback_silently, show_callback_page=show_callback_page, mark_no_auto_delete_message=mark_no_auto_delete_message), pattern=r"^(?:ip_(?:detail_list|active_user_detail):(ip_(?:1h|24h|7d|30d)|iprange_\d+_\d+):(\d+)(?::\d+)?(?::alert)?|ip_alert_notice:\d+|ip_ignore_menu:(ip_(?:1h|24h|7d|30d)|iprange_\d+_\d+):\d+:\d+(?::alert)?|ip_ignore_page:(?:area|asn|cidr):(ip_(?:1h|24h|7d|30d)|iprange_\d+_\d+):\d+:\d+:\d+(?::alert)?|ip_ig_t:[A-Za-z0-9]+|ip_ignore_toggle:(?:area|asn|cidr):(ip_(?:1h|24h|7d|30d)|iprange_\d+_\d+):\d+:\d+:\d+:[A-Za-z0-9]+(?::alert)?)$"))
    app.add_handler(CallbackQueryHandler(partial(handle_detail_back_callback, cfg=cfg, cache_path=cache_path, answer_callback_silently=answer_callback_silently, show_callback_page=show_callback_page), pattern=r"^detail_back:(1h|24h|7d|30d|menu)$"))
    app.add_handler(CallbackQueryHandler(partial(handle_close_message_callback, cfg=cfg), pattern=r"^close_message$"))
    app.add_handler(MessageHandler(filters.ALL, partial(handle_fallback_message, cfg=cfg, bot_ctx=bot_ctx, cache_path=cache_path, track_auto_delete_message=track_auto_delete_message, reply_cover_card=reply_cover_card, resolve_telegram_user_label=resolve_telegram_user_label, context_bot_delete_message=context_bot_delete_message, edit_global_alert_prompt=edit_global_alert_prompt, edit_alert_prompt=edit_alert_prompt)))

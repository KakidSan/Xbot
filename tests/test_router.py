import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
)

from xbot.bot.callback_data import (
    ALERT_PATTERN,
    MAIN_MENU_PATTERN,
    VERSION_UPDATE_PATTERN,
)
from xbot.bot.context import BotContext, BotRuntime
from xbot.bot.router import register_handlers
from xbot.config import AppConfig, MySQLConfig, RedisConfig, TelegramConfig


def fake_runtime(cache_path: Path) -> BotRuntime:
    cfg = AppConfig(
        telegram=TelegramConfig(bot_token="123:ABC", super_admin_user_ids={1}),
        redis=RedisConfig(),
        mysql=MySQLConfig(),
        cache_path=cache_path,
    )
    service = Mock()
    return BotRuntime(
        bot_ctx=BotContext(cfg=cfg, cache_path=cache_path),
        reply_main_menu=service.reply_main_menu,
        delete_trigger_command_message=service.delete_trigger_command_message,
        track_auto_delete_message=service.track_auto_delete_message,
        reply_cover_card=service.reply_cover_card,
        edit_or_replace_status_any=service.edit_or_replace_status_any,
        reply_connection_status=service.reply_connection_status,
        reply_long_text=service.reply_long_text,
        send_or_jump_traffic_dashboard=service.send_or_jump_traffic_dashboard,
        get_traffic_custom_state=service.get_traffic_custom_state,
        traffic_custom_prompt_text=service.traffic_custom_prompt_text,
        show_callback_page=service.show_callback_page,
        answer_callback_silently=service.answer_callback_silently,
        cache_retention_text_sync=service.cache_retention_text_sync,
        cache_retention_preview_text=service.cache_retention_preview_text,
        show_initialization_gate=service.show_initialization_gate,
        send_start_menu=service.send_start_menu,
        open_dashboard_card=service.open_dashboard_card,
        purge_chat_history=service.purge_chat_history,
        resolve_telegram_user_label=service.resolve_telegram_user_label,
        mark_no_auto_delete_message=service.mark_no_auto_delete_message,
        send_dashboard_card=service.send_dashboard_card,
        edit_dashboard_card=service.edit_dashboard_card,
        open_traffic_dashboard_message=service.open_traffic_dashboard_message,
        switch_traffic_dashboard_message=service.switch_traffic_dashboard_message,
        context_bot_delete_message=service.context_bot_delete_message,
        edit_global_alert_prompt=service.edit_global_alert_prompt,
        edit_alert_prompt=service.edit_alert_prompt,
    )


def build_registered_app(cache_path: Path) -> Application:
    app = Application.builder().token("123:ABC").build()
    register_handlers(app, fake_runtime(cache_path))
    return app


def flatten_handlers(app: Application):
    return [handler for handlers in app.handlers.values() for handler in handlers]


class RouterTest(unittest.TestCase):
    def test_register_handlers_adds_handlers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app = build_registered_app(Path(tmpdir) / "xbot.sqlite3")

            handler_count = sum(len(handlers) for handlers in app.handlers.values())
            self.assertGreater(handler_count, 0)

    def test_register_handlers_sets_runtime_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app = build_registered_app(Path(tmpdir) / "xbot.sqlite3")

            self.assertIsInstance(app.bot_data["xbot_context"], BotContext)

    def test_registers_core_command_handlers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app = build_registered_app(Path(tmpdir) / "xbot.sqlite3")

            commands = {
                command
                for handler in flatten_handlers(app)
                if isinstance(handler, CommandHandler)
                for command in handler.commands
            }
            self.assertGreaterEqual(
                commands,
                {
                    "start",
                    "status",
                    "health",
                    "version",
                },
            )

    def test_registers_core_callback_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app = build_registered_app(Path(tmpdir) / "xbot.sqlite3")

            patterns = {
                handler.pattern.pattern
                for handler in flatten_handlers(app)
                if isinstance(handler, CallbackQueryHandler) and handler.pattern
            }
            self.assertIn(VERSION_UPDATE_PATTERN, patterns)
            self.assertIn(MAIN_MENU_PATTERN, patterns)
            self.assertIn(ALERT_PATTERN, patterns)

    def test_fallback_message_handler_is_text_only_not_filters_all(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app = build_registered_app(Path(tmpdir) / "xbot.sqlite3")

            message_handlers = [
                handler
                for handler in flatten_handlers(app)
                if isinstance(handler, MessageHandler)
            ]
            self.assertEqual(len(message_handlers), 1)
            filter_text = repr(message_handlers[0].filters)
            self.assertIn("filters.TEXT", filter_text)
            self.assertIn("filters.COMMAND", filter_text)
            self.assertNotIn("filters.ALL", filter_text)


if __name__ == "__main__":
    unittest.main()

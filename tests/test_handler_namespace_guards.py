import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from xbot.bot.context import BotContext
from xbot.bot.handlers.auth import auth_callback
from xbot.bot.handlers.debug import debug_callback
from xbot.bot.handlers.ip_monitor import ip_monitor_callback
from xbot.bot.handlers.notifications import handle_notifications_callback
from xbot.bot.handlers.parameters import parameter_callback
from xbot.config import AppConfig, MySQLConfig, RedisConfig, TelegramConfig


def test_context(cache_path: Path) -> BotContext:
    cfg = AppConfig(
        telegram=TelegramConfig(
            bot_token="123:ABC",
            super_admin_user_ids={1},
            authorized_user_ids={1},
        ),
        redis=RedisConfig(),
        mysql=MySQLConfig(),
        cache_path=cache_path,
    )
    return BotContext(cfg=cfg, cache_path=cache_path)


def fake_query(data: str):
    return SimpleNamespace(
        data=data,
        message=SimpleNamespace(chat_id=100, message_id=200),
        from_user=SimpleNamespace(id=1, is_bot=False),
        answer=AsyncMock(),
    )


def fake_update(query):
    return SimpleNamespace(callback_query=query, effective_user=query.from_user)


class HandlerNamespaceGuardTest(unittest.IsolatedAsyncioTestCase):
    async def test_auth_callback_ignores_non_auth_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bot_ctx = test_context(Path(tmpdir) / "xbot.sqlite3")
            result = await auth_callback(
                fake_update(fake_query("params")),
                SimpleNamespace(user_data={}),
                cfg=bot_ctx.cfg,
                bot_ctx=bot_ctx,
                cache_path=bot_ctx.cache_path,
                data="params",
                query=fake_query("params"),
                answer_callback_silently=AsyncMock(),
                show_callback_page=AsyncMock(),
                resolve_telegram_user_label=AsyncMock(),
            )
            self.assertFalse(result)

    async def test_ip_monitor_callback_ignores_non_ip_monitor_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bot_ctx = test_context(Path(tmpdir) / "xbot.sqlite3")
            result = await ip_monitor_callback(
                fake_update(fake_query("auth")),
                SimpleNamespace(user_data={}),
                cfg=bot_ctx.cfg,
                bot_ctx=bot_ctx,
                cache_path=bot_ctx.cache_path,
                data="auth",
                query=fake_query("auth"),
                answer_callback_silently=AsyncMock(),
                show_callback_page=AsyncMock(),
                open_dashboard_card=AsyncMock(),
            )
            self.assertFalse(result)

    async def test_parameters_callback_ignores_non_params_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bot_ctx = test_context(Path(tmpdir) / "xbot.sqlite3")
            result = await parameter_callback(
                fake_update(fake_query("debug:tools")),
                SimpleNamespace(user_data={}),
                cfg=bot_ctx.cfg,
                bot_ctx=bot_ctx,
                cache_path=bot_ctx.cache_path,
                data="debug:tools",
                query=fake_query("debug:tools"),
                answer_callback_silently=AsyncMock(),
                show_callback_page=AsyncMock(),
                cache_retention_text_sync=Mock(),
                cache_retention_preview_text=Mock(),
            )
            self.assertFalse(result)

    async def test_parameters_callback_shows_settings_menu(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bot_ctx = test_context(Path(tmpdir) / "xbot.sqlite3")
            show_callback_page = AsyncMock()
            query = fake_query("params")
            await parameter_callback(
                fake_update(query),
                SimpleNamespace(user_data={}),
                cfg=bot_ctx.cfg,
                bot_ctx=bot_ctx,
                cache_path=bot_ctx.cache_path,
                data="params",
                query=query,
                answer_callback_silently=AsyncMock(),
                show_callback_page=show_callback_page,
                cache_retention_text_sync=Mock(),
                cache_retention_preview_text=Mock(),
            )
            show_callback_page.assert_awaited_once()
            self.assertIn("个人设置", show_callback_page.await_args.args[1])

    async def test_parameters_callback_enters_speedtest_tool_add(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bot_ctx = test_context(Path(tmpdir) / "xbot.sqlite3")
            context = SimpleNamespace(user_data={})
            show_callback_page = AsyncMock()
            query = fake_query("params:speedtest_jump:add")
            await parameter_callback(
                fake_update(query),
                context,
                cfg=bot_ctx.cfg,
                bot_ctx=bot_ctx,
                cache_path=bot_ctx.cache_path,
                data="params:speedtest_jump:add",
                query=query,
                answer_callback_silently=AsyncMock(),
                show_callback_page=show_callback_page,
                cache_retention_text_sync=Mock(),
                cache_retention_preview_text=Mock(),
            )
            self.assertTrue(context.user_data["awaiting_speedtest_jump_id"])
            show_callback_page.assert_awaited_once()
            self.assertIn("添加测试工具", show_callback_page.await_args.args[1])

    async def test_parameters_callback_enters_cover_upload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bot_ctx = test_context(Path(tmpdir) / "xbot.sqlite3")
            context = SimpleNamespace(user_data={"awaiting_custom_nickname": True})
            show_callback_page = AsyncMock()
            query = fake_query("params:cover")
            await parameter_callback(
                fake_update(query),
                context,
                cfg=bot_ctx.cfg,
                bot_ctx=bot_ctx,
                cache_path=bot_ctx.cache_path,
                data="params:cover",
                query=query,
                answer_callback_silently=AsyncMock(),
                show_callback_page=show_callback_page,
                cache_retention_text_sync=Mock(),
                cache_retention_preview_text=Mock(),
            )
            self.assertTrue(context.user_data["awaiting_custom_cover"])
            self.assertNotIn("awaiting_custom_nickname", context.user_data)
            self.assertIn("自定题图", show_callback_page.await_args.args[1])

    async def test_parameters_callback_enters_nickname_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bot_ctx = test_context(Path(tmpdir) / "xbot.sqlite3")
            context = SimpleNamespace(user_data={"awaiting_custom_cover": True})
            show_callback_page = AsyncMock()
            query = fake_query("params:nickname")
            await parameter_callback(
                fake_update(query),
                context,
                cfg=bot_ctx.cfg,
                bot_ctx=bot_ctx,
                cache_path=bot_ctx.cache_path,
                data="params:nickname",
                query=query,
                answer_callback_silently=AsyncMock(),
                show_callback_page=show_callback_page,
                cache_retention_text_sync=Mock(),
                cache_retention_preview_text=Mock(),
            )
            self.assertTrue(context.user_data["awaiting_custom_nickname"])
            self.assertNotIn("awaiting_custom_cover", context.user_data)
            self.assertIn("自定昵称", show_callback_page.await_args.args[1])

    async def test_debug_callback_ignores_non_debug_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bot_ctx = test_context(Path(tmpdir) / "xbot.sqlite3")
            result = await debug_callback(
                fake_update(fake_query("auth")),
                SimpleNamespace(user_data={}),
                cfg=bot_ctx.cfg,
                bot_ctx=bot_ctx,
                cache_path=bot_ctx.cache_path,
                data="auth",
                query=fake_query("auth"),
                answer_callback_silently=AsyncMock(),
                show_callback_page=AsyncMock(),
                traffic_custom_state=Mock(),
                traffic_custom_prompt_text=Mock(),
            )
            self.assertFalse(result)

    async def test_notifications_callback_rejects_unexpected_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bot_ctx = test_context(Path(tmpdir) / "xbot.sqlite3")
            query = fake_query("auth")
            await handle_notifications_callback(
                fake_update(query),
                SimpleNamespace(user_data={}),
                cfg=bot_ctx.cfg,
                bot_ctx=bot_ctx,
                answer_callback_silently=AsyncMock(),
                show_callback_page=AsyncMock(),
            )
            query.answer.assert_awaited_once_with("该入口暂未开放", show_alert=True)


if __name__ == "__main__":
    unittest.main()

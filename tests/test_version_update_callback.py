import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from xbot.bot.context import BotContext
from xbot.bot.version import version_update_callback
from xbot.config import AppConfig, MySQLConfig, RedisConfig, TelegramConfig


class VersionUpdateCallbackTest(unittest.IsolatedAsyncioTestCase):
    def bot_ctx(self, tmpdir: str) -> BotContext:
        cfg = AppConfig(
            telegram=TelegramConfig(
                bot_token="123:ABC",
                admin_user_id=676104247,
                super_admin_user_ids={676104247},
            ),
            redis=RedisConfig(),
            mysql=MySQLConfig(),
            cache_path=Path(tmpdir) / "xbot.sqlite3",
        )
        return BotContext(cfg=cfg, cache_path=cfg.cache_path)

    def update_for(self, data: str):
        user = SimpleNamespace(id=676104247)
        message = SimpleNamespace(chat_id=676104247, message_id=4578)
        query = SimpleNamespace(
            data=data,
            message=message,
            from_user=user,
            answer=AsyncMock(),
        )
        return SimpleNamespace(callback_query=query, effective_user=user), query

    async def test_update_confirm_card_is_not_auto_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bot_ctx = self.bot_ctx(tmpdir)
            update, query = self.update_for("version_update:start:v3.0.0-beta4")
            show_callback_page = AsyncMock()

            with patch("xbot.bot.version.auto_delete_message_delete_sync") as delete_sync:
                await version_update_callback(
                    update,
                    SimpleNamespace(),
                    bot_ctx,
                    show_callback_page,
                    AsyncMock(),
                )

            delete_sync.assert_called_once_with(
                bot_ctx.cache_path, "676104247", 4578
            )
            show_callback_page.assert_awaited_once()
            self.assertFalse(show_callback_page.await_args.kwargs["auto_delete"])
            query.answer.assert_not_awaited()

    async def test_update_running_card_is_not_auto_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bot_ctx = self.bot_ctx(tmpdir)
            update, query = self.update_for("version_update:confirm:v3.0.0-beta4")
            show_callback_page = AsyncMock()

            with (
                patch("xbot.bot.version.auto_delete_message_delete_sync") as delete_sync,
                patch(
                    "xbot.bot.version.start_background_update_sync",
                    return_value=(True, "后台更新已启动。"),
                ),
            ):
                await version_update_callback(
                    update,
                    SimpleNamespace(),
                    bot_ctx,
                    show_callback_page,
                    AsyncMock(),
                )

            query.answer.assert_awaited_once_with("后台更新已启动")
            delete_sync.assert_called_once_with(
                bot_ctx.cache_path, "676104247", 4578
            )
            show_callback_page.assert_awaited_once()
            self.assertFalse(show_callback_page.await_args.kwargs["auto_delete"])


if __name__ == "__main__":
    unittest.main()

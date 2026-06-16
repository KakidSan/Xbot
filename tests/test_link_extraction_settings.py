import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xbot.bot.handlers.parameters import (
    speedtest_jump_delete_keyboard,
    speedtest_jump_keyboard,
    speedtest_jump_text,
)
from xbot.bot.handlers.text_input import _parse_test_tool_target, _synthetic_telegram_id
from xbot.db.cache import (
    init_cache,
    speedtest_jump_target_delete_sync,
    speedtest_jump_target_upsert_sync,
    speedtest_jump_targets_sync,
)


def callbacks(markup) -> list[str]:
    result: list[str] = []
    for row in markup.inline_keyboard:
        for button in row:
            if button.callback_data:
                result.append(button.callback_data)
    return result


class LinkExtractionSettingsTest(unittest.TestCase):
    def cache_path(self, tmpdir: str) -> Path:
        path = Path(tmpdir) / "xbot.sqlite3"
        init_cache(path)
        return path

    def test_parse_test_tool_target_accepts_common_telegram_forms(self) -> None:
        cases = [
            ("7383548966", ("7383548966", 7383548966)),
            ("@huachuanBot", ("huachuanBot", "@huachuanBot")),
            ("huachuanBot", ("huachuanBot", "@huachuanBot")),
            ("https://t.me/huachuanBot", ("huachuanBot", "@huachuanBot")),
            ("t.me/huachuanBot", ("huachuanBot", "@huachuanBot")),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(_parse_test_tool_target(raw), expected)

        self.assertEqual(_parse_test_tool_target("bad user"), ("bad user", None))
        self.assertEqual(_parse_test_tool_target(""), ("", None))

    def test_synthetic_telegram_id_is_negative_and_stable(self) -> None:
        first = _synthetic_telegram_id("HuachuanBot")
        second = _synthetic_telegram_id("huachuanbot")

        self.assertLess(first, 0)
        self.assertEqual(first, second)

    def test_speedtest_jump_text_handles_empty_and_personal_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = self.cache_path(tmpdir)
            self.assertIn("• 暂无", speedtest_jump_text(cache_path, 100))

            speedtest_jump_target_upsert_sync(
                cache_path, 100, 7383548966, "好用爱用的测速机器人", "huachuanBot"
            )
            speedtest_jump_target_upsert_sync(
                cache_path, 200, 1, "别人设置的工具", "other_bot"
            )

            text = speedtest_jump_text(cache_path, 100)
            self.assertIn("当前已添加的测速工具", text)
            self.assertIn("好用爱用的测速机器人", text)
            self.assertIn("7383548966", text)
            self.assertNotIn("别人设置的工具", text)

    def test_speedtest_jump_keyboards_expose_add_delete_and_negative_delete_target(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = self.cache_path(tmpdir)
            speedtest_jump_target_upsert_sync(
                cache_path, 100, -123456, "用户名工具", "tool_bot"
            )

            root_callbacks = callbacks(speedtest_jump_keyboard())
            delete_callbacks = callbacks(
                speedtest_jump_delete_keyboard(cache_path, 100)
            )

        self.assertIn("main_menu:parameter_config:speedtest_jump:add", root_callbacks)
        self.assertIn(
            "main_menu:parameter_config:speedtest_jump:delete:0", root_callbacks
        )
        self.assertIn(
            "main_menu:parameter_config:speedtest_jump:delete_target:-123456:0",
            delete_callbacks,
        )

    def test_node_link_keyboard_uses_no_real_http_for_link_targets(self) -> None:
        with patch(
            "xbot.node_monitor.speedtest_jump_targets_sync",
            return_value=[
                {"telegram_id": -1, "nickname": "Koipy", "username": "koipy_bot"}
            ],
        ):
            from xbot.node_monitor import node_link_detail_keyboard_sync

            markup = node_link_detail_keyboard_sync(Path("cache.sqlite3"), 123)

        self.assertEqual(markup.inline_keyboard[0][0].url, "https://t.me/koipy_bot")


class TestToolCacheSchemaTest(unittest.TestCase):
    def cache_path(self, tmpdir: str) -> Path:
        path = Path(tmpdir) / "xbot.sqlite3"
        init_cache(path)
        return path

    def test_test_tool_targets_are_scoped_by_owner_and_round_trip_username(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = self.cache_path(tmpdir)

            speedtest_jump_target_upsert_sync(
                cache_path, 100, 7383548966, "测速机器人", "huachuanBot"
            )
            speedtest_jump_target_upsert_sync(
                cache_path, 200, 7383548966, "同一个工具其他人", "huachuanBot"
            )
            speedtest_jump_target_upsert_sync(
                cache_path, 100, -42, "用户名兜底", "fallback_bot"
            )

            owner_rows = speedtest_jump_targets_sync(cache_path, 100)
            other_rows = speedtest_jump_targets_sync(cache_path, 200)

            self.assertEqual(
                {row["telegram_id"] for row in owner_rows}, {7383548966, -42}
            )
            self.assertEqual(len(other_rows), 1)
            self.assertEqual(other_rows[0]["nickname"], "同一个工具其他人")
            self.assertEqual(other_rows[0]["username"], "huachuanBot")

            speedtest_jump_target_upsert_sync(
                cache_path, 100, 7383548966, "测速机器人改名", None
            )
            updated = {
                row["telegram_id"]: row
                for row in speedtest_jump_targets_sync(cache_path, 100)
            }
            self.assertEqual(updated[7383548966]["nickname"], "测速机器人改名")
            self.assertIsNone(updated[7383548966]["username"])

            self.assertTrue(speedtest_jump_target_delete_sync(cache_path, 100, -42))
            self.assertFalse(speedtest_jump_target_delete_sync(cache_path, 100, -42))
            self.assertEqual(
                {
                    row["telegram_id"]
                    for row in speedtest_jump_targets_sync(cache_path, 100)
                },
                {7383548966},
            )


if __name__ == "__main__":
    unittest.main()

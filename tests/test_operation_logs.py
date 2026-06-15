import tempfile
import unittest
from pathlib import Path

from xbot.bot.operation_logs import (
    operation_log_action_label,
    operation_log_detail_display_text,
    xboard_user_ids_to_labels,
)
from xbot.db.cache import cache_connect, init_cache, ui_pref_set_sync


def set_xboard_user_name(cache_path: Path, user_id: int, name: str) -> None:
    with cache_connect(cache_path) as conn:
        conn.execute(
            "INSERT INTO users(user_id, display_name, remarks, email, updated_at) VALUES(?, ?, '', '', 1)",
            (user_id, name),
        )


class OperationLogsTest(unittest.TestCase):
    def test_operation_log_action_label_adds_icon_only_once(self) -> None:
        self.assertEqual(operation_log_action_label("auth", "增加授权"), "🔐 增加授权")
        self.assertEqual(operation_log_action_label("auth", "🔐 增加授权"), "🔐 增加授权")
        self.assertEqual(operation_log_action_label("unknown", "动作"), "动作")

    def test_xboard_user_ids_to_labels_rewrites_only_user_references(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "xbot.sqlite3"
            init_cache(cache_path)
            set_xboard_user_name(cache_path, 12, "Alice")
            set_xboard_user_name(cache_path, 34, "Bob")

            self.assertEqual(xboard_user_ids_to_labels(cache_path, "用户 12"), "Alice (user_id: 12)")
            self.assertEqual(xboard_user_ids_to_labels(cache_path, "对象：XBoard 用户 34"), "对象：Bob (user_id: 34)")
            self.assertEqual(
                xboard_user_ids_to_labels(cache_path, "邮箱 user12@example.com 与 用户 12"),
                "邮箱 user12@example.com 与 Alice (user_id: 12)",
            )

    def test_operation_log_detail_display_text_converts_auth_and_xboard_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "xbot.sqlite3"
            init_cache(cache_path)
            ui_pref_set_sync(cache_path, 100, "telegram_label", "Mat (@mat)")
            set_xboard_user_name(cache_path, 12, "Alice")

            auth_detail = operation_log_detail_display_text(cache_path, "auth", "修改前：100\n修改后：空")
            self.assertEqual(auth_detail, "修改前：Mat (@mat)\n修改后：空")

            ip_detail = operation_log_detail_display_text(cache_path, "ip_ignore", "对象：用户 12\n修改前：未忽略")
            self.assertEqual(ip_detail, "对象：Alice (user_id: 12)\n修改前：未忽略")


if __name__ == "__main__":
    unittest.main()

import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from xbot.collector import due_traffic_report_kinds, traffic_report_push_loop
from xbot.config import AppConfig, MySQLConfig, RedisConfig, TelegramConfig
from xbot.db.cache import init_cache, traffic_report_already_sent_sync


class TrafficReportPushTest(unittest.IsolatedAsyncioTestCase):
    def test_due_traffic_report_time_can_be_overridden(self) -> None:
        with patch.dict(
            os.environ,
            {"TRAFFIC_REPORT_PUSH_HOUR": "23", "TRAFFIC_REPORT_PUSH_MINUTE": "59"},
        ):
            tz = timezone(timedelta(hours=8))
            self.assertEqual(
                due_traffic_report_kinds(datetime(2026, 7, 3, 23, 58, tzinfo=tz)), []
            )
            self.assertEqual(
                due_traffic_report_kinds(datetime(2026, 7, 3, 23, 59, tzinfo=tz)),
                ["daily"],
            )

    async def test_daily_report_pushes_to_default_allowed_admin(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "xbot.sqlite3"
            init_cache(cache_path)
            cfg = AppConfig(
                telegram=TelegramConfig(
                    bot_token="123:ABC",
                    admin_user_id=676104247,
                    super_admin_user_ids={676104247},
                ),
                redis=RedisConfig(),
                mysql=MySQLConfig(),
                cache_path=cache_path,
            )
            stop_event = asyncio.Event()
            sent: list[tuple[str, str]] = []

            class FakeBot:
                async def send_message(
                    self, chat_id: str, text: str, parse_mode: str
                ) -> None:
                    sent.append((str(chat_id), parse_mode))
                    stop_event.set()

            app = SimpleNamespace(bot=FakeBot())

            with patch.dict(
                os.environ,
                {"TRAFFIC_REPORT_PUSH_HOUR": "23", "TRAFFIC_REPORT_PUSH_MINUTE": "59"},
            ), patch("xbot.collector.beijing_now") as fake_now, patch(
                "xbot.collector.traffic_report_text_sync",
                return_value=("日报", 1783008000, 1783094399),
            ):
                fake_now.return_value = datetime(
                    2026, 7, 3, 23, 59, tzinfo=timezone(timedelta(hours=8))
                )
                await asyncio.wait_for(
                    traffic_report_push_loop(app, cfg, cache_path, stop_event),
                    timeout=2,
                )

            self.assertEqual(sent, [("676104247", "HTML")])
            self.assertTrue(
                traffic_report_already_sent_sync(
                    cache_path,
                    "daily",
                    1783008000,
                    1783094399,
                    "676104247",
                )
            )


if __name__ == "__main__":
    unittest.main()

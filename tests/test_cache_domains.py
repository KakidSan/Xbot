import tempfile
import unittest
from pathlib import Path

from xbot.db.cache import (
    alert_global_period_sync,
    alert_global_threshold_sync,
    alert_reset_setting_sync,
    alert_set_global_period_sync,
    alert_set_global_threshold_sync,
    alert_upsert_setting_sync,
    ignored_rule_count_sync,
    ignored_rule_counts_by_dimension_sync,
    ignored_rule_items_sync,
    ignored_rule_toggle_sync,
    ignored_rule_values_sync,
    init_cache,
    mark_traffic_report_sent_sync,
    save_traffic_range_sync,
    traffic_base_kind,
    traffic_range_kind_from_cache_sync,
    traffic_report_already_sent_sync,
)


class CacheDomainTest(unittest.TestCase):
    def cache_path(self, tmpdir: str) -> Path:
        path = Path(tmpdir) / "xbot.sqlite3"
        init_cache(path)
        return path

    def test_alert_global_threshold_and_period_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = self.cache_path(tmpdir)

            self.assertEqual(alert_global_period_sync(cache_path, "traffic"), "1h")
            self.assertEqual(alert_global_period_sync(cache_path, "ip"), "24h")

            self.assertEqual(
                alert_set_global_period_sync(cache_path, "traffic", "7d"), "7d"
            )
            self.assertEqual(alert_global_period_sync(cache_path, "traffic"), "7d")

            # Traffic thresholds are passed in GiB and stored as bytes.
            self.assertEqual(
                alert_set_global_threshold_sync(cache_path, "traffic", 2), 2 * 1024**3
            )
            self.assertEqual(
                alert_global_threshold_sync(cache_path, "traffic"), 2 * 1024**3
            )

            self.assertEqual(alert_set_global_threshold_sync(cache_path, "ip", 5), 5)
            self.assertEqual(alert_global_threshold_sync(cache_path, "ip"), 5)

            with self.assertRaises(ValueError):
                alert_set_global_period_sync(cache_path, "traffic", "invalid")
            with self.assertRaises(ValueError):
                alert_set_global_threshold_sync(cache_path, "ip", 0)

    def test_alert_user_setting_upsert_and_reset(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = self.cache_path(tmpdir)

            setting = alert_upsert_setting_sync(
                cache_path,
                123,
                traffic_threshold_bytes=99,
                traffic_whitelist=1,
                traffic_period="monthly",
                ip_city_threshold=7,
                ip_whitelist=1,
                ip_period="weekly",
            )
            self.assertEqual(setting["traffic_threshold_bytes"], 99)
            self.assertEqual(setting["traffic_whitelist"], 1)
            self.assertEqual(setting["ip_city_threshold"], 7)
            self.assertEqual(setting["ip_whitelist"], 1)

            traffic_reset = alert_reset_setting_sync(cache_path, 123, "traffic")
            self.assertIsNone(traffic_reset["traffic_threshold_bytes"])
            self.assertEqual(traffic_reset["traffic_whitelist"], 0)
            self.assertIsNone(traffic_reset["traffic_period"])
            self.assertEqual(traffic_reset["ip_city_threshold"], 7)

            ip_reset = alert_reset_setting_sync(cache_path, 123, "ip")
            self.assertIsNone(ip_reset["ip_city_threshold"])
            self.assertEqual(ip_reset["ip_whitelist"], 0)
            self.assertIsNone(ip_reset["ip_period"])

            with self.assertRaises(ValueError):
                alert_reset_setting_sync(cache_path, 123, "unknown")

    def test_traffic_range_and_report_sent_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = self.cache_path(tmpdir)

            save_traffic_range_sync(
                cache_path, "users_range_abcd", 100, 200, "测试区间"
            )
            saved = traffic_range_kind_from_cache_sync(cache_path, "users_range_abcd")
            self.assertIsNotNone(saved)
            self.assertEqual(saved["start_ts"], 100)
            self.assertEqual(saved["end_ts"], 200)
            self.assertEqual(saved["label"], "测试区间")

            self.assertEqual(traffic_base_kind("range_abcd"), "range_abcd")
            self.assertEqual(traffic_base_kind("users_24h"), "preset_24h")
            self.assertEqual(traffic_base_kind("nodes_7d"), "preset_7d")

            self.assertFalse(
                traffic_report_already_sent_sync(
                    cache_path, "daily", 100, 200, "chat-a"
                )
            )
            mark_traffic_report_sent_sync(cache_path, "daily", 100, 200, "chat-a")
            self.assertTrue(
                traffic_report_already_sent_sync(
                    cache_path, "daily", 100, 200, "chat-a"
                )
            )
            self.assertFalse(
                traffic_report_already_sent_sync(
                    cache_path, "daily", 100, 200, "chat-b"
                )
            )

    def test_ignored_rule_toggle_counts_and_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = self.cache_path(tmpdir)

            self.assertEqual(ignored_rule_count_sync(cache_path), 0)
            self.assertTrue(ignored_rule_toggle_sync(cache_path, "area", "HK/香港"))
            self.assertTrue(ignored_rule_toggle_sync(cache_path, "asn", "AS123"))

            self.assertEqual(ignored_rule_count_sync(cache_path), 2)
            self.assertEqual(
                ignored_rule_counts_by_dimension_sync(cache_path)["area"], 1
            )
            self.assertEqual(
                ignored_rule_counts_by_dimension_sync(cache_path)["asn"], 1
            )
            self.assertEqual(ignored_rule_values_sync(cache_path, "area"), {"HK/香港"})

            items = ignored_rule_items_sync(cache_path)
            self.assertEqual({item["dimension"] for item in items}, {"area", "asn"})

            self.assertFalse(ignored_rule_toggle_sync(cache_path, "area", "HK/香港"))
            self.assertEqual(ignored_rule_values_sync(cache_path, "area"), set())
            self.assertEqual(ignored_rule_count_sync(cache_path), 1)

            with self.assertRaises(ValueError):
                ignored_rule_toggle_sync(cache_path, "bad", "value")


if __name__ == "__main__":
    unittest.main()

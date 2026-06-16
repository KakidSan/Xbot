import time
import unittest

from xbot.bot.formatters import (
    cached_user_display_name,
    format_bytes,
    format_collector_gap_alert,
    format_collector_health_alert,
    format_geo_pending_text,
    format_ip_alert,
    format_traffic_alert,
    notification_ip_alert_mode_label,
    redact_sensitive_text_for_non_admin,
    render_user_label,
    traffic_title_for_dimension,
)


class FormattersTest(unittest.TestCase):
    def test_notification_ip_alert_mode_label(self) -> None:
        self.assertEqual(notification_ip_alert_mode_label("off"), "关闭")
        self.assertEqual(notification_ip_alert_mode_label("basic"), "基础")
        self.assertEqual(notification_ip_alert_mode_label("advanced"), "高级")
        self.assertEqual(notification_ip_alert_mode_label("unknown"), "基础")

    def test_size_and_geo_pending_formatters(self) -> None:
        self.assertEqual(format_bytes(None), "0.00 B")
        self.assertEqual(format_bytes(0), "0.00 B")
        self.assertEqual(format_bytes(1023), "1023.00 B")
        self.assertEqual(format_bytes(1024), "1.00 KB")
        self.assertEqual(format_bytes(1024 * 1024 * 3), "3.00 MB")
        self.assertEqual(format_geo_pending_text(0, 60), "待补全 0 个")
        self.assertIn("待补全 120 个", format_geo_pending_text(120, 60))

    def test_user_label_formatters_escape_and_fallback(self) -> None:
        self.assertEqual(render_user_label(12, "Alice"), "Alice (user_id: 12)")
        self.assertEqual(render_user_label(12, "用户12"), "用户 12")
        self.assertEqual(render_user_label("7", "<Mat & Co>"), "&lt;Mat &amp; Co&gt; (user_id: 7)")
        self.assertEqual(cached_user_display_name(None, 9), "用户9")

    def test_alert_message_formatters_include_routes_and_thresholds(self) -> None:
        traffic = format_traffic_alert({"user_id": 1, "name": "Alice", "period_label": "近 1 小时", "threshold": 1024, "total": 2048})
        self.assertIn("🚨 <b>流量异常告警</b>", traffic)
        self.assertIn("Alice (user_id: 1)", traffic)
        self.assertIn("近 1 小时用量：<b>2.00 KB</b>", traffic)
        self.assertIn("默认规则", traffic)

        recovered = format_traffic_alert({"user_id": 1, "name": "Alice", "period_label": "近 1 小时", "threshold": 1024, "total": 512}, recovered=True)
        self.assertIn("✅ <b>流量异常恢复</b>", recovered)
        self.assertIn("当前近 1 小时用量：512.00 B", recovered)

        ip = format_ip_alert({"user_id": 2, "name": "Bob", "period_label": "近 24 小时", "threshold": 2, "city_count": 3, "cities": ["东京", "大阪"]})
        self.assertIn("🚨 <b>异地登录</b>", ip)
        self.assertIn("Bob (user_id: 2)", ip)
        self.assertIn("近 24 小时城市数：<b>3</b>", ip)
        self.assertIn("涉及城市：东京、大阪", ip)

        changed = format_ip_alert({"user_id": 2, "name": "Bob", "period_label": "近 24 小时", "threshold": 2, "city_count": 4, "cities": ["东京"]}, previous_city_count=3)
        self.assertIn("📈 <b>异地登录变化</b>", changed)
        self.assertIn("城市数变化：3 → 4", changed)
        self.assertIn("状态：仍超过阈值", changed)

        ip_recovered = format_ip_alert({"user_id": 2, "name": "Bob", "period_label": "近 24 小时", "threshold": 2, "city_count": 1, "cities": []}, recovered=True, previous_city_count=3)
        self.assertIn("✅ <b>异地登录恢复</b>", ip_recovered)
        self.assertIn("城市数变化：3 → 1", ip_recovered)
        self.assertIn("涉及城市：未知", ip_recovered)

    def test_traffic_title_and_collector_alerts(self) -> None:
        self.assertEqual(traffic_title_for_dimension("近 1 小时", "users"), "📈 近 1 小时 用户流量统计")
        self.assertEqual(traffic_title_for_dimension("近 1 小时", "nodes"), "📈 近 1 小时 节点流量统计")
        self.assertEqual(traffic_title_for_dimension("近 1 小时", "combined"), "📈 近 1 小时 流量统计")

        admin_alert = format_collector_health_alert("mysql", recovered=False, detail="host=example.com port=3306", admin_view=True)
        self.assertIn("⚠️ <b>采集异常</b>", admin_alert)
        self.assertIn("状态：异常", admin_alert)
        self.assertIn("host=example.com port=3306", admin_alert)

        user_alert = format_collector_health_alert("mysql", recovered=True, detail="host=example.com port=3306", admin_view=False)
        self.assertIn("✅ <b>采集异常恢复</b>", user_alert)
        self.assertIn("host=[已隐藏]", user_alert)
        self.assertIn("port=[已隐藏]", user_alert)
        self.assertIn("敏感连接信息已隐藏", user_alert)

        now = int(time.time())
        gap = format_collector_gap_alert(now - 3600, now, 3600)
        self.assertIn("检测到 Bot 已恢复运行", gap)
        self.assertIn("影响时长：1 小时", gap)

    def test_redact_sensitive_text_for_non_admin(self) -> None:
        redacted = redact_sensitive_text_for_non_admin("mysql://user:pass@example.com:3306 host=10.0.0.1 port=6379 访问 https://1.2.3.4/path")
        self.assertNotIn("10.0.0.1", redacted)
        self.assertNotIn("1.2.3.4", redacted)
        self.assertNotIn("example.com", redacted)
        self.assertIn("[已隐藏URL]", redacted)
        self.assertIn("host=[已隐藏]", redacted)
        self.assertIn("port=[已隐藏]", redacted)


if __name__ == "__main__":
    unittest.main()

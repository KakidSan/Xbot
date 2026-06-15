import unittest

from xbot.bot.operation_details import (
    alert_category,
    alert_setting_before_after_detail,
    alert_type_label,
    auth_change_detail,
    ip_ignore_detail,
)


class OperationDetailsTest(unittest.TestCase):
    def test_alert_detail_helpers(self) -> None:
        self.assertEqual(alert_category("traffic"), "traffic_alert")
        self.assertEqual(alert_category("ip"), "ip_alert")
        self.assertEqual(alert_type_label("traffic"), "流量告警")
        self.assertEqual(alert_type_label("ip"), "IP 监控")
        self.assertEqual(
            alert_setting_before_after_detail("traffic", "默认规则", "旧", "新"),
            "范围：默认规则\n类型：流量告警\n修改前：旧\n修改后：新",
        )
        self.assertEqual(
            alert_setting_before_after_detail("ip", "独立规则", "旧", "新", 123),
            "对象：XBoard 用户 123\n范围：独立规则\n类型：IP 监控\n修改前：旧\n修改后：新",
        )
        self.assertIn("范围：白名单", alert_setting_before_after_detail("traffic", "白名单", "关", "开", 123))

    def test_auth_change_detail_formats_empty_added_and_deleted_users(self) -> None:
        self.assertEqual(
            auth_change_detail([], [100], [200], [200, 300], added_user_id=300),
            "修改前管理员：空\n"
            "修改后管理员：100\n"
            "修改前普通用户：200\n"
            "修改后普通用户：200, 300\n"
            "新增：300",
        )
        self.assertEqual(
            auth_change_detail([100], [], [200, 300], [200], deleted_user_ids={300}),
            "修改前管理员：100\n"
            "修改后管理员：空\n"
            "修改前普通用户：200, 300\n"
            "修改后普通用户：200\n"
            "删除：300",
        )

    def test_ip_ignore_detail_formats_before_after_state(self) -> None:
        self.assertEqual(
            ip_ignore_detail("城市", "东京", set(), {"东京"}),
            "维度：城市\n对象：东京\n修改前：未忽略\n修改后：已忽略",
        )
        self.assertEqual(
            ip_ignore_detail("ASN", "AS123", {"AS123"}, set(), xboard_user_id=42),
            "维度：ASN\n"
            "对象：AS123\n"
            "XBoard 用户：42\n"
            "修改前：已忽略\n"
            "修改后：未忽略",
        )


if __name__ == "__main__":
    unittest.main()

import re
import unittest

from xbot.bot import callback_data


class CallbackDataPatternTest(unittest.TestCase):
    def assertMatches(self, pattern: str, value: str) -> None:
        self.assertIsNotNone(re.fullmatch(pattern, value), value)

    def assertNotMatches(self, pattern: str, value: str) -> None:
        self.assertIsNone(re.fullmatch(pattern, value), value)

    def test_version_update_pattern(self) -> None:
        self.assertMatches(
            callback_data.VERSION_UPDATE_PATTERN, "version_update:start:v2.0.1"
        )
        self.assertMatches(
            callback_data.VERSION_UPDATE_PATTERN, "version_update:confirm:v3.0.0-beta"
        )
        self.assertMatches(
            callback_data.VERSION_UPDATE_PATTERN, "version_update:cancel"
        )
        self.assertNotMatches(
            callback_data.VERSION_UPDATE_PATTERN, "version_update:start:2.0.1"
        )

    def test_main_menu_pattern(self) -> None:
        self.assertMatches(callback_data.MAIN_MENU_PATTERN, "main_menu")
        self.assertMatches(
            callback_data.MAIN_MENU_PATTERN, "main_menu:system_check_refresh"
        )
        self.assertNotMatches(callback_data.MAIN_MENU_PATTERN, "main_menu:auth")

    def test_main_menu_auth_pattern_supports_legacy_and_new_namespace(self) -> None:
        self.assertMatches(callback_data.MAIN_MENU_AUTH_PATTERN, "main_menu:auth:add")
        self.assertMatches(callback_data.MAIN_MENU_AUTH_PATTERN, "auth:role_toggle:123")
        self.assertNotMatches(
            callback_data.MAIN_MENU_AUTH_PATTERN, "main_menu:ip_monitor"
        )

    def test_main_menu_ip_monitor_pattern_supports_legacy_and_new_namespace(
        self,
    ) -> None:
        self.assertMatches(
            callback_data.MAIN_MENU_IP_MONITOR_PATTERN,
            "main_menu:ip_monitor:ignore:area:0",
        )
        self.assertMatches(
            callback_data.MAIN_MENU_IP_MONITOR_PATTERN,
            "ip_monitor:ignored_rules:1",
        )
        self.assertNotMatches(
            callback_data.MAIN_MENU_IP_MONITOR_PATTERN, "ip_monitor:unknown"
        )

    def test_main_menu_parameter_pattern_supports_legacy_and_new_namespace(
        self,
    ) -> None:
        self.assertMatches(
            callback_data.MAIN_MENU_PARAMETER_CONFIG_PATTERN,
            "main_menu:parameter_config:cache_retention_select:1m",
        )
        self.assertMatches(
            callback_data.MAIN_MENU_PARAMETER_CONFIG_PATTERN,
            "params:speedtest_jump:delete_target:-123:0",
        )
        self.assertNotMatches(
            callback_data.MAIN_MENU_PARAMETER_CONFIG_PATTERN,
            "params:cache_retention_select:1q",
        )

    def test_main_menu_notifications_pattern_supports_legacy_and_new_namespace(
        self,
    ) -> None:
        self.assertMatches(
            callback_data.MAIN_MENU_NOTIFICATIONS_PATTERN, "main_menu:notifications"
        )
        self.assertMatches(
            callback_data.MAIN_MENU_NOTIFICATIONS_PATTERN, "notify:version_update"
        )
        self.assertNotMatches(
            callback_data.MAIN_MENU_NOTIFICATIONS_PATTERN, "notify:unknown"
        )

    def test_main_menu_debug_pattern_supports_legacy_and_new_namespace(self) -> None:
        self.assertMatches(
            callback_data.MAIN_MENU_DEBUG_PATTERN,
            "main_menu:debug:reset_cache_now_confirm",
        )
        self.assertMatches(
            callback_data.MAIN_MENU_DEBUG_PATTERN, "debug:reset_user_ip_toggle:123:0"
        )
        self.assertNotMatches(
            callback_data.MAIN_MENU_DEBUG_PATTERN, "debug:reset_database"
        )

    def test_alert_pattern(self) -> None:
        self.assertMatches(callback_data.ALERT_PATTERN, "alert_menu:traffic")
        self.assertMatches(
            callback_data.ALERT_PATTERN, "alert_set:traffic:threshold:123:80"
        )
        self.assertNotMatches(
            callback_data.ALERT_PATTERN, "alert_set:traffic:threshold:abc:80"
        )

    def test_traffic_daily_pattern(self) -> None:
        self.assertMatches(callback_data.TRAFFIC_DAILY_PATTERN, "traffic_menu:users")
        self.assertMatches(
            callback_data.TRAFFIC_DAILY_PATTERN, "traffic_dashboard:pin:abc123"
        )
        self.assertNotMatches(
            callback_data.TRAFFIC_DAILY_PATTERN, "traffic_dashboard:pin:abc-123"
        )

    def test_active_users_pattern(self) -> None:
        self.assertMatches(callback_data.ACTIVE_USERS_PATTERN, "active_users:24h:0")
        self.assertMatches(
            callback_data.ACTIVE_USERS_PATTERN, "ip_user_query:custom:123:456"
        )
        self.assertNotMatches(callback_data.ACTIVE_USERS_PATTERN, "active_users:2h")

    def test_ip_detail_pattern(self) -> None:
        self.assertMatches(
            callback_data.IP_DETAIL_PATTERN,
            "ip_ignore_toggle:area:ip_24h:123:1:0:abc",
        )
        self.assertMatches(callback_data.IP_DETAIL_PATTERN, "ip_alert_notice:123")
        self.assertNotMatches(
            callback_data.IP_DETAIL_PATTERN, "ip_ignore_toggle:user:ip_24h:123:1:0:abc"
        )

    def test_detail_back_pattern(self) -> None:
        self.assertMatches(callback_data.DETAIL_BACK_PATTERN, "detail_back:24h")
        self.assertMatches(callback_data.DETAIL_BACK_PATTERN, "detail_back:menu")
        self.assertNotMatches(callback_data.DETAIL_BACK_PATTERN, "detail_back:2h")

    def test_close_message_pattern(self) -> None:
        self.assertMatches(callback_data.CLOSE_MESSAGE_PATTERN, "close_message")
        self.assertMatches(callback_data.CLOSE_MESSAGE_PATTERN, "close_message")
        self.assertNotMatches(callback_data.CLOSE_MESSAGE_PATTERN, "close_message:1")

    def test_node_link_pattern(self) -> None:
        self.assertMatches(callback_data.NODE_LINK_PATTERN, "main_menu:node_links")
        self.assertMatches(callback_data.NODE_LINK_PATTERN, "node_link:select:123:0")
        self.assertNotMatches(callback_data.NODE_LINK_PATTERN, "node_link:select:abc:0")

    def test_callback_builders(self) -> None:
        self.assertEqual(callback_data.cb_auth(), "auth")
        self.assertEqual(
            callback_data.cb_auth("role_toggle", 123), "auth:role_toggle:123"
        )
        self.assertEqual(
            callback_data.cb_ip_monitor("ignore", "area", 0), "ip_monitor:ignore:area:0"
        )
        self.assertEqual(callback_data.cb_params("cover_reset"), "params:cover_reset")
        self.assertEqual(callback_data.cb_notify("daily"), "notify:daily")
        self.assertEqual(callback_data.cb_debug("tools"), "debug:tools")

    def test_normalize_new_namespaces_to_existing_handler_values(self) -> None:
        cases = {
            "main_menu:auth:add": "auth:add",
            "main_menu:ip_monitor:ignore:area:0": "ip_monitor:ignore:area:0",
            "main_menu:parameter_config:cache_retention_select:1m": "params:cache_retention_select:1m",
            "main_menu:notifications:daily": "notify:daily",
            "main_menu:debug:reset_cache_now_confirm": "debug:reset_cache_now_confirm",
            "main_menu:debug_tools": "debug:tools",
            "main_menu": "main_menu",
        }
        for raw, normalized in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(
                    callback_data.normalize_main_menu_callback(raw), normalized
                )


if __name__ == "__main__":
    unittest.main()

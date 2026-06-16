import re
import unittest

from telegram import InlineKeyboardMarkup

from xbot.bot.keyboards import (
    active_users_keyboard,
    alert_menu_keyboard,
    alert_period_keyboard,
    alert_user_list_keyboard,
    alert_user_period_select_keyboard,
    cache_retention_confirm_keyboard,
    detail_keyboard,
    ip_alert_keyboard,
    ip_detail_list_keyboard,
    ip_monitor_period_keyboard,
    ip_monitor_period_result_keyboard,
    traffic_dashboard_keyboard_static,
    traffic_period_keyboard,
    user_ip_detail_keyboard,
    user_ip_query_page_keyboard,
)
from xbot.bot.menus import (
    back_close_row,
    clear_history_confirm_keyboard,
    cover_config_keyboard,
    debug_tools_keyboard,
    empty_section_keyboard,
    health_check_keyboard,
    ip_ignore_menu_keyboard,
    ip_monitor_keyboard,
    main_menu_keyboard,
    nickname_config_keyboard,
    parameter_config_keyboard,
    reset_cache_confirm_keyboard,
    reset_cache_keyboard,
    reset_user_ip_multi_confirm_keyboard,
    traffic_management_keyboard,
)


def callback_data(markup: InlineKeyboardMarkup) -> list[str]:
    return [button.callback_data for row in markup.inline_keyboard for button in row]


class MenusTest(unittest.TestCase):
    def assert_markup(self, markup: InlineKeyboardMarkup) -> None:
        self.assertIsInstance(markup, InlineKeyboardMarkup)
        self.assertGreater(len(markup.inline_keyboard), 0)
        self.assertTrue(all(button.callback_data for row in markup.inline_keyboard for button in row))

    def test_back_close_row_defaults_and_override(self) -> None:
        self.assertEqual([button.callback_data for button in back_close_row()], ["main_menu", "close_message"])
        self.assertEqual([button.callback_data for button in back_close_row("main_menu:ip_monitor")], ["main_menu:ip_monitor", "close_message"])

    def test_main_menu_admin_routes_are_stable(self) -> None:
        callbacks = callback_data(main_menu_keyboard(is_admin=True))
        self.assertIn("main_menu:system_check", callbacks)
        self.assertIn("main_menu:notifications", callbacks)
        self.assertIn("main_menu:traffic_management", callbacks)
        self.assertIn("main_menu:ip_monitor", callbacks)
        self.assertIn("main_menu:op_logs", callbacks)
        self.assertIn("main_menu:auth", callbacks)
        self.assertIn("close_message", callbacks)

    def test_menu_keyboards_return_markup_with_expected_callbacks(self) -> None:
        cases = [
            (clear_history_confirm_keyboard(), {"main_menu:clear_history_confirm", "main_menu"}),
            (empty_section_keyboard(), {"main_menu", "close_message"}),
            (health_check_keyboard(), {"main_menu:system_check_refresh", "main_menu", "close_message"}),
            (traffic_management_keyboard(), {"main_menu:traffic_users", "main_menu:traffic_nodes", "alert_menu:traffic"}),
            (ip_monitor_keyboard(), {"main_menu:ip_monitor:period", "alert_menu:ip", "main_menu:ip_monitor:ignore"}),
            (ip_ignore_menu_keyboard(), {"main_menu:ip_monitor:ignore:area:0", "main_menu:ip_monitor:ignored_rules:0"}),
            (parameter_config_keyboard(), {"main_menu:parameter_config:cover", "main_menu:parameter_config:nickname", "main_menu:parameter_config:cache_retention"}),
            (debug_tools_keyboard(is_admin=True), {"main_menu:debug:reset_cache", "main_menu:debug:reset_user_ip"}),
            (reset_cache_keyboard(), {"main_menu:debug:reset_cache_now", "main_menu:debug:reset_cache_floor"}),
            (reset_cache_confirm_keyboard(), {"main_menu:debug:reset_cache_now_confirm"}),
            (reset_user_ip_multi_confirm_keyboard([1, 2]), {"main_menu:debug:reset_user_ip_multi_confirm", "main_menu:debug:reset_user_ip_page:0"}),
            (cover_config_keyboard(), {"main_menu:parameter_config:cover_reset"}),
            (nickname_config_keyboard(), {"main_menu:parameter_config:nickname_reset"}),
        ]
        for markup, expected_callbacks in cases:
            with self.subTest(expected_callbacks=expected_callbacks):
                self.assert_markup(markup)
                self.assertTrue(expected_callbacks.issubset(set(callback_data(markup))))


class KeyboardsTest(unittest.TestCase):
    def assert_markup(self, markup: InlineKeyboardMarkup) -> None:
        self.assertIsInstance(markup, InlineKeyboardMarkup)
        self.assertGreater(len(markup.inline_keyboard), 0)
        callbacks = callback_data(markup)
        self.assertTrue(all(callbacks))
        self.assertNotIn(None, callbacks)

    def test_ip_and_traffic_dashboard_callbacks(self) -> None:
        callbacks = callback_data(ip_alert_keyboard({"user_id": 12, "period": "1h"}))
        self.assertRegex(callbacks[0], r"^ip_active_user_detail:iprange_\d+_\d+:12:0:alert$")
        self.assertEqual(callbacks[1], "alert_user:ip:12:alert")
        self.assertRegex(callbacks[2], r"^ip_ignore_page:area:iprange_\d+_\d+:12:0:0:alert$")

        self.assertEqual(
            callback_data(traffic_dashboard_keyboard_static("traffic_1h", is_pinned=True)),
            ["traffic_menu:traffic_1h", "traffic_dashboard:unpin:traffic_1h", "traffic_dashboard:delete:traffic_1h"],
        )
        ip_callbacks = callback_data(traffic_dashboard_keyboard_static("ip_1h"))
        self.assertIn("active_users:24h", ip_callbacks)
        self.assertIn("ip_custom:start", ip_callbacks)
        self.assertIn("ip_detail_list:ip_1h:0", ip_callbacks)
        self.assertIn("traffic_dashboard:pin:ip_1h", ip_callbacks)

    def test_alert_keyboards_callbacks(self) -> None:
        self.assertEqual(callback_data(alert_menu_keyboard("traffic")), ["alert_global:traffic", "alert_users:traffic:0", "main_menu:traffic_management", "close_message"])
        self.assertEqual(callback_data(alert_menu_keyboard("ip")), ["alert_global:ip", "alert_users:ip:0", "main_menu:ip_monitor", "close_message"])
        self.assertEqual(
            callback_data(alert_period_keyboard("alert_set", "traffic")),
            [
                "alert_set:traffic:period:1h",
                "alert_set:traffic:period:24h",
                "alert_set:traffic:period:7d",
                "alert_set:traffic:period:today",
                "alert_set:traffic:period:week",
            ],
        )
        self.assertIn("alert_set:ip:period:24h:99", callback_data(alert_user_period_select_keyboard("ip", 99)))

    def test_list_and_pagination_keyboards(self) -> None:
        users = [(idx, f"用户{idx}") for idx in range(1, 8)]
        callbacks = callback_data(active_users_keyboard("1h", users, page=1))
        self.assertIn("active_user_detail:1h:6", callbacks)
        self.assertIn("active_users_query:1h:0", callbacks)
        self.assertIn("active_users_cancel:1h", callbacks)

        ip_callbacks = callback_data(ip_detail_list_keyboard("ip_24h", users, page=1))
        self.assertIn("ip_active_user_detail:ip_24h:6", ip_callbacks)
        self.assertIn("ip_detail_list:ip_24h:0", ip_callbacks)
        self.assertIn("main_menu:ip_monitor:period", ip_callbacks)

        alert_callbacks = callback_data(alert_user_list_keyboard("traffic", [{"user_id": i, "name": f"U{i}"} for i in range(12)], page=0))
        self.assertIn("alert_user:traffic:0", alert_callbacks)
        self.assertIn("alert_users:traffic:1", alert_callbacks)

    def test_period_and_detail_keyboards_callbacks(self) -> None:
        self.assertEqual(
            callback_data(ip_monitor_period_keyboard()),
            ["active_users:1h", "active_users:24h", "active_users:7d", "active_users:30d", "ip_custom:start", "main_menu:ip_monitor", "close_message"],
        )
        self.assertNotIn("active_users:24h", callback_data(ip_monitor_period_result_keyboard("24h")))
        self.assertEqual(callback_data(detail_keyboard("7d")), ["detail_back:7d", "close_message"])
        self.assertEqual(callback_data(detail_keyboard("unexpected")), ["detail_back:menu", "close_message"])

    def test_user_detail_and_traffic_period_callbacks(self) -> None:
        callbacks = callback_data(user_ip_detail_keyboard("ip_7d", 42, total_ips=11, page=0, source="alert"))
        self.assertIn("ip_active_user_detail:ip_7d:42:1:alert", callbacks)
        self.assertIn("ip_ignore_page:cidr:ip_7d:42:0:0:alert", callbacks)
        self.assertIn("ip_alert_notice:42", callbacks)

        query_callbacks = callback_data(user_ip_query_page_keyboard("30d", 42, total_ips=11, page=1))
        self.assertIn("user_ip_page:42:0:30d", query_callbacks)
        self.assertIn("detail_back:30d", query_callbacks)

        period_callbacks = callback_data(traffic_period_keyboard("users", source_kind="traffic_1h"))
        self.assertIn("traffic_switch:preset_1h:users", period_callbacks)
        self.assertIn("traffic_custom:start:users", period_callbacks)
        self.assertIn("traffic_back:traffic_1h", period_callbacks)
        self.assertTrue(all(re.match(r"^[a-z_]+(?::[A-Za-z0-9_]+)+$|^close_message$", callback) for callback in period_callbacks))

    def test_cache_retention_confirm_keyboard(self) -> None:
        self.assertEqual(
            callback_data(cache_retention_confirm_keyboard("1q")),
            ["main_menu:parameter_config:cache_retention_confirm:1q", "main_menu:parameter_config:cache_retention", "close_message"],
        )


if __name__ == "__main__":
    unittest.main()

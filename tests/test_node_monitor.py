import base64
import unittest
from pathlib import Path
from unittest.mock import patch

from xbot.config import AppConfig, MySQLConfig, RedisConfig, TelegramConfig
from xbot.node_monitor import (
    _base64_decode_subscription,
    _fetch_official_subscription_text_sync,
    _subscription_nodes_cache,
    fetch_subscription_nodes_sync,
    format_node_link_detail_sync,
    format_node_links_text_sync,
    node_link_detail_keyboard_sync,
    node_links_keyboard_sync,
    refresh_subscription_nodes_sync,
)


def app_config() -> AppConfig:
    return AppConfig(
        telegram=TelegramConfig(bot_token="123:abc", super_admin_user_ids={1}),
        redis=RedisConfig(),
        mysql=MySQLConfig(
            host="127.0.0.1",
            port=3306,
            database="xboard",
            username="reader",
            password="secret",
        ),
        cache_path=Path(":memory:"),
        link_extract_user_id=7,
    )


def keyboard_callbacks(markup) -> list[str]:
    callbacks: list[str] = []
    for row in markup.inline_keyboard:
        for button in row:
            if button.callback_data:
                callbacks.append(button.callback_data)
    return callbacks


class FakeCursor:
    def __init__(self) -> None:
        self.queries: list[tuple[str, tuple[object, ...] | None]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        self.queries.append((sql, params))

    def fetchone(self) -> dict[str, str]:
        return {"token": "tok en/测试"}

    def fetchall(self) -> list[dict[str, str]]:
        return [
            {"name": "subscribe_url", "value": "https://sub.example.com/"},
            {"name": "subscribe_path", "value": "api/sub"},
        ]


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_obj = FakeCursor()
        self.closed = False

    def cursor(self) -> FakeCursor:
        return self.cursor_obj

    def close(self) -> None:
        self.closed = True


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class NodeMonitorTest(unittest.TestCase):
    def setUp(self) -> None:
        _subscription_nodes_cache.clear()

    def test_base64_decode_accepts_plain_and_encoded_subscriptions(self) -> None:
        plain = "ss://one#节点一\nvmess://two#节点二"
        encoded = base64.b64encode(plain.encode())

        self.assertEqual(_base64_decode_subscription(plain.encode()), plain)
        self.assertEqual(_base64_decode_subscription(encoded), plain)
        self.assertEqual(
            _base64_decode_subscription(b"not-base64-@@@"), "not-base64-@@@"
        )

    def test_fetch_official_subscription_text_uses_mysql_token_and_decodes_response(
        self,
    ) -> None:
        cfg = app_config()
        conn = FakeConnection()
        raw_text = "ss://alpha#香港 01\nss://beta#日本 02"
        encoded = base64.b64encode(raw_text.encode())
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout: int):
            captured["url"] = request.full_url
            captured["user_agent"] = request.headers.get("User-agent")
            captured["timeout"] = timeout
            return FakeResponse(encoded)

        with (
            patch("xbot.node_monitor.mysql_config_missing", return_value=False),
            patch("xbot.node_monitor.mysql_connect", return_value=conn),
            patch("xbot.node_monitor.urllib.request.urlopen", side_effect=fake_urlopen),
        ):
            text = _fetch_official_subscription_text_sync(cfg, "general")

        self.assertEqual(text, raw_text)
        self.assertTrue(conn.closed)
        self.assertIn(
            "/api/sub/tok%20en%2F%E6%B5%8B%E8%AF%95?flag=general", str(captured["url"])
        )
        self.assertEqual(captured["user_agent"], "XbotLinkExtract/1.0 (general)")
        self.assertEqual(captured["timeout"], 15)
        self.assertEqual(conn.cursor_obj.queries[0][1], (7,))

    def test_fetch_subscription_nodes_parses_official_lines_and_uses_cache(
        self,
    ) -> None:
        cfg = app_config()
        subscription = "\n".join(
            [
                "ss://alpha#香港%2001",
                "",
                "not a link",
                "trojan://beta@example.com:443#Trojan节点",
            ]
        )

        with patch(
            "xbot.node_monitor._fetch_official_subscription_text_sync",
            return_value=subscription,
        ) as fetch_mock:
            nodes = fetch_subscription_nodes_sync(cfg)
            cached_nodes = fetch_subscription_nodes_sync(cfg)
            refreshed_total = refresh_subscription_nodes_sync(cfg)

        self.assertEqual(fetch_mock.call_count, 2)
        self.assertEqual(refreshed_total, 2)
        self.assertEqual(cached_nodes, nodes)
        self.assertEqual([node.id for node in nodes], [1, 2])
        self.assertEqual(nodes[0].name, "香港 01")
        self.assertEqual(nodes[0].scheme, "ss")
        self.assertEqual(nodes[1].name, "Trojan节点")
        self.assertEqual(nodes[1].scheme, "trojan")

    def test_node_links_keyboard_uses_expected_callbacks_and_pagination(self) -> None:
        cfg = app_config()
        nodes_text = "\n".join(f"ss://node{i}#节点{i}" for i in range(1, 13))
        with patch(
            "xbot.node_monitor._fetch_official_subscription_text_sync",
            return_value=nodes_text,
        ):
            markup = node_links_keyboard_sync(cfg, page=1)

        callbacks = keyboard_callbacks(markup)
        self.assertIn("node_link:select:11:1", callbacks)
        self.assertIn("node_link:select:12:1", callbacks)
        self.assertIn("node_link:page:0", callbacks)
        self.assertIn("node_link:refresh:1", callbacks)
        self.assertIn("main_menu", callbacks)
        self.assertNotIn("back_to_main_menu", callbacks)
        self.assertNotIn("main_menu:node_status", callbacks)

    def test_node_link_text_and_detail_escape_node_values(self) -> None:
        cfg = app_config()
        nodes_text = "ss://secret#HK%20%26%20JP"
        with patch(
            "xbot.node_monitor._fetch_official_subscription_text_sync",
            return_value=nodes_text,
        ):
            list_text = format_node_links_text_sync(cfg)
            detail_text = format_node_link_detail_sync(cfg, 1)
            missing_text = format_node_link_detail_sync(cfg, 99)

        self.assertIn("可用节点：<b>1</b> 个", list_text)
        self.assertIn("HK &amp; JP", detail_text)
        self.assertIn("<code>ss://secret#HK%20%26%20JP</code>", detail_text)
        self.assertIn("节点不存在", missing_text)

    def test_node_link_detail_keyboard_uses_personal_test_tools(self) -> None:
        rows = [
            {"telegram_id": 1001, "nickname": "数字工具", "username": None},
            {"telegram_id": -123, "nickname": "用户名工具", "username": "tool_bot"},
        ]
        with patch(
            "xbot.node_monitor.speedtest_jump_targets_sync", return_value=rows
        ) as targets:
            markup = node_link_detail_keyboard_sync(Path("cache.sqlite3"), 42, page=3)

        targets.assert_called_once_with(Path("cache.sqlite3"), 42)
        first = markup.inline_keyboard[0][0]
        second = markup.inline_keyboard[1][0]
        callbacks = keyboard_callbacks(markup)
        self.assertEqual(first.text, "数字工具")
        self.assertEqual(first.url, "tg://user?id=1001")
        self.assertEqual(second.url, "https://t.me/tool_bot")
        self.assertIn("node_link:page:3", callbacks)
        self.assertIn("close_message", callbacks)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from typing import Any


def _callback(namespace: str, *parts: Any) -> str:
    return ":".join((namespace, *(str(part) for part in parts))) if parts else namespace


def cb_auth(*parts: Any) -> str:
    return _callback("auth", *parts)


def cb_ip_monitor(*parts: Any) -> str:
    return _callback("ip_monitor", *parts)


def cb_params(*parts: Any) -> str:
    return _callback("params", *parts)


def cb_notify(*parts: Any) -> str:
    return _callback("notify", *parts)


def cb_debug(*parts: Any) -> str:
    return _callback("debug", *parts)


VERSION_UPDATE_PATTERN = r"^version_update:(?:start|confirm):v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$|^version_update:cancel$"
MAIN_MENU_PATTERN = r"^main_menu(?::(?:init_ack|clear_history|clear_history_confirm|system_check|system_check_refresh|traffic_management|traffic_users|traffic_nodes|traffic_alerts))?$"
MAIN_MENU_OP_LOGS_PATTERN = r"^main_menu:op_logs(?::(?:traffic_alert|ip_alert|ip_ignore|reset_cache|reset_ip|parameter_config|auth)(?::\d+)?)?$"
MAIN_MENU_AUTH_PATTERN = r"^(?:main_menu:auth|auth)(?::(?:add|delete|del_done|del_toggle:\d+|del_confirm|roles|role_toggle:\d+|role_save))?$"
MAIN_MENU_IP_MONITOR_PATTERN = r"^(?:main_menu:ip_monitor|ip_monitor)(?::(?:period|user_query|ignore|ignored_rules:\d+|ignored_rule_toggle:\d+:[A-Za-z0-9]+|ignore:(?:area|asn|cidr):\d+|ignore_toggle:(?:area|asn|cidr):\d+:[A-Za-z0-9]+))?$"
MAIN_MENU_NOOP_PATTERN = r"^main_menu:noop$"
NODE_LINK_PATTERN = (
    r"^(main_menu:node_links|node_link:(?:page:\d+|refresh:\d+|select:\d+:\d+))$"
)
MAIN_MENU_PARAMETER_CONFIG_PATTERN = r"^(?:main_menu:parameter_config|params)(?::(?:cover|cover_reset|nickname|nickname_reset|cache_retention|cache_retention_select:(?:1m|1y|all)|cache_retention_confirm:(?:1m|1y|all)|speedtest_jump|speedtest_jump:add|speedtest_jump:delete:\d+|speedtest_jump:delete_target:-?\d+:\d+))?$"
MAIN_MENU_NOTIFICATIONS_PATTERN = r"^(?:main_menu:(?:notifications|status_notice)|notify)(?::(?:daily|weekly|monthly|collector|traffic_alert|ip_alert|version_update))?$"
MAIN_MENU_DEBUG_PATTERN = r"^(?:main_menu:debug_tools|debug:tools|main_menu:debug(?::reset_cache|:reset_cache_now|:reset_cache_now_confirm|:reset_cache_floor|:reset_user_ip|:reset_user_ip_page:\d+|:reset_user_ip_toggle:\d+:\d+|:reset_user_ip_done|:reset_user_ip_multi_confirm)|debug(?::reset_cache|:reset_cache_now|:reset_cache_now_confirm|:reset_cache_floor|:reset_user_ip|:reset_user_ip_page:\d+|:reset_user_ip_toggle:\d+:\d+|:reset_user_ip_done|:reset_user_ip_multi_confirm))$"
ALERT_PATTERN = r"^(alert_menu:(?:traffic|ip)|alert_period_page:(?:traffic|ip):\d+|alert_global_period_page:(?:traffic|ip)|alert_global:(?:traffic|ip)(?::(?:custom|period:(?:1h|24h|7d|today|week)))?|alert_users:(?:traffic|ip):\d+|alert_user:(?:traffic|ip):\d+(?::alert)?|alert_set:(?:traffic|ip):(?:custom:\d+|period:(?:1h|24h|7d|today|week):\d+|threshold:\d+:\d+|whitelist:\d+|reset:\d+))$"
TRAFFIC_DAILY_PATTERN = r"^(traffic_menu(?::[A-Za-z0-9_]+)?|traffic_back:[A-Za-z0-9_]+|traffic_(?:period|switch):(preset_1h|preset_24h|preset_7d|preset_30d|today|yesterday|this_week|this_month)(?::(?:users|nodes))?|ip_custom:start|traffic_custom:(start(?::(?:combined|users|nodes))?|now|(year|month|day|hour|minute):\d+|back:(year|month|day|hour))|traffic_floor:(start|confirm:\d+)|traffic_dashboard:(pin|unpin|delete):[A-Za-z0-9_]+)$"
ACTIVE_USERS_PATTERN = r"^(active_users(?::|_query:)(1h|24h|7d|30d)(?::\d+)?|ip_user_query:(?:(1h|24h|7d|30d)|custom:\d+:\d+)|user_ip_page:\d+:\d+:(?:all|(?:1h|24h|7d|30d)|custom:\d+:\d+)|active_user_detail:(1h|24h|7d|30d):\d+|active_users_cancel:(1h|24h|7d|30d)|noop)$"
IP_DETAIL_PATTERN = r"^(?:ip_(?:detail_list|active_user_detail):(ip_(?:1h|24h|7d|30d)|iprange_\d+_\d+):(\d+)(?::\d+)?(?::alert)?|ip_alert_notice:\d+|ip_ignore_menu:(ip_(?:1h|24h|7d|30d)|iprange_\d+_\d+):\d+:\d+(?::alert)?|ip_ignore_page:(?:area|asn|cidr):(ip_(?:1h|24h|7d|30d)|iprange_\d+_\d+):\d+:\d+:\d+(?::alert)?|ip_ig_t:[A-Za-z0-9]+|ip_ignore_toggle:(?:area|asn|cidr):(ip_(?:1h|24h|7d|30d)|iprange_\d+_\d+):\d+:\d+:\d+:[A-Za-z0-9]+(?::alert)?)$"
DETAIL_BACK_PATTERN = r"^detail_back:(1h|24h|7d|30d|menu)$"
CLOSE_MESSAGE_PATTERN = r"^close_message$"


def normalize_main_menu_callback(data: str) -> str:
    """Map legacy ``main_menu:*`` callbacks to the newer handler namespaces.

    Router patterns accept both forms during the compatibility window. Handlers
    should branch on the smaller domain namespace.
    """
    if data == "main_menu:debug_tools":
        return "debug:tools"
    if data.startswith("main_menu:auth"):
        return "auth" + data[len("main_menu:auth") :]
    if data.startswith("main_menu:ip_monitor"):
        return "ip_monitor" + data[len("main_menu:ip_monitor") :]
    if data.startswith("main_menu:parameter_config"):
        return "params" + data[len("main_menu:parameter_config") :]
    if data == "main_menu:status_notice":
        return "notify"
    if data.startswith("main_menu:notifications"):
        return "notify" + data[len("main_menu:notifications") :]
    if data.startswith("main_menu:debug"):
        return "debug" + data[len("main_menu:debug") :]
    return data

from __future__ import annotations

from .common import (
    ALERT_DEFAULT_PERIOD,
    Application,
    BEIJING_TZ,
    InlineKeyboardMarkup,
    Path,
    alert_period_label,
    beijing_midnight,
    beijing_now,
    datetime,
    json,
    log,
    timedelta,
)
from .db.cache import (
    alert_effective_rule_detail_for_user_sync,
    alert_notification_chats_sync,
    alert_state_get_sync,
    alert_state_set_sync,
    current_ip_alert_detail_for_user_sync,
    current_traffic_alert_value_for_user_sync,
    initialization_status_sync,
    ip_alert_notification_chat_modes_sync,
    ip_alert_rows_sync,
    traffic_alert_rows_sync,
)
from .bot.formatters import alert_period_label, cached_user_name_by_id, format_ip_alert, format_traffic_alert
from .bot.keyboards import ip_alert_keyboard

async def send_user_alert_to_chats(app: Application, chat_ids: list[str], text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    for chat_id in chat_ids:
        try:
            await app.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=reply_markup)
        except Exception as exc:
            log.warning("发送异常提醒失败 chat=%s：%s", chat_id, exc)

async def send_user_alert(app: Application, cfg: AppConfig, cache_path: Path, alert_type: str, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    await send_user_alert_to_chats(app, alert_notification_chats_sync(cache_path, cfg, alert_type), text, reply_markup)

async def check_traffic_alerts(app: Application, cfg: AppConfig, cache_path: Path) -> None:
    rows = traffic_alert_rows_sync(cache_path)
    current = {int(row["user_id"]): row for row in rows}
    previous_raw = alert_state_get_sync(cache_path, "traffic_alert_active_users") or "[]"
    try:
        previous = {int(x) for x in json.loads(previous_raw)}
    except Exception:
        previous = set()
    for user_id, row in current.items():
        if user_id not in previous:
            await send_user_alert(app, cfg, cache_path, "traffic", format_traffic_alert(row))
    for user_id in sorted(previous - set(current)):
        period, period_label, threshold, rule_type = alert_effective_rule_detail_for_user_sync(cache_path, "traffic", user_id)
        total = current_traffic_alert_value_for_user_sync(cache_path, user_id, period)
        row = {"user_id": user_id, "name": cached_user_name_by_id(cache_path, user_id) or f"用户{user_id}", "total": total, "threshold": threshold, "period": period, "period_label": period_label, "rule_type": rule_type}
        await send_user_alert(app, cfg, cache_path, "traffic", format_traffic_alert(row, recovered=True))
    alert_state_set_sync(cache_path, "traffic_alert_active_users", json.dumps(sorted(current)))

async def check_ip_alerts(app: Application, cfg: AppConfig, cache_path: Path) -> None:
    init_status = initialization_status_sync(cache_path, cfg.ip_geo_queries_per_minute)
    if init_status.get("initializing"):
        log.info(
            "IP 告警检查等待初始化完成：活跃 IP %s 条，归属地待查询 %s 条",
            init_status.get("active_ips"), init_status.get("geo_pending"),
        )
        return
    rows = ip_alert_rows_sync(cache_path)
    current = {int(row["user_id"]): row for row in rows}
    previous_raw = alert_state_get_sync(cache_path, "ip_alert_active_users") or "{}"
    try:
        loaded = json.loads(previous_raw)
        if isinstance(loaded, dict):
            previous = {int(user_id): int(count) for user_id, count in loaded.items()}
        else:
            # 兼容上一版只存活跃 user_id 列表的状态。
            previous = {int(user_id): -1 for user_id in loaded}
    except Exception:
        previous = {}
    chat_modes = ip_alert_notification_chat_modes_sync(cache_path, cfg)
    basic_or_advanced_chats = [chat_id for chat_id, mode in chat_modes.items() if mode in {"basic", "advanced"}]
    advanced_chats = [chat_id for chat_id, mode in chat_modes.items() if mode == "advanced"]
    for user_id, row in current.items():
        city_count = int(row.get("city_count") or 0)
        previous_count = previous.get(user_id)
        if previous_count is None:
            await send_user_alert_to_chats(app, basic_or_advanced_chats, format_ip_alert(row), ip_alert_keyboard(row))
        elif previous_count != city_count:
            await send_user_alert_to_chats(app, advanced_chats, format_ip_alert(row, previous_city_count=previous_count), ip_alert_keyboard(row))
    for user_id in sorted(set(previous) - set(current)):
        period, period_label, threshold, rule_type = alert_effective_rule_detail_for_user_sync(cache_path, "ip", user_id)
        city_count, cities = current_ip_alert_detail_for_user_sync(cache_path, user_id, period)
        row = {"user_id": user_id, "name": cached_user_name_by_id(cache_path, user_id) or f"用户{user_id}", "city_count": city_count, "threshold": threshold, "period": period, "period_label": period_label, "cities": cities, "rule_type": rule_type}
        await send_user_alert_to_chats(app, basic_or_advanced_chats, format_ip_alert(row, recovered=True, previous_city_count=previous.get(user_id)))
    alert_state_set_sync(cache_path, "ip_alert_active_users", json.dumps({str(user_id): int(row.get("city_count") or 0) for user_id, row in current.items()}, sort_keys=True))

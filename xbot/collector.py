from __future__ import annotations

from .common import (
    Application,
    BEIJING_TZ,
    BadRequest,
    MySQLError,
    Path,
    TRAFFIC_REPORT_KINDS,
    TRAFFIC_SAMPLE_GAP_TOLERANCE_SECONDS,
    asyncio,
    beijing_now,
    datetime,
    is_admin_user_id,
    log,
)
from .config import AppConfig
from .db.cache import (
    auto_delete_due_messages_sync,
    auto_delete_message_delete_sync,
    auto_delete_message_set_sync,
    cache_retention_days_sync,
    collector_notification_chats_sync,
    init_cache,
    initialization_mark_complete_sync,
    initialization_mark_started_sync,
    initialization_status_sync,
    mark_traffic_report_sent_sync,
    default_allowlist_notification_chats_sync,
    pinned_dashboard_all_sync,
    pinned_dashboard_delete_message_sync,
    pinned_dashboard_delete_sync,
    sample_traffic_deltas_sync,
    set_collector_health_status_sync,
    traffic_report_already_sent_sync,
    upsert_cache_records,
    upsert_cache_users,
)
from .db.redis import collect_redis_ip_records_sync
from .geo import (
    backfill_geo_pending_rate_limited,
    backfill_geo_pending_until_complete,
    cache_geo_status_sync,
)
from .alerts import check_ip_alerts, check_traffic_alerts
from .bot.formatters import (
    format_collector_gap_alert,
    format_collector_health_alert,
    format_duration,
    redact_sensitive_text_for_non_admin,
    traffic_dashboard_text_from_kind_sync,
    traffic_report_text_sync,
)
from .bot.keyboards import traffic_dashboard_keyboard_static


def run_cache_collection_once(
    cfg: AppConfig, cache_path: Path
) -> tuple[bool, str, bool, str, int, int, int]:
    init_cache(cache_path)
    records = collect_redis_ip_records_sync(cfg.redis)
    if isinstance(records, str):
        log.warning("缓存采集 Redis 失败：%s", records)
        return False, records, True, "", 0, 0, 0
    user_ids = upsert_cache_records(
        cache_path, records, cache_retention_days_sync(cache_path)
    )
    mysql_ok = True
    mysql_detail = ""
    try:
        upsert_cache_users(cache_path, cfg.mysql, user_ids)
    except MySQLError as exc:
        log.warning("缓存采集 MySQL 用户信息失败：%s", exc)
        mysql_ok = False
        mysql_detail = f"{type(exc).__name__}: {exc}"

    # 采集器发现新 IP 后自动补全归属地，不依赖前台查询或手动 /init。
    # 默认 collector_interval_seconds=60、ip_geo_queries_per_minute=30，即每轮最多自动查 30 个。
    # 如果短时间新增量超过免费 API 安全速率，剩余 pending 会在后续采集轮次继续自动补全。
    geo_limit = max(
        1,
        int(
            cfg.ip_geo_queries_per_minute
            * max(5.0, cfg.collector_interval_seconds)
            / 60
        ),
    )
    geo_total, geo_success, geo_failed, _ = backfill_geo_pending_rate_limited(
        cache_path,
        limit=geo_limit,
        queries_per_minute=cfg.ip_geo_queries_per_minute,
        stop_when_rate_limited=True,
    )
    if geo_total:
        pending_after = cache_geo_status_sync(cache_path)["geo_pending"]
        log.info(
            "后台 IP 归属地自动补全：本轮待处理 %s 个，成功 %s 个，失败 %s 个，剩余 %s 个",
            geo_total,
            geo_success,
            geo_failed,
            pending_after,
        )
        init_status = initialization_status_sync(
            cache_path, cfg.ip_geo_queries_per_minute
        )
        if init_status.get("initializing") and pending_after <= 0:
            initialization_mark_complete_sync(
                cache_path, len(records), geo_total, geo_success, geo_failed
            )
            log.info(
                "后台初始化完成：Redis IP 记录 %s 条，归属地待处理已清零", len(records)
            )
    else:
        init_status = initialization_status_sync(
            cache_path, cfg.ip_geo_queries_per_minute
        )
        if (
            init_status.get("initializing")
            and init_status.get("geo_pending", 0) <= 0
            and records
        ):
            initialization_mark_complete_sync(cache_path, len(records), 0, 0, 0)
            log.info(
                "后台初始化完成：Redis IP 记录 %s 条，无待补全归属地", len(records)
            )
    log.info(
        "缓存采集完成：Redis IP 记录 %s 条，用户 %s 个", len(records), len(user_ids)
    )
    return True, "", mysql_ok, mysql_detail, geo_total, geo_success, geo_failed


async def send_collector_alert(
    app: Application,
    cfg: AppConfig,
    cache_path: Path,
    service: str,
    ok: bool,
    detail: str = "",
) -> None:
    chats = await asyncio.to_thread(collector_notification_chats_sync, cache_path, cfg)
    for chat_id in chats:
        try:
            admin_view = (
                is_admin_user_id(int(chat_id), cfg)
                if str(chat_id).lstrip("-").isdigit()
                else False
            )
            text = format_collector_health_alert(
                service, recovered=ok, detail=detail, admin_view=admin_view
            )
            await app.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
        except Exception as exc:
            log.warning("发送采集异常通知失败 chat=%s：%s", chat_id, exc)


async def send_collector_text_alert(
    app: Application, cfg: AppConfig, cache_path: Path, text: str
) -> None:
    chats = await asyncio.to_thread(collector_notification_chats_sync, cache_path, cfg)
    for chat_id in chats:
        try:
            admin_view = (
                is_admin_user_id(int(chat_id), cfg)
                if str(chat_id).lstrip("-").isdigit()
                else False
            )
            safe_text = (
                text
                if admin_view
                else redact_sensitive_text_for_non_admin(text)
                + "\n\n敏感连接信息已隐藏，仅管理员可查看完整详情。"
            )
            await app.bot.send_message(
                chat_id=chat_id, text=safe_text, parse_mode="HTML"
            )
        except Exception as exc:
            log.warning("发送采集异常通知失败 chat=%s：%s", chat_id, exc)


async def notify_collector_health_transition(
    app: Application,
    cfg: AppConfig,
    cache_path: Path,
    service: str,
    ok: bool,
    detail: str = "",
) -> None:
    previous_status, current_status = await asyncio.to_thread(
        set_collector_health_status_sync, cache_path, service, ok, detail
    )
    if previous_status == current_status:
        return
    if previous_status is None and ok:
        return
    await send_collector_alert(app, cfg, cache_path, service, ok, detail)


async def cache_collector_loop(
    app: Application, cfg: AppConfig, cache_path: Path, stop_event: asyncio.Event
) -> None:
    """Run Redis/MySQL -> SQLite cache collection immediately, then periodically."""
    while not stop_event.is_set():
        try:
            (
                redis_ok,
                redis_detail,
                mysql_ok,
                mysql_detail,
                geo_total,
                geo_success,
                geo_failed,
            ) = await asyncio.to_thread(run_cache_collection_once, cfg, cache_path)
            await notify_collector_health_transition(
                app,
                cfg,
                cache_path,
                "redis",
                redis_ok,
                redis_detail or "Redis 缓存采集已恢复成功。",
            )
            # Redis 失败时，本轮不会继续检查 MySQL 用户信息；不要把“未检查”误判成 MySQL 恢复，
            # 否则会和流量采样循环的 MySQL 失败状态互相覆盖，造成“失败-恢复”反复通知。
            if redis_ok or mysql_detail:
                await notify_collector_health_transition(
                    app,
                    cfg,
                    cache_path,
                    "mysql",
                    mysql_ok,
                    mysql_detail or "MySQL 用户信息采集已恢复成功。",
                )
            if geo_success:
                await notify_collector_health_transition(
                    app,
                    cfg,
                    cache_path,
                    "ip_api",
                    True,
                    "IP-API 已恢复响应，本轮已有 IP 归属地补全成功。",
                )
            elif geo_failed:
                await notify_collector_health_transition(
                    app,
                    cfg,
                    cache_path,
                    "ip_api",
                    False,
                    f"本轮 IP 归属地补全失败 {geo_failed} 个。",
                )
            await check_ip_alerts(app, cfg, cache_path)
        except Exception as exc:
            log.exception("缓存采集任务异常：%s", exc)
        try:
            await asyncio.wait_for(
                stop_event.wait(), timeout=max(5.0, cfg.collector_interval_seconds)
            )
        except asyncio.TimeoutError:
            continue


async def traffic_sampler_loop(
    app: Application, cfg: AppConfig, cache_path: Path, stop_event: asyncio.Event
) -> None:
    """Sample Xboard cumulative counters once per minute and store local deltas."""
    while not stop_event.is_set():
        try:
            (
                users,
                nodes,
                deltas,
                gap_seconds,
                previous_ts,
                current_ts,
            ) = await asyncio.to_thread(
                sample_traffic_deltas_sync, cache_path, cfg.mysql
            )
            await check_traffic_alerts(app, cfg, cache_path)
            if gap_seconds > TRAFFIC_SAMPLE_GAP_TOLERANCE_SECONDS:
                log.warning("检测到流量采样间隔异常：%s", format_duration(gap_seconds))
                await send_collector_text_alert(
                    app,
                    cfg,
                    cache_path,
                    format_collector_gap_alert(previous_ts, current_ts, gap_seconds),
                )
            log.info(
                "流量采样完成：用户 %s 个，节点 %s 个，增量记录 %s 条",
                users,
                nodes,
                deltas,
            )
            await notify_collector_health_transition(
                app, cfg, cache_path, "mysql", True, "流量采样只读查询已恢复成功。"
            )
        except Exception as exc:
            log.exception("流量采样任务异常：%s", exc)
            await notify_collector_health_transition(
                app, cfg, cache_path, "mysql", False, f"{type(exc).__name__}: {exc}"
            )
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=max(30.0, cfg.traffic_dashboard_refresh_seconds),
            )
        except asyncio.TimeoutError:
            continue


async def cleanup_legacy_traffic_dashboard_messages(
    app: Application, cache_path: Path
) -> None:
    rows = await asyncio.to_thread(pinned_dashboard_all_sync, cache_path)
    for row in rows:
        kind = str(row.get("kind") or "")
        if kind not in {"users", "nodes"}:
            continue
        chat_id = str(row.get("chat_id") or "")
        message_id = int(row.get("message_id") or 0)
        try:
            await app.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except BadRequest as exc:
            log.debug(
                "删除旧流量面板消息失败 chat=%s message=%s：%s",
                chat_id,
                message_id,
                exc,
            )
        await asyncio.to_thread(pinned_dashboard_delete_sync, cache_path, kind, chat_id)


cleanup_traffic_dashboard_messages = cleanup_legacy_traffic_dashboard_messages


def due_traffic_report_kinds(now: datetime | None = None) -> list[str]:
    current = now.astimezone(BEIJING_TZ) if now else beijing_now()
    # 默认 00:03 以后发送，给 00:00 附近最后一轮采样一点缓冲。
    if current.hour != 0 or current.minute < 3:
        return []
    kinds = ["daily"]
    if current.weekday() == 0:  # 周一发送上周周报
        kinds.append("weekly")
    if current.day == 1:
        kinds.append("monthly")
    return kinds


async def traffic_report_push_loop(
    app: Application,
    cfg: AppConfig,
    cache_path: Path,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        try:
            now = beijing_now()
            for kind in due_traffic_report_kinds(now):
                text, start_ts, end_ts = await asyncio.to_thread(
                    traffic_report_text_sync, cache_path, kind
                )
                chats = await asyncio.to_thread(
                    default_allowlist_notification_chats_sync, cache_path, cfg, kind
                )
                sent_chats: list[str] = []
                for chat_id in chats:
                    if await asyncio.to_thread(
                        traffic_report_already_sent_sync,
                        cache_path,
                        kind,
                        start_ts,
                        end_ts,
                        chat_id,
                    ):
                        continue
                    try:
                        await app.bot.send_message(
                            chat_id=chat_id, text=text, parse_mode="HTML"
                        )
                        await asyncio.to_thread(
                            mark_traffic_report_sent_sync,
                            cache_path,
                            kind,
                            start_ts,
                            end_ts,
                            chat_id,
                        )
                        sent_chats.append(chat_id)
                    except Exception as exc:
                        log.warning(
                            "发送 %s 失败 chat=%s：%s",
                            TRAFFIC_REPORT_KINDS[kind],
                            chat_id,
                            exc,
                        )
                if sent_chats:
                    log.info(
                        "%s 推送完成：%s 个聊天",
                        TRAFFIC_REPORT_KINDS[kind],
                        len(sent_chats),
                    )
        except Exception as exc:
            log.exception("流量报表推送任务异常：%s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=60)
        except asyncio.TimeoutError:
            continue


async def traffic_dashboard_refresh_loop(
    app: Application,
    cfg: AppConfig,
    cache_path: Path,
    stop_event: asyncio.Event,
) -> None:
    """Refresh pinned dashboard messages and delete unpinned interactive messages after 3 minutes."""
    while not stop_event.is_set():
        try:
            now_ts = int(datetime.now().timestamp())
            due_rows = await asyncio.to_thread(
                auto_delete_due_messages_sync, cache_path, now_ts - 180
            )
            for row in due_rows:
                chat_id = str(row.get("chat_id") or "")
                message_id = int(row.get("message_id") or 0)
                if not chat_id or not message_id:
                    continue
                try:
                    chat = await app.bot.get_chat(chat_id=chat_id)
                    pinned = getattr(chat, "pinned_message", None)
                    if pinned and getattr(pinned, "message_id", None) == message_id:
                        await asyncio.to_thread(
                            auto_delete_message_set_sync,
                            cache_path,
                            chat_id,
                            message_id,
                            True,
                        )
                        continue
                except Exception as exc:
                    log.debug(
                        "检查置顶消息失败 chat=%s message=%s：%s",
                        chat_id,
                        message_id,
                        exc,
                    )
                try:
                    await app.bot.delete_message(chat_id=chat_id, message_id=message_id)
                except BadRequest as exc:
                    log.debug(
                        "自动删除面板消息失败 chat=%s message=%s：%s",
                        chat_id,
                        message_id,
                        exc,
                    )
                await asyncio.to_thread(
                    pinned_dashboard_delete_message_sync,
                    cache_path,
                    chat_id,
                    message_id,
                )
                await asyncio.to_thread(
                    auto_delete_message_delete_sync, cache_path, chat_id, message_id
                )

            rows = await asyncio.to_thread(pinned_dashboard_all_sync, cache_path)
            for row in rows:
                kind = str(row.get("kind") or "")
                chat_id = str(row.get("chat_id") or "")
                message_id = int(row.get("message_id") or 0)
                is_pinned = bool(int(row.get("is_pinned") or 0))
                if not is_pinned:
                    continue
                if kind.startswith("ip_") or kind.startswith("iprange_"):
                    continue
                if (
                    kind == "combined"
                    or kind.startswith("preset_")
                    or kind.startswith("users_")
                    or kind.startswith("nodes_")
                    or kind.startswith("range_")
                ):
                    text = await asyncio.to_thread(
                        traffic_dashboard_text_from_kind_sync, cache_path, kind
                    )
                else:
                    continue
                try:
                    await app.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=text,
                        parse_mode="HTML",
                        reply_markup=traffic_dashboard_keyboard_static(kind, is_pinned),
                    )
                except BadRequest as exc:
                    if "message is not modified" in str(exc).lower():
                        continue
                    log.warning(
                        "刷新流量仪表盘消息失败，移除记录 kind=%s chat=%s msg=%s：%s",
                        kind,
                        chat_id,
                        message_id,
                        exc,
                    )
                    await asyncio.to_thread(
                        pinned_dashboard_delete_message_sync,
                        cache_path,
                        chat_id,
                        message_id,
                    )
        except Exception as exc:
            log.exception("流量仪表盘刷新任务异常：%s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=10)
        except asyncio.TimeoutError:
            continue


def initialize_cache_before_notifications_sync(
    cfg: AppConfig, cache_path: Path
) -> tuple[bool, str, bool, str, int, int, int]:
    """Collect active IPs and finish geo lookup before starting judgment/notification loops."""
    init_cache(cache_path)
    init_status_before = initialization_status_sync(
        cache_path, cfg.ip_geo_queries_per_minute
    )
    require_ack = bool(init_status_before.get("initializing"))
    if require_ack:
        initialization_mark_started_sync(cache_path, "startup")
    records = collect_redis_ip_records_sync(cfg.redis)
    if isinstance(records, str):
        log.warning("启动初始化缓存采集 Redis 失败：%s", records)
        return False, records, True, "", 0, 0, 0
    user_ids = upsert_cache_records(
        cache_path, records, cache_retention_days_sync(cache_path)
    )
    mysql_ok = True
    mysql_detail = ""
    try:
        upsert_cache_users(cache_path, cfg.mysql, user_ids)
    except MySQLError as exc:
        log.warning("启动初始化缓存采集 MySQL 用户信息失败：%s", exc)
        mysql_ok = False
        mysql_detail = f"{type(exc).__name__}: {exc}"
    geo_total, geo_success, geo_failed = backfill_geo_pending_until_complete(
        cache_path,
        queries_per_minute=max(1, int(cfg.ip_geo_queries_per_minute)),
    )
    log.info(
        "启动初始化缓存采集完成：Redis IP 记录 %s 条，用户 %s 个，归属地待处理 %s 个，成功 %s 个，失败 %s 个",
        len(records),
        len(user_ids),
        geo_total,
        geo_success,
        geo_failed,
    )
    if require_ack:
        initialization_mark_complete_sync(
            cache_path, len(records), geo_total, geo_success, geo_failed
        )
    return True, "", mysql_ok, mysql_detail, geo_total, geo_success, geo_failed

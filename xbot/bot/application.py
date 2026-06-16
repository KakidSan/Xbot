from __future__ import annotations


from ..common import (
    APP_DIR,
    Any,
    Application,
    BOT_COMMANDS,
    LOG_FORMAT,
    Path,
    Update,
    argparse,
    asyncio,
    log,
    logging,
    signal,
)
from ..config import AppConfig, build_config_from_env
from ..db.cache import (
    init_cache,
    resolve_cache_path,
)
from ..alerts import check_ip_alerts
from ..collector import (
    cache_collector_loop,
    cleanup_traffic_dashboard_messages,
    initialize_cache_before_notifications_sync,
    notify_collector_health_transition,
    traffic_dashboard_refresh_loop,
    traffic_report_push_loop,
    traffic_sampler_loop,
)
from ..updater import send_update_result_notice, version_update_check_loop
from .context import BotContext, BotRuntime
from .message_utils import edit_or_replace_status_any, reply_connection_status


def build_application(cfg: AppConfig, cache_path: Path) -> Application:
    bot_ctx = BotContext(cfg=cfg, cache_path=cache_path)
    app = Application.builder().token(bot_ctx.cfg.telegram.bot_token).build()

    from .messaging import build_runtime_services
    from .router import register_handlers

    services = build_runtime_services(app, cfg, cache_path)
    runtime = BotRuntime(
        bot_ctx=bot_ctx,
        edit_or_replace_status_any=edit_or_replace_status_any,
        reply_connection_status=reply_connection_status,
        **services,
    )
    register_handlers(app, runtime)
    return app


async def run_once(
    cfg: AppConfig,
    stop_event: asyncio.Event,
) -> None:
    cache_path = resolve_cache_path(cfg.cache_path, APP_DIR)
    bot_ctx = BotContext(cfg=cfg, cache_path=cache_path)
    init_cache(bot_ctx.cache_path)

    app = build_application(bot_ctx.cfg, bot_ctx.cache_path)
    collector_stop_event = asyncio.Event()
    collector_task: asyncio.Task[Any] | None = None
    sampler_stop_event = asyncio.Event()
    sampler_task: asyncio.Task[Any] | None = None
    dashboard_stop_event = asyncio.Event()
    dashboard_task: asyncio.Task[Any] | None = None
    report_stop_event = asyncio.Event()
    report_task: asyncio.Task[Any] | None = None
    version_stop_event = asyncio.Event()
    version_task: asyncio.Task[Any] | None = None

    await app.initialize()
    await app.bot.set_my_commands(BOT_COMMANDS)
    await app.start()
    if not app.updater:
        raise RuntimeError("Telegram updater 初始化失败")
    (
        redis_ok,
        redis_detail,
        mysql_ok,
        mysql_detail,
        geo_total,
        geo_success,
        geo_failed,
    ) = await asyncio.to_thread(
        initialize_cache_before_notifications_sync, cfg, cache_path
    )
    await notify_collector_health_transition(
        app,
        cfg,
        cache_path,
        "redis",
        redis_ok,
        redis_detail or "Redis 缓存采集已恢复成功。",
    )
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
            "IP-API 已恢复响应，启动初始化已完成 IP 归属地补全。",
        )
    elif geo_failed:
        await notify_collector_health_transition(
            app,
            cfg,
            cache_path,
            "ip_api",
            False,
            f"启动初始化 IP 归属地补全失败 {geo_failed} 个。",
        )
    await check_ip_alerts(app, cfg, cache_path)
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    await cleanup_traffic_dashboard_messages(app, bot_ctx.cache_path)
    collector_task = asyncio.create_task(
        cache_collector_loop(app, bot_ctx.cfg, bot_ctx.cache_path, collector_stop_event)
    )
    sampler_task = asyncio.create_task(
        traffic_sampler_loop(app, bot_ctx.cfg, bot_ctx.cache_path, sampler_stop_event)
    )
    dashboard_task = asyncio.create_task(
        traffic_dashboard_refresh_loop(
            app, bot_ctx.cfg, bot_ctx.cache_path, dashboard_stop_event
        )
    )
    report_task = asyncio.create_task(
        traffic_report_push_loop(app, bot_ctx.cache_path, report_stop_event)
    )
    version_task = asyncio.create_task(
        version_update_check_loop(
            app, bot_ctx.cfg, bot_ctx.cache_path, version_stop_event
        )
    )
    await send_update_result_notice(app)
    log.info("Telegram Bot 已启动，缓存文件：%s", bot_ctx.cache_path)

    try:
        await stop_event.wait()
        return None
    finally:
        log.info("正在停止 Telegram Bot 和缓存采集任务...")
        collector_stop_event.set()
        sampler_stop_event.set()
        dashboard_stop_event.set()
        report_stop_event.set()
        version_stop_event.set()
        if collector_task:
            try:
                await asyncio.wait_for(collector_task, timeout=10)
            except asyncio.TimeoutError:
                collector_task.cancel()
        if sampler_task:
            try:
                await asyncio.wait_for(sampler_task, timeout=10)
            except asyncio.TimeoutError:
                sampler_task.cancel()
        if dashboard_task:
            try:
                await asyncio.wait_for(dashboard_task, timeout=10)
            except asyncio.TimeoutError:
                dashboard_task.cancel()
        if report_task:
            try:
                await asyncio.wait_for(report_task, timeout=10)
            except asyncio.TimeoutError:
                report_task.cancel()
        if version_task:
            try:
                await asyncio.wait_for(version_task, timeout=10)
            except asyncio.TimeoutError:
                version_task.cancel()
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        log.info("Telegram Bot 已停止")


async def serve() -> None:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    cfg = build_config_from_env()

    while not stop_event.is_set():
        try:
            await run_once(cfg, stop_event)
            break
        except Exception as exc:
            log.exception("服务运行异常：%s", exc)
            if stop_event.is_set():
                break
            log.info("5 秒后重试启动")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=5)
                break
            except asyncio.TimeoutError:
                cfg = build_config_from_env()
                continue


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Xbot Telegram Monitor Bot")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    # 避免 httpx 在日志中输出 Telegram Bot Token 所在的完整请求 URL。
    logging.getLogger("httpx").setLevel(logging.WARNING)

    parse_args()
    asyncio.run(serve())

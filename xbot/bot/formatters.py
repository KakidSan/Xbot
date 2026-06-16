from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import AppConfig

from ..common import (
    Any,
    COLLECTOR_HEALTH_SERVICES,
    IP_ALERT_DEFAULT_CITY_THRESHOLD,
    PROCESS_STARTED_AT,
    Path,
    TRAFFIC_REPORT_KINDS,
    Update,
    alert_period_label,
    datetime,
    html,
    parse_ip_kind,
    re,
    sqlite3,
    traffic_report_window,
)
from ..db.cache import (
    alert_global_period_sync,
    alert_global_threshold_sync,
    alert_setting_label,
    alert_user_setting_sync,
    cache_connect,
    earliest_cache_collect_at_sync,
    earliest_traffic_sample_at_sync,
    format_duration,
    format_timestamp,
    get_cache_counts_sync,
    get_collector_state_sync,
    get_stats_floor_ts_sync,
    init_cache,
    list_user_ips_from_cache_sync,
    query_traffic_deltas_range_from_cache_sync,
    traffic_base_kind,
    traffic_dimension_from_kind,
    traffic_range_kind_from_cache_sync,
    traffic_sample_gap_warning_for_range_sync,
)
from ..db.mysql import connection_check_lines_sync
from ..geo import cache_geo_status_sync, estimate_geo_wait_seconds, row_value


def user_display(update: Update) -> str:
    user = update.effective_user
    if not user:
        return "unknown"
    name = user.full_name or user.username or str(user.id)
    return f"{name} ({user.id})"


def format_traffic_alert(row: dict[str, Any], recovered: bool = False) -> str:
    title = "✅ <b>流量异常恢复</b>" if recovered else "🚨 <b>流量异常告警</b>"
    label = render_user_label(int(row["user_id"]), str(row.get("name") or ""))
    period_label = str(row.get("period_label") or alert_period_label(row.get("period")))
    rule_type = str(row.get("rule_type") or "默认规则")
    rule_line = f"当前适用：{rule_type} (<b>{period_label} / {format_bytes(int(row['threshold']))}</b>)"
    if recovered:
        return "\n".join(
            [
                title,
                "────────────",
                f"用户：{label}",
                rule_line,
                f"当前{period_label}用量：{format_bytes(int(row['total']))}",
            ]
        )
    return "\n".join(
        [
            title,
            "────────────",
            f"用户：{label}",
            rule_line,
            f"{period_label}用量：<b>{format_bytes(int(row['total']))}</b>",
            "",
            "连续超出规则期间只会首次通知；恢复到规则内后会推送恢复。",
        ]
    )


def format_ip_alert(
    row: dict[str, Any], recovered: bool = False, previous_city_count: int | None = None
) -> str:
    if recovered:
        title = "✅ <b>异地登录恢复</b>"
    elif previous_city_count is not None:
        trend = (
            "📈" if int(row.get("city_count") or 0) > int(previous_city_count) else "📉"
        )
        title = f"{trend} <b>异地登录变化</b>"
    else:
        title = "🚨 <b>异地登录</b>"
    label = render_user_label(int(row["user_id"]), str(row.get("name") or ""))
    period_label = str(row.get("period_label") or alert_period_label(row.get("period")))
    cities = "、".join(html.escape(c) for c in row.get("cities", []) if c) or "未知"
    rule_type = str(row.get("rule_type") or "默认规则")
    rule_line = f"当前适用：{rule_type} (<b>{period_label} / {int(row.get('threshold') or IP_ALERT_DEFAULT_CITY_THRESHOLD)} 个城市</b>)"
    city_count = int(row.get("city_count") or 0)
    change_line = (
        f"城市数变化：{int(previous_city_count)} → {city_count}"
        if previous_city_count is not None
        else ""
    )
    if recovered:
        lines = [title, "────────────", f"用户：{label}", rule_line]
        if change_line:
            lines.append(change_line)
        lines.extend([f"{period_label}城市数：{city_count}", f"涉及城市：{cities}"])
        return "\n".join(lines)
    lines = [title, "────────────", f"用户：{label}", rule_line]
    if change_line:
        lines.append(change_line)
        lines.append("状态：仍超过阈值")
    lines.extend([f"{period_label}城市数：<b>{city_count}</b>", f"涉及城市：{cities}"])
    if previous_city_count is None:
        lines.extend(
            [
                "",
                "基础版只在首次超出和恢复时提醒；高级版会在超出阈值后的城市数量变化时再次提醒。",
            ]
        )
    return "\n".join(lines)


def format_geo_pending_text(pending_count: int, queries_per_minute: int) -> str:
    if pending_count <= 0:
        return "待补全 0 个"
    wait_seconds = estimate_geo_wait_seconds(pending_count, queries_per_minute)
    return f"待补全 {pending_count} 个，预计约 {format_duration(wait_seconds)}"


def format_bytes(value: int | float | None) -> str:
    size = float(value or 0)
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    for unit in units:
        if abs(size) < 1024 or unit == units[-1]:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"


def format_age(ts: int | None) -> str:
    if not ts:
        return "未知"
    seconds = int(datetime.now().timestamp()) - int(ts)
    if seconds <= 5:
        return "刚刚"
    return f"{format_duration(seconds)}前"


def format_health_age(ts: int | None) -> str:
    if not ts:
        return "未知"
    seconds = max(0, int(datetime.now().timestamp()) - int(ts))
    if seconds >= 86400:
        return f"{max(1, seconds // 86400)}天前"
    return format_age(ts)


def format_age_with_time(ts: int | None) -> str:
    if not ts:
        return "未知"
    return f"{format_health_age(ts)} ({format_timestamp(ts)})"


def bot_health_overview_text_sync(
    cfg: AppConfig, cache_path: Path, admin_view: bool = True
) -> str:
    counts = get_cache_counts_sync(cache_path)
    geo_status = cache_geo_status_sync(cache_path)
    geo_pending_text = format_geo_pending_text(
        geo_status["geo_pending"], cfg.ip_geo_queries_per_minute
    )
    cache_size = cache_path.stat().st_size if cache_path.exists() else 0
    uptime_seconds = int((datetime.now() - PROCESS_STARTED_AT).total_seconds())
    collect_state = get_collector_state_sync(cache_path, "last_collect_at")
    traffic_state = get_collector_state_sync(cache_path, "last_traffic_sample_at")
    collect_ts = collect_state[1] if collect_state else None
    traffic_ts = traffic_state[1] if traffic_state else None
    first_collect_state = get_collector_state_sync(cache_path, "first_collect_at")
    first_collect_at = (
        first_collect_state[1]
        if first_collect_state
        else earliest_cache_collect_at_sync(cache_path)
    )
    first_traffic_at = earliest_traffic_sample_at_sync(cache_path)
    connection_lines, _, _, sqlite_ok = connection_check_lines_sync(cfg, cache_path)
    if not admin_view:
        connection_lines = [
            redact_sensitive_text_for_non_admin(line).replace("端口", "服务")
            for line in connection_lines
        ]

    lines = [
        "🩺 <b>健康检查</b>",
        "────────────",
        "🤖 <b>服务启动状态</b>",
        f"启动时间：{PROCESS_STARTED_AT.strftime('%Y-%m-%d %H:%M:%S')}",
        f"运行时长：{format_duration(uptime_seconds)}",
        "",
        "🔗 <b>连接检查</b>",
        *connection_lines,
        "",
        "📦 <b>缓存采集</b>",
        f"缓存文件：{format_bytes(cache_size)}",
        "",
        f"首次缓存采集：{format_age_with_time(first_collect_at)}",
        f"最后缓存采集：{format_age_with_time(collect_ts)}",
        "",
        f"IP 缓存：{counts['active_ips']} 个",
        f"IP 归属地缓存：{geo_status['geo_total']} 个 ({geo_pending_text})",
        f"用户信息缓存：{counts['users']} 个",
        "",
        f"首次流量采样：{format_age_with_time(first_traffic_at)}",
        f"最后流量采样：{format_age_with_time(traffic_ts)}",
    ]
    if not sqlite_ok:
        lines.append("\n⚠️ SQLite 异常时，缓存统计可能不完整。")
    text = "\n".join(lines)
    if not admin_view:
        text += "\n\n敏感连接信息已隐藏，仅管理员可查看完整详情。"
    return text


def cached_user_display_name(row: sqlite3.Row | None, xboard_user_id: int) -> str:
    display_name = str(row["display_name"] or "").strip() if row else ""
    return display_name or f"用户{xboard_user_id}"


def cached_user_name_by_id(cache_path: Path, xboard_user_id: int) -> str:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        row = conn.execute(
            "SELECT display_name FROM users WHERE user_id = ?", (xboard_user_id,)
        ).fetchone()
    return cached_user_display_name(row, xboard_user_id)


def render_user_label(user_id_value: Any, display_name_value: Any = None) -> str:
    xboard_user_id = int(user_id_value or 0)
    display_name = str(display_name_value or "").strip()
    if display_name and display_name != f"用户{xboard_user_id}":
        return (
            f"{html.escape(display_name)} (user_id: {html.escape(str(xboard_user_id))})"
        )
    return f"用户 {html.escape(str(xboard_user_id))}"


def bot_status_text_sync(cfg: AppConfig, cache_path: Path) -> str:
    counts = get_cache_counts_sync(cache_path)
    collect_state = get_collector_state_sync(cache_path, "last_collect_at")
    traffic_state = get_collector_state_sync(cache_path, "last_traffic_sample_at")
    cleared_state = get_collector_state_sync(
        cache_path, "last_active_ip_records_cleared_at"
    )
    stats_floor = get_stats_floor_ts_sync(cache_path)
    geo_status = cache_geo_status_sync(cache_path)
    cache_size = cache_path.stat().st_size if cache_path.exists() else 0
    uptime_seconds = int((datetime.now() - PROCESS_STARTED_AT).total_seconds())

    lines = [
        "🟢 <b>Bot 运行状态</b>",
        "────────────",
        f"运行时长：{format_duration(uptime_seconds)}",
        f"进程启动：{PROCESS_STARTED_AT.strftime('%Y-%m-%d %H:%M:%S')}",
        f"缓存文件：{format_bytes(cache_size)}",
        "",
        f"最后 Redis 缓存采集：{format_age(collect_state[1] if collect_state else None)}",
        f"最后流量采样：{format_age(traffic_state[1] if traffic_state else None)}",
        f"最近清空用户 IP 记录：{format_timestamp(cleared_state[1]) if cleared_state else '未清空'}",
        f"统计起始点：{format_timestamp(stats_floor) if stats_floor else '未手动重置'}",
        f"采集间隔：{format_duration(int(cfg.collector_interval_seconds))}",
        "",
        f"IP 缓存：{counts['active_ips']} 个",
        f"用户缓存：{counts['users']} 个",
        f"IP 归属地缓存：{geo_status['geo_total']} 个 (待补全 {geo_status['geo_pending']} 个)",
        f"流量增量样本：{counts['traffic_samples']} 条",
        f"流量面板消息：{counts['pinned_dashboards']} 条",
    ]
    return "\n".join(lines)


def geo_text(row: sqlite3.Row) -> str:
    raw_parts = [
        str(row_value(row, name) or "").strip()
        for name in ("country", "region", "city", "district", "isp")
    ]
    parts: list[str] = []
    for part in raw_parts:
        if part and part not in parts:
            parts.append(part)
    return "，".join(parts)


def safe_autolink_text(value: str) -> str:
    """Prevent Telegram from auto-linking domain-like fragments in plain text."""
    # Telegram may auto-link strings such as Alibaba.com even inside HTML messages.
    # A zero-width space after dots keeps the text readable but breaks autolink detection.
    return value.replace(".", ".\u200b")


def ip_monitor_text_from_kind_sync(cache_path: Path, kind: str) -> str:
    parsed = parse_ip_kind(kind)
    if not parsed:
        return "请求无效，请重新进入。"
    label, start_ts, end_ts = parsed
    return list_user_ips_from_cache_sync(cache_path, label, None, start_ts, end_ts)


def traffic_report_text_sync(cache_path: Path, kind: str) -> tuple[str, int, int]:
    start_ts, end_ts, label = traffic_report_window(kind)
    title = f"📰 {TRAFFIC_REPORT_KINDS[kind]}"
    text = render_traffic_dashboard_text(title, label, start_ts, end_ts, cache_path)
    return text, start_ts, end_ts


def render_traffic_dashboard_text(
    title: str,
    period_label: str,
    start_ts: int,
    end_ts: int,
    cache_path: Path,
    limit: int = 10,
    dimension: str = "combined",
) -> str:
    safe_limit = max(1, min(limit, 50))
    dimension = dimension if dimension in {"combined", "users", "nodes"} else "combined"
    grand_total, user_rows, node_rows, first_sample = (
        query_traffic_deltas_range_from_cache_sync(
            cache_path, start_ts, end_ts, safe_limit, dimension
        )
    )
    gap_warning = traffic_sample_gap_warning_for_range_sync(
        cache_path, start_ts, end_ts, period_label
    )
    lines = [
        f"<b>{title}</b>",
        "",
        f"🌊 总流量：{format_bytes(grand_total)}",
        f"🕒 最后更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "────────────",
        "",
    ]
    if first_sample:
        covered_start = max(start_ts, first_sample)
        covered_end = min(end_ts, int(datetime.now().timestamp()))
        covered_seconds = max(0, covered_end - covered_start)
        period_seconds = max(1, end_ts - start_ts)
        if covered_seconds < period_seconds:
            lines.extend(
                [
                    f"📡 采样覆盖：{format_duration(covered_seconds)}",
                    "⚠️ 当前统计周期内采样未覆盖完整窗口，统计可能存在偏差。",
                    "",
                ]
            )
    if gap_warning:
        lines.extend([gap_warning, ""])

    if dimension in {"combined", "users"}:
        lines.append(f"🏅 <b>用户流量 Top {len(user_rows)}</b>")
        if user_rows:
            for index, row in enumerate(user_rows, start=1):
                entity_id = row.get("entity_id")
                name = html.escape(str(row.get("name") or f"用户{entity_id}"))
                user_id = html.escape(str(entity_id or ""))
                lines.append(
                    f"{index}. {name} (user_id: {user_id})：{format_bytes(row.get('total'))}"
                )
        else:
            lines.append("暂无用户流量记录。")

    if dimension in {"combined", "nodes"}:
        if dimension == "combined":
            lines.extend(["", "────────────", ""])
        lines.append(f"🏅 <b>节点流量 Top {len(node_rows)}</b>")
        if node_rows:
            for index, row in enumerate(node_rows, start=1):
                name = html.escape(
                    safe_autolink_text(
                        str(row.get("name") or f"节点{row.get('entity_id')}")
                    )
                )
                lines.append(f"{index}. {name}：{format_bytes(row.get('total'))}")
        else:
            lines.append("暂无节点流量记录。")
    result = "\n".join(lines).strip()
    if len(result) > 3900:
        result = result[:3850].rstrip() + "\n\n……内容过长，已截断。"
    return result


def traffic_dashboard_text_from_kind_sync(cache_path: Path, kind: str) -> str:
    if kind.startswith("ip_") or kind.startswith("iprange_"):
        return ip_monitor_text_from_kind_sync(cache_path, kind)
    now_ts = int(datetime.now().timestamp())
    dimension = traffic_dimension_from_kind(kind)
    base_kind = traffic_base_kind(kind)
    presets = {
        "combined": ("近 24 小时", now_ts - 24 * 3600, now_ts),
        "preset_1h": ("近 1 小时", now_ts - 3600, now_ts),
        "preset_24h": ("近 24 小时", now_ts - 24 * 3600, now_ts),
        "preset_7d": ("近 7 天", now_ts - 7 * 24 * 3600, now_ts),
        "preset_30d": ("近 30 天", now_ts - 30 * 24 * 3600, now_ts),
    }
    if base_kind in presets:
        label, start_ts, end_ts = presets[base_kind]
        title = traffic_title_for_dimension(label, dimension)
        return render_traffic_dashboard_text(
            title, label, start_ts, end_ts, cache_path, dimension=dimension
        )
    if base_kind.startswith("range_"):
        range_kind = traffic_range_kind_from_cache_sync(cache_path, base_kind)
        if not range_kind:
            return "请求无效，请重新进入。"
        label = str(range_kind["label"])
        return render_traffic_dashboard_text(
            traffic_title_for_dimension(label, dimension),
            label,
            int(range_kind["start_ts"]),
            int(range_kind["end_ts"]),
            cache_path,
            dimension=dimension,
        )
    return "请求无效，请重新进入。"


def traffic_title_for_dimension(label: str, dimension: str) -> str:
    if dimension == "users":
        return f"📈 {label} 用户流量统计"
    if dimension == "nodes":
        return f"📈 {label} 节点流量统计"
    return f"📈 {label} 流量统计"


def format_collector_health_alert(
    service: str, recovered: bool, detail: str = "", admin_view: bool = True
) -> str:
    title = "✅ <b>采集异常恢复</b>" if recovered else "⚠️ <b>采集异常</b>"
    status_line = "状态：已恢复" if recovered else "状态：异常"
    lines = [
        title,
        "────────────",
        f"服务：{html.escape(COLLECTOR_HEALTH_SERVICES.get(service, service))}",
        status_line,
        f"时间：{format_timestamp(int(datetime.now().timestamp()))}",
    ]
    if detail:
        safe_detail = (
            detail if admin_view else redact_sensitive_text_for_non_admin(detail)
        )
        lines.extend(["", f"详情：{html.escape(safe_detail)[:500]}"])
    if not admin_view:
        lines.extend(["", "敏感连接信息已隐藏，仅管理员可查看完整详情。"])
    return "\n".join(lines)


def format_collector_gap_alert(
    previous_ts: int, current_ts: int, gap_seconds: int
) -> str:
    return "\n".join(
        [
            "✅ <b>采集异常恢复</b>",
            "────────────",
            "检测到 Bot 已恢复运行，但中断期间未能完成流量采样。",
            "",
            f"中断开始：{format_timestamp(previous_ts)}",
            f"恢复采样：{format_timestamp(current_ts)}",
            f"影响时长：{format_duration(gap_seconds)}",
        ]
    )


def redact_sensitive_text_for_non_admin(text: str) -> str:
    """Remove host/IP/port-like details from messages shown to non-admin Telegram users."""
    if not text:
        return text
    redacted = text
    redacted = re.sub(r"https?://[^\s<]+", "[已隐藏URL]", redacted)
    redacted = re.sub(
        r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?", "[已隐藏IP]", redacted
    )
    redacted = re.sub(r"\[[0-9a-fA-F:]+\](?::\d{1,5})?", "[已隐藏IP]", redacted)
    redacted = re.sub(
        r"\b[0-9a-fA-F]{0,4}:[0-9a-fA-F:]{2,}(?::\d{1,5})?\b", "[已隐藏IP]", redacted
    )
    redacted = re.sub(
        r"(?i)\b([a-z0-9-]+\.)+[a-z]{2,}(?::\d{1,5})?\b", "[已隐藏主机]", redacted
    )
    redacted = re.sub(
        r"(?i)\b(host|hostname|server|addr|address|endpoint)\s*[:=]\s*[^\s，。；;]+",
        r"\1=[已隐藏]",
        redacted,
    )
    redacted = re.sub(r"(?i)\b(port)\s*[:=]\s*\d{1,5}\b", r"\1=[已隐藏]", redacted)
    redacted = re.sub(r"端口\s*[:：]?\s*\d{1,5}", "端口：[已隐藏]", redacted)
    return redacted


def alert_global_setting_text_sync(cache_path: Path, alert_type: str) -> str:
    threshold = alert_global_threshold_sync(cache_path, alert_type)
    period = alert_global_period_sync(cache_path, alert_type)
    period_label = alert_period_label(period)
    if alert_type == "traffic":
        threshold_text = format_bytes(threshold)
        title = "🎚 异常告警<b>默认规则</b>"
        suffix = ""
    else:
        threshold_text = f"{threshold} 个城市"
        title = "🎚 异地登录<b>默认规则</b>"
        suffix = ""
    return (
        "\n".join(
            [
                title,
                "────────────",
                f"当前规则：<b>{period_label} / {threshold_text}</b>",
            ]
        )
        + suffix
    )


def alert_user_setting_text_sync(
    cache_path: Path, alert_type: str, xboard_user_id: int
) -> str:
    setting = alert_user_setting_sync(cache_path, xboard_user_id)
    name = cached_user_name_by_id(cache_path, xboard_user_id) or f"用户{xboard_user_id}"
    if alert_type == "traffic":
        whitelisted = bool(int(setting.get("traffic_whitelist") or 0))
        effective = alert_setting_label(setting, "traffic", cache_path)
        uses_independent = (
            setting.get("traffic_threshold_bytes") is not None
            or setting.get("traffic_period") is not None
        )
        if whitelisted:
            current_line = "当前适用：白名单 (不提醒)"
        else:
            current_line = f"当前适用：{'独立规则' if uses_independent else '默认规则'} (<b>{effective}</b>)"
        lines = [
            "🚨 <b>用户流量告警设置</b>",
            "────────────",
            f"用户：{render_user_label(xboard_user_id, name)}",
            current_line,
        ]
    else:
        whitelisted = bool(int(setting.get("ip_whitelist") or 0))
        effective = alert_setting_label(setting, "ip", cache_path)
        uses_independent = (
            setting.get("ip_city_threshold") is not None
            or setting.get("ip_period") is not None
        )
        if whitelisted:
            current_line = "当前适用：白名单 (不提醒)"
        else:
            current_line = f"当前适用：{'独立规则' if uses_independent else '默认规则'} (<b>{effective}</b>)"
        lines = [
            "🚨 <b>用户异地告警设置</b>",
            "────────────",
            f"用户：{render_user_label(xboard_user_id, name)}",
            current_line,
        ]
    return "\n".join(lines)


def alert_summary_sync(cache_path: Path, alert_type: str) -> str:
    init_cache(cache_path)
    if alert_type == "traffic":
        title = "🚨 <b>异常告警</b>"
        default_line = f"默认规则：{alert_period_label(alert_global_period_sync(cache_path, 'traffic'))} / <b>{format_bytes(alert_global_threshold_sync(cache_path, 'traffic'))}</b>"
    else:
        title = "🚨 <b>异地登录</b>"
        default_line = f"默认规则：{alert_period_label(alert_global_period_sync(cache_path, 'ip'))} / <b>{alert_global_threshold_sync(cache_path, 'ip')} 个城市</b>"
    lines = [title, "────────────", default_line]
    return "\n".join(lines)


def notification_ip_alert_mode_label(mode: str) -> str:
    return {"off": "关闭", "basic": "基础", "advanced": "高级"}.get(mode, "基础")

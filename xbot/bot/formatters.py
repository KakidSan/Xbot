from __future__ import annotations

from ..common import *
from ..db.cache import *
from ..db.redis import *
from ..db.mysql import *
from ..geo import *

def user_display(update: Update) -> str:
    user = update.effective_user
    if not user:
        return "unknown"
    name = user.full_name or user.username or str(user.id)
    return f"{name} ({user.id})"

def html_code(value: Any) -> str:
    return f"<code>{html.escape(str(value))}</code>"

def format_traffic_alert(row: dict[str, Any], recovered: bool = False) -> str:
    title = "✅ <b>流量异常恢复</b>" if recovered else "🚨 <b>流量异常告警</b>"
    label = render_user_label(int(row["user_id"]), str(row.get("name") or ""))
    period_label = str(row.get("period_label") or alert_period_label(row.get("period")))
    rule_type = str(row.get("rule_type") or "默认规则")
    rule_line = f"当前适用：{rule_type} (<b>{period_label} / {format_bytes(int(row['threshold']))}</b>)"
    if recovered:
        return "\n".join([title, "────────────", f"用户：{label}", rule_line, f"当前{period_label}用量：{format_bytes(int(row['total']))}"])
    return "\n".join([title, "────────────", f"用户：{label}", rule_line, f"{period_label}用量：<b>{format_bytes(int(row['total']))}</b>", "", "连续超出规则期间只会首次通知；恢复到规则内后会推送恢复。"] )

def format_ip_alert(row: dict[str, Any], recovered: bool = False, previous_city_count: int | None = None) -> str:
    if recovered:
        title = "✅ <b>异地登录恢复</b>"
    elif previous_city_count is not None:
        trend = "📈" if int(row.get("city_count") or 0) > int(previous_city_count) else "📉"
        title = f"{trend} <b>异地登录变化</b>"
    else:
        title = "🚨 <b>异地登录</b>"
    label = render_user_label(int(row["user_id"]), str(row.get("name") or ""))
    period_label = str(row.get("period_label") or alert_period_label(row.get("period")))
    cities = "、".join(html.escape(c) for c in row.get("cities", []) if c) or "未知"
    rule_type = str(row.get("rule_type") or "默认规则")
    rule_line = f"当前适用：{rule_type} (<b>{period_label} / {int(row.get('threshold') or IP_ALERT_DEFAULT_CITY_THRESHOLD)} 个城市</b>)"
    city_count = int(row.get("city_count") or 0)
    change_line = f"城市数变化：{int(previous_city_count)} → {city_count}" if previous_city_count is not None else ""
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
        lines.extend(["", "基础版只在首次超出和恢复时提醒；高级版会在超出阈值后的城市数量变化时再次提醒。"])
    return "\n".join(lines)

def format_geo_pending_text(pending_count: int, queries_per_minute: int) -> str:
    if pending_count <= 0:
        return "待补全 0 个"
    wait_seconds = estimate_geo_wait_seconds(pending_count, queries_per_minute)
    return f"待补全 {pending_count} 个，预计约 {format_duration(wait_seconds)}"

def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} 小时 {minutes} 分钟"
    if minutes:
        return f"{minutes} 分钟 {sec} 秒" if sec else f"{minutes} 分钟"
    return f"{sec} 秒"

def format_bytes(value: int | float | None) -> str:
    size = float(value or 0)
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    for unit in units:
        if abs(size) < 1024 or unit == units[-1]:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"

def geo_area_rule_label(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return value
    if "|" in value:
        return " / ".join(part for part in value.split("|") if part)
    if ":" in value:
        return value.split(":")[-1] or value
    return value

def format_timestamp(ts: int | None) -> str:
    if not ts:
        return "未知"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

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

def compact_connection_error_lines(result: str) -> list[str]:
    raw_lines = [line.strip() for line in result.splitlines() if line.strip()]
    summary_candidates = [line for line in raw_lines[1:] if line.startswith("❌") and "错误类型" not in line and "错误代码" not in line]
    lines: list[str] = []
    if summary_candidates:
        lines.append(f"　{summary_candidates[0]}")
    for line in raw_lines:
        if "错误类型" in line or "错误代码" in line:
            lines.append(f"　{line}")
    reason_candidates = [line for line in summary_candidates[1:] if "可能" not in line and "常见原因" not in line]
    if reason_candidates:
        lines.append(f"　{reason_candidates[-1]}")
    return lines

def bot_health_overview_text_sync(cfg: AppConfig, cache_path: Path, admin_view: bool = True) -> str:
    counts = get_cache_counts_sync(cache_path)
    geo_status = cache_geo_status_sync(cache_path)
    geo_pending_text = format_geo_pending_text(geo_status["geo_pending"], cfg.ip_geo_queries_per_minute)
    cache_size = cache_path.stat().st_size if cache_path.exists() else 0
    uptime_seconds = int((datetime.now() - PROCESS_STARTED_AT).total_seconds())
    collect_state = get_collector_state_sync(cache_path, "last_collect_at")
    traffic_state = get_collector_state_sync(cache_path, "last_traffic_sample_at")
    first_collect_state = get_collector_state_sync(cache_path, "first_collect_at")
    first_collect_at = first_collect_state[1] if first_collect_state else earliest_cache_collect_at_sync(cache_path)
    first_traffic_at = earliest_traffic_sample_at_sync(cache_path)
    connection_lines, _, _, sqlite_ok = connection_check_lines_sync(cfg, cache_path)
    if not admin_view:
        connection_lines = [redact_sensitive_text_for_non_admin(line).replace("端口", "服务") for line in connection_lines]

    now_ts = int(datetime.now().timestamp())
    collect_ts = collect_state[1] if collect_state else None
    traffic_ts = traffic_state[1] if traffic_state else None
    collect_lag = now_ts - collect_ts if collect_ts else None
    traffic_lag = now_ts - traffic_ts if traffic_ts else None
    collect_ok = collect_lag is not None and collect_lag <= max(180, int(cfg.collector_interval_seconds * 3))
    traffic_ok = traffic_lag is not None and traffic_lag <= 180

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

def cached_user_button_label(row: sqlite3.Row | None, xboard_user_id: int) -> str:
    display_name = str(row["display_name"] or "").strip() if row else ""
    if display_name:
        return f"{display_name} (user_id: {xboard_user_id})"
    return f"用户 {xboard_user_id}"

def render_cached_user_label(row: sqlite3.Row | None, xboard_user_id: int) -> str:
    display_name = str(row["display_name"] or "").strip() if row else ""
    if display_name:
        return f"{html.escape(display_name)} (user_id: {html.escape(str(xboard_user_id))})"
    return f"用户 {html.escape(str(xboard_user_id))}"

def cached_user_name_by_id(cache_path: Path, xboard_user_id: int) -> str:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        row = conn.execute("SELECT display_name FROM users WHERE user_id = ?", (xboard_user_id,)).fetchone()
    return cached_user_display_name(row, xboard_user_id)

def render_user_label(user_id_value: Any, display_name_value: Any = None) -> str:
    xboard_user_id = int(user_id_value or 0)
    display_name = str(display_name_value or "").strip()
    if display_name and display_name != f"用户{xboard_user_id}":
        return f"{html.escape(display_name)} (user_id: {html.escape(str(xboard_user_id))})"
    return f"用户 {html.escape(str(xboard_user_id))}"

def bot_status_text_sync(cfg: AppConfig, cache_path: Path) -> str:
    counts = get_cache_counts_sync(cache_path)
    collect_state = get_collector_state_sync(cache_path, "last_collect_at")
    traffic_state = get_collector_state_sync(cache_path, "last_traffic_sample_at")
    cleared_state = get_collector_state_sync(cache_path, "last_active_ip_records_cleared_at")
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
    raw_parts = [str(row_value(row, name) or "").strip() for name in ("country", "region", "city", "district", "isp")]
    parts: list[str] = []
    for part in raw_parts:
        if part and part not in parts:
            parts.append(part)
    return "，".join(parts)

def geo_location_text(row: sqlite3.Row) -> str:
    raw_parts = [str(row_value(row, name) or "").strip() for name in ("country", "region", "city", "district")]
    parts: list[str] = []
    for part in raw_parts:
        if part and part not in parts:
            parts.append(part)
    return "，".join(parts)

def asn_text(row: sqlite3.Row) -> str:
    raw = raw_geo_data(row)
    return asn_label_from_raw(raw) or str(row["isp"] or "").strip() or "待查询"

def safe_autolink_text(value: str) -> str:
    """Prevent Telegram from auto-linking domain-like fragments in plain text."""
    # Telegram may auto-link strings such as Alibaba.com even inside HTML messages.
    # A zero-width space after dots keeps the text readable but breaks autolink detection.
    return value.replace(".", ".\u200b")

def geo_area_key(row: sqlite3.Row) -> str | None:
    """Return a city-level area key for de-duplicated active area counting."""
    stat_area_key = str(row_value(row, "stat_area_key") or "").strip()
    if stat_area_key:
        return stat_area_key
    raw = raw_geo_data(row)
    if raw:
        stat_area = build_geo_stat_area(raw)
        if stat_area.get("key"):
            return stat_area["key"]
    country = str(row_value(row, "country") or "").strip()
    region = str(row_value(row, "region") or "").strip()
    city = str(row_value(row, "city") or "").strip()
    if city:
        return "|".join(part for part in (country, region, city) if part)
    if region:
        return "|".join(part for part in (country, region) if part)
    if country:
        return country
    return None

def count_geo_areas(rows: list[sqlite3.Row]) -> int:
    return len({key for row in rows if (key := geo_area_key(row))})

def ip_range_kind(start_ts: int, end_ts: int) -> str:
    return f"iprange_{start_ts}_{end_ts}"

def parse_ip_kind(kind: str) -> tuple[str, int | None, int | None] | None:
    now_ts = int(datetime.now().timestamp())
    if kind.startswith("ip_"):
        key = kind.removeprefix("ip_")
        if key not in IP_PERIODS:
            return None
        label, seconds = IP_PERIODS[key]
        return label, now_ts - seconds, now_ts
    match = re.fullmatch(r"iprange_(\d+)_(\d+)", kind)
    if match:
        start_ts = int(match.group(1))
        end_ts = int(match.group(2))
        return "自定区间", start_ts, end_ts
    return None

def ip_monitor_text_from_kind_sync(cache_path: Path, kind: str) -> str:
    parsed = parse_ip_kind(kind)
    if not parsed:
        return "请求无效，请重新进入。"
    label, start_ts, end_ts = parsed
    return list_user_ips_from_cache_sync(cache_path, label, None, start_ts, end_ts)

def render_cached_ip_bucket(title: str, rows: list[sqlite3.Row], shown_ips: set[str], cutoff_ts: int) -> list[str]:
    bucket_rows: list[sqlite3.Row] = []
    for row in rows:
        ip = str(row["ip"])
        if ip in shown_ips:
            continue
        if int(row["last_seen_at"]) < cutoff_ts:
            continue
        bucket_rows.append(row)
        shown_ips.add(ip)

    lines = [f"🌐 <b>{title}活跃 IP {len(bucket_rows)} 个，活跃地区 {count_geo_areas(bucket_rows)} 个</b>", ""]
    if not bucket_rows:
        return lines[:-1]
    for index, row in enumerate(bucket_rows, start=1):
        ip = str(row["ip"])
        location = geo_location_text(row) or "待查询"
        safe_location = html.escape(safe_autolink_text(location))
        safe_asn = html.escape(safe_autolink_text(asn_text(row)))
        lines.extend([
            f"{index}. <code>{html.escape(ip)}/24</code>",
            f"📍地区：{safe_location}",
            f"🏷️ ASN：{safe_asn}",
            f"🕒最后活跃时间：{html.escape(format_timestamp(int(row['last_seen_at'])))}",
            "────────────",
        ])
    if lines[-1] == "────────────":
        lines.pop()
    return lines

def render_user_ip_rows_page(
    user_label: str,
    label: str,
    rows: list[sqlite3.Row],
    page: int = 0,
    page_size: int = 10,
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> str:
    safe_page_size = max(1, min(page_size, 50))
    total = len(rows)
    total_pages = max(1, (total + safe_page_size - 1) // safe_page_size)
    page = min(max(page, 0), total_pages - 1)
    start = page * safe_page_size
    page_rows = rows[start:start + safe_page_size]
    if start_ts is not None and end_ts is not None and label == "自定区间":
        lines = [
            f"{user_label}",
            f"时间区间：{datetime.fromtimestamp(start_ts).strftime('%Y-%m-%d %H:%M')} - {datetime.fromtimestamp(end_ts).strftime('%Y-%m-%d %H:%M')}",
            f"活跃 IP {total} 个，活跃地区 {count_geo_areas(rows)} 个",
            "────────────",
            "",
        ]
    else:
        lines = [
            f"{user_label} {label}活跃 IP {total} 个，活跃地区 {count_geo_areas(rows)} 个",
            "────────────",
            "",
        ]
    if not page_rows:
        lines.append("暂无符合条件的活跃 IP。")
        return "\n".join(lines).strip()
    for index, row in enumerate(page_rows, start=start + 1):
        ip = str(row["ip"])
        location = geo_location_text(row) or "待查询"
        safe_location = html.escape(safe_autolink_text(location))
        safe_asn = html.escape(safe_autolink_text(asn_text(row)))
        lines.extend([
            f"{index}. <code>{html.escape(ip)}/24</code>",
            f"📍地区：{safe_location}",
            f"🏷️ ASN：{safe_asn}",
            f"🕒最后活跃时间：{html.escape(format_timestamp(int(row['last_seen_at'])))}",
            "────────────",
        ])
    if lines[-1] == "────────────":
        lines.pop()
    return "\n".join(lines).strip()

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
    grand_total, user_rows, node_rows, first_sample = query_traffic_deltas_range_from_cache_sync(cache_path, start_ts, end_ts, safe_limit, dimension)
    gap_warning = traffic_sample_gap_warning_for_range_sync(cache_path, start_ts, end_ts, period_label)
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
            lines.extend([
                f"📡 采样覆盖：{format_duration(covered_seconds)}",
                "⚠️ 当前统计周期内采样未覆盖完整窗口，统计可能存在偏差。",
                "",
            ])
    if gap_warning:
        lines.extend([gap_warning, ""])

    if dimension in {"combined", "users"}:
        lines.append(f"🏅 <b>用户流量 Top {len(user_rows)}</b>")
        if user_rows:
            for index, row in enumerate(user_rows, start=1):
                entity_id = row.get("entity_id")
                name = html.escape(str(row.get("name") or f"用户{entity_id}"))
                user_id = html.escape(str(entity_id or ""))
                lines.append(f"{index}. {name} (user_id: {user_id})：{format_bytes(row.get('total'))}")
        else:
            lines.append("暂无用户流量记录。")

    if dimension in {"combined", "nodes"}:
        if dimension == "combined":
            lines.extend(["", "────────────", ""])
        lines.append(f"🏅 <b>节点流量 Top {len(node_rows)}</b>")
        if node_rows:
            for index, row in enumerate(node_rows, start=1):
                name = html.escape(safe_autolink_text(str(row.get("name") or f"节点{row.get('entity_id')}")))
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
        return render_traffic_dashboard_text(title, label, start_ts, end_ts, cache_path, dimension=dimension)
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

def format_collector_health_alert(service: str, recovered: bool, detail: str = "", admin_view: bool = True) -> str:
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
        safe_detail = detail if admin_view else redact_sensitive_text_for_non_admin(detail)
        lines.extend(["", f"详情：{html.escape(safe_detail)[:500]}"])
    if not admin_view:
        lines.extend(["", "敏感连接信息已隐藏，仅管理员可查看完整详情。"])
    return "\n".join(lines)

def format_collector_gap_alert(previous_ts: int, current_ts: int, gap_seconds: int) -> str:
    return "\n".join([
        "✅ <b>采集异常恢复</b>",
        "────────────",
        "检测到 Bot 已恢复运行，但中断期间未能完成流量采样。",
        "",
        f"中断开始：{format_timestamp(previous_ts)}",
        f"恢复采样：{format_timestamp(current_ts)}",
        f"影响时长：{format_duration(gap_seconds)}",
    ])

def redact_sensitive_text_for_non_admin(text: str) -> str:
    """Remove host/IP/port-like details from messages shown to non-admin Telegram users."""
    if not text:
        return text
    redacted = text
    redacted = re.sub(r"https?://[^\s<]+", "[已隐藏URL]", redacted)
    redacted = re.sub(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?", "[已隐藏IP]", redacted)
    redacted = re.sub(r"\[[0-9a-fA-F:]+\](?::\d{1,5})?", "[已隐藏IP]", redacted)
    redacted = re.sub(r"\b[0-9a-fA-F]{0,4}:[0-9a-fA-F:]{2,}(?::\d{1,5})?\b", "[已隐藏IP]", redacted)
    redacted = re.sub(r"(?i)\b([a-z0-9-]+\.)+[a-z]{2,}(?::\d{1,5})?\b", "[已隐藏主机]", redacted)
    redacted = re.sub(r"(?i)\b(host|hostname|server|addr|address|endpoint)\s*[:=]\s*[^\s，。；;]+", r"\1=[已隐藏]", redacted)
    redacted = re.sub(r"(?i)\b(port)\s*[:=]\s*\d{1,5}\b", r"\1=[已隐藏]", redacted)
    redacted = re.sub(r"端口\s*[:：]?\s*\d{1,5}", "端口：[已隐藏]", redacted)
    return redacted

def alert_setting_label(setting: dict[str, Any], alert_type: str, cache_path: Path | None = None) -> str:
    if alert_type == "traffic":
        if int(setting.get("traffic_whitelist") or 0):
            return "白名单"
        period = setting.get("traffic_period") or (alert_global_period_sync(cache_path, "traffic") if cache_path else ALERT_DEFAULT_PERIOD)
        threshold = setting.get("traffic_threshold_bytes") or (alert_global_threshold_sync(cache_path, "traffic") if cache_path else TRAFFIC_ALERT_DEFAULT_THRESHOLD_BYTES)
        return f"{alert_period_label(period)} / {format_bytes(int(threshold))}"
    if int(setting.get("ip_whitelist") or 0):
        return "白名单"
    period = setting.get("ip_period") or (alert_global_period_sync(cache_path, "ip") if cache_path else ALERT_DEFAULT_PERIOD)
    threshold = setting.get("ip_city_threshold") or (alert_global_threshold_sync(cache_path, "ip") if cache_path else IP_ALERT_DEFAULT_CITY_THRESHOLD)
    return f"{alert_period_label(period)} / {int(threshold)} 个城市"

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
    return "\n".join([title, "────────────", f"当前规则：<b>{period_label} / {threshold_text}</b>"] ) + suffix

def alert_user_setting_text_sync(cache_path: Path, alert_type: str, xboard_user_id: int) -> str:
    setting = alert_user_setting_sync(cache_path, xboard_user_id)
    name = cached_user_name_by_id(cache_path, xboard_user_id) or f"用户{xboard_user_id}"
    if alert_type == "traffic":
        whitelisted = bool(int(setting.get("traffic_whitelist") or 0))
        effective = alert_setting_label(setting, "traffic", cache_path)
        uses_independent = setting.get("traffic_threshold_bytes") is not None or setting.get("traffic_period") is not None
        if whitelisted:
            current_line = "当前适用：白名单 (不提醒)"
        else:
            current_line = f"当前适用：{'独立规则' if uses_independent else '默认规则'} (<b>{effective}</b>)"
        lines = ["🚨 <b>用户流量告警设置</b>", "────────────", f"用户：{render_user_label(xboard_user_id, name)}", current_line]
    else:
        whitelisted = bool(int(setting.get("ip_whitelist") or 0))
        effective = alert_setting_label(setting, "ip", cache_path)
        uses_independent = setting.get("ip_city_threshold") is not None or setting.get("ip_period") is not None
        if whitelisted:
            current_line = "当前适用：白名单 (不提醒)"
        else:
            current_line = f"当前适用：{'独立规则' if uses_independent else '默认规则'} (<b>{effective}</b>)"
        lines = ["🚨 <b>用户异地告警设置</b>", "────────────", f"用户：{render_user_label(xboard_user_id, name)}", current_line]
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

def alert_period_label(period: str | None) -> str:
    return ALERT_PERIOD_LABELS.get(period or ALERT_DEFAULT_PERIOD, ALERT_PERIOD_LABELS[ALERT_DEFAULT_PERIOD])

def notification_ip_alert_mode_label(mode: str) -> str:
    return {"off": "关闭", "basic": "基础", "advanced": "高级"}.get(mode, "基础")
# Export this module's own public symbols for downstream star imports.
__all__ = [name for name in globals() if not name.startswith("_")]

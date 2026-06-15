from __future__ import annotations

from ..common import *
from .mysql import collect_traffic_counters_sync, fetch_user_display_details_sync, fetch_all_user_display_details_sync

def _normalize_geo_name(value: Any) -> str:
    return str(value or "").strip()

def _geo_text_contains(values: list[str], patterns: list[str]) -> bool:
    text = " ".join(values)
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)

def _normalize_taiwan_city(region: str, city: str, district: str) -> str:
    county_cities = {
        "台北市", "新北市", "桃园市", "臺北市", "新北市", "桃園市", "台中市", "臺中市", "台南市", "臺南市", "高雄市",
        "基隆市", "新竹市", "嘉义市", "嘉義市", "新竹县", "新竹縣", "苗栗县", "苗栗縣", "彰化县", "彰化縣",
        "南投县", "南投縣", "云林县", "雲林縣", "嘉义县", "嘉義縣", "屏东县", "屏東縣", "宜兰县", "宜蘭縣",
        "花莲县", "花蓮縣", "台东县", "臺東縣", "澎湖县", "澎湖縣", "金门县", "金門縣", "连江县", "連江縣",
    }
    aliases = {"Taipei": "台北市", "New Taipei": "新北市", "Taoyuan": "桃园市", "Taichung": "台中市", "Tainan": "台南市", "Kaohsiung": "高雄市"}
    for item in (region, city, district):
        name = _normalize_geo_name(item)
        if not name:
            continue
        if name in county_cities:
            return name
        if name in aliases:
            return aliases[name]
    return _normalize_geo_name(region or city or district) or "台湾未知城市"

def build_geo_stat_area(data: dict[str, Any]) -> dict[str, str]:
    country_code = _normalize_geo_name(data.get("countryCode")).upper()
    country = _normalize_geo_name(data.get("country"))
    region = _normalize_geo_name(data.get("regionName"))
    city = _normalize_geo_name(data.get("city"))
    district = _normalize_geo_name(data.get("district"))
    values = [country, region, city, district]

    if country_code == "HK" or _geo_text_contains(values, [r"香港", r"Hong\s*Kong"]):
        return {"key": "HK:香港", "name": "香港", "level": "sar_city"}
    if country_code == "MO" or _geo_text_contains(values, [r"澳门", r"澳門", r"Macau", r"Macao"]):
        return {"key": "MO:澳门", "name": "澳门", "level": "sar_city"}
    if country_code == "TW" or _geo_text_contains(values, [r"台湾", r"Taiwan"]):
        stat_name = _normalize_taiwan_city(region, city, district)
        return {"key": f"TW:{stat_name}", "name": stat_name, "level": "tw_city"}
    if country_code == "CN" or country == "中国":
        municipalities = {"北京市", "上海市", "天津市", "重庆市"}
        if region in municipalities:
            return {"key": f"CN:{region}", "name": region, "level": "municipality"}
        stat_name = city or region or "未知城市"
        return {"key": f"CN:{region or '未知省份'}:{stat_name}", "name": stat_name, "level": "city"}
    stat_name = city or region or country or "未知地区"
    return {"key": f"{country_code or country or 'UNKNOWN'}:{region}:{stat_name}", "name": stat_name, "level": "city"}

def _cache_format_bytes(value: int | float | None) -> str:
    size = float(value or 0)
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    for unit in units:
        if abs(size) < 1024 or unit == units[-1]:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"

def cached_user_button_label(row: sqlite3.Row | None, xboard_user_id: int) -> str:
    display_name = str(row["display_name"] or "").strip() if row else ""
    if display_name:
        return f"{display_name} (user_id: {xboard_user_id})"
    return f"用户 {xboard_user_id}"

def geo_area_rule_label(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return value
    if "|" in value:
        return " / ".join(part for part in value.split("|") if part)
    if ":" in value:
        return value.split(":")[-1] or value
    return value

def raw_geo_data(row: sqlite3.Row) -> dict[str, Any]:
    try:
        raw = row["raw"]
    except (KeyError, IndexError):
        raw = None
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}

def safe_autolink_text(value: str) -> str:
    """Prevent Telegram from auto-linking domain-like fragments in plain text."""
    return value.replace(".", ".\u200b")

def _row_value(row: sqlite3.Row, name: str) -> Any:
    try:
        return row[name]
    except (KeyError, IndexError):
        return None

def geo_area_key(row: sqlite3.Row) -> str | None:
    stat_area_key = str(_row_value(row, "stat_area_key") or "").strip()
    if stat_area_key:
        return stat_area_key
    country = str(_row_value(row, "country") or "").strip()
    region = str(_row_value(row, "region") or "").strip()
    city = str(_row_value(row, "city") or "").strip()
    if city:
        return "|".join(part for part in (country, region, city) if part)
    if region:
        return "|".join(part for part in (country, region) if part)
    if country:
        return country
    return None

def geo_area_display_label(row: sqlite3.Row, value: str | None = None) -> str:
    """Human-facing area label. Do not expose internal rule keys like JP:Tokyo:Tokyo."""
    stat_area_name = str(_row_value(row, "stat_area_name") or "").strip()
    country = str(_row_value(row, "country") or "").strip()
    region = str(_row_value(row, "region") or "").strip()
    city = str(_row_value(row, "city") or "").strip()
    parts: list[str] = []
    for part in (country, region, city or stat_area_name):
        if part and part not in parts:
            parts.append(part)
    if parts:
        return " / ".join(parts)
    return geo_area_rule_label(value or "")

def count_geo_areas(rows: list[sqlite3.Row]) -> int:
    return len({key for row in rows if (key := geo_area_key(row))})

def render_cached_user_label(row: sqlite3.Row | None, xboard_user_id: int) -> str:
    display_name = str(row["display_name"] or "").strip() if row else ""
    if display_name:
        return f"{html.escape(display_name)} (user_id: {html.escape(str(xboard_user_id))})"
    return f"用户 {html.escape(str(xboard_user_id))}"

def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} 小时 {minutes} 分钟"
    if minutes:
        return f"{minutes} 分钟 {sec} 秒" if sec else f"{minutes} 分钟"
    return f"{sec} 秒"

def format_timestamp(ts: int | None) -> str:
    if not ts:
        return "未知"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

def ipv4_24_cidr(value: str) -> str | None:
    try:
        ip_obj = ipaddress.ip_address(str(value).strip())
    except ValueError:
        return None
    if ip_obj.version != 4:
        return None
    return str(ipaddress.ip_network(f"{ip_obj}/24", strict=False))

def asn_key_from_raw(raw: dict[str, Any]) -> str | None:
    as_text = str(raw.get("as") or "").strip()
    match = re.search(r"\bAS\s*(\d+)\b", as_text, re.IGNORECASE)
    if match:
        return f"AS{match.group(1)}"
    asn = str(raw.get("asname") or raw.get("org") or raw.get("isp") or "").strip()
    return asn or None

def asn_label_from_raw(raw: dict[str, Any]) -> str | None:
    key = asn_key_from_raw(raw)
    name = str(raw.get("asname") or raw.get("org") or raw.get("isp") or "").strip()
    if key and name and name != key:
        return f"{key} {name}"
    return key or name or None

def geo_location_text(row: sqlite3.Row) -> str:
    raw_parts = [str(_row_value(row, name) or "").strip() for name in ("country", "region", "city", "district")]
    parts: list[str] = []
    for part in raw_parts:
        if part and part not in parts:
            parts.append(part)
    return "，".join(parts)

def asn_text(row: sqlite3.Row) -> str:
    raw = raw_geo_data(row)
    return asn_label_from_raw(raw) or str(_row_value(row, "isp") or "").strip() or "待查询"

def asn_key_for_geo_row(row: sqlite3.Row) -> str | None:
    return asn_key_from_raw(raw_geo_data(row))

def ignore_items_from_ip_rows(rows: list[sqlite3.Row], dimension: str) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        if dimension == "area":
            value = geo_area_key(row)
            if not value:
                continue
            label = geo_area_display_label(row, value)
        elif dimension == "asn":
            value = asn_key_for_geo_row(row)
            if not value:
                continue
            label = asn_text(row)
        elif dimension == "cidr":
            value = ipv4_24_cidr(str(row["ip"] or ""))
            if not value:
                continue
            label = value
        else:
            continue
        bucket = buckets.setdefault(value, {"value": value, "label": label, "ips": set(), "last_seen_at": 0})
        bucket["ips"].add(str(row["ip"]))
        bucket["last_seen_at"] = max(int(bucket["last_seen_at"]), int(row["last_seen_at"] or 0))
    return [
        {"value": value, "label": str(bucket["label"]), "sub": f"{len(bucket['ips'])} IP", "last_seen_at": int(bucket["last_seen_at"])}
        for value, bucket in sorted(buckets.items(), key=lambda item: (-int(item[1]["last_seen_at"]), item[0]))
    ]

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

def alert_setting_label(setting: dict[str, Any], alert_type: str, cache_path: Path | None = None) -> str:
    if alert_type == "traffic":
        if int(setting.get("traffic_whitelist") or 0):
            return "白名单"
        period = setting.get("traffic_period") or (alert_global_period_sync(cache_path, "traffic") if cache_path else ALERT_DEFAULT_PERIOD)
        threshold = setting.get("traffic_threshold_bytes") or (alert_global_threshold_sync(cache_path, "traffic") if cache_path else TRAFFIC_ALERT_DEFAULT_THRESHOLD_BYTES)
        return f"{alert_period_label(period)} / {_cache_format_bytes(int(threshold))}"
    if int(setting.get("ip_whitelist") or 0):
        return "白名单"
    period = setting.get("ip_period") or (alert_global_period_sync(cache_path, "ip") if cache_path else ALERT_DEFAULT_PERIOD)
    threshold = setting.get("ip_city_threshold") or (alert_global_threshold_sync(cache_path, "ip") if cache_path else IP_ALERT_DEFAULT_CITY_THRESHOLD)
    return f"{alert_period_label(period)} / {int(threshold)} 个城市"

def resolve_cache_path(path: Path, base_dir: Path | None = None) -> Path:
    if path.is_absolute():
        return path
    return (base_dir or Path.cwd()) / path

def cache_connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn

def init_cache(path: Path) -> None:
    cache_path = path.resolve()
    if cache_path in _INITIALIZED_CACHE_PATHS and cache_path.exists():
        return
    with cache_connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS active_ip_records (
                user_id INTEGER NOT NULL,
                ip TEXT NOT NULL,
                first_seen_at INTEGER NOT NULL,
                last_seen_at INTEGER NOT NULL,
                last_ttl INTEGER,
                source_key TEXT,
                ignored_at INTEGER,
                ignore_reason TEXT,
                ignore_note TEXT,
                PRIMARY KEY (user_id, ip)
            );
            CREATE INDEX IF NOT EXISTS idx_active_ip_records_last_seen_at
                ON active_ip_records(last_seen_at);
            CREATE INDEX IF NOT EXISTS idx_active_ip_records_user_last_seen
                ON active_ip_records(user_id, last_seen_at);

            CREATE TABLE IF NOT EXISTS debug_ip_record_suppressions (
                user_id INTEGER NOT NULL,
                ip TEXT NOT NULL,
                last_seen_floor INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (user_id, ip)
            );
            CREATE INDEX IF NOT EXISTS idx_debug_ip_record_suppressions_expires
                ON debug_ip_record_suppressions(expires_at);

            CREATE TABLE IF NOT EXISTS ignored_ip_rules (
                dimension TEXT NOT NULL,
                value TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (dimension, value)
            );

            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                display_name TEXT,
                remarks TEXT,
                email TEXT,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ip_geo_cache (
                ip TEXT PRIMARY KEY,
                country TEXT,
                region TEXT,
                city TEXT,
                district TEXT,
                isp TEXT,
                stat_area_key TEXT,
                stat_area_name TEXT,
                stat_area_level TEXT,
                raw TEXT,
                queried_at INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS collector_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pinned_dashboard_messages (
                kind TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                is_pinned INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (kind, chat_id)
            );

            CREATE TABLE IF NOT EXISTS dashboard_auto_delete_messages (
                chat_id TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                is_pinned INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (chat_id, message_id)
            );

            CREATE TABLE IF NOT EXISTS notification_subscriptions (
                chat_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (chat_id, kind)
            );

            CREATE TABLE IF NOT EXISTS traffic_counter_snapshots (
                kind TEXT NOT NULL,
                entity_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                total INTEGER NOT NULL,
                sampled_at INTEGER NOT NULL,
                PRIMARY KEY (kind, entity_id)
            );

            CREATE TABLE IF NOT EXISTS traffic_delta_samples (
                sampled_at INTEGER NOT NULL,
                kind TEXT NOT NULL,
                entity_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                delta INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS traffic_sample_gaps (
                gap_start_at INTEGER NOT NULL,
                gap_end_at INTEGER NOT NULL,
                gap_seconds INTEGER NOT NULL,
                detected_at INTEGER NOT NULL,
                PRIMARY KEY (gap_start_at, gap_end_at)
            );
            CREATE TABLE IF NOT EXISTS traffic_ranges (
                kind TEXT PRIMARY KEY,
                start_ts INTEGER NOT NULL,
                end_ts INTEGER NOT NULL,
                label TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ui_preferences (
                user_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (user_id, key)
            );

            CREATE TABLE IF NOT EXISTS operation_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at INTEGER NOT NULL,
                actor_tg_id INTEGER,
                actor_name TEXT,
                category TEXT NOT NULL,
                action TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_operation_logs_category_time
                ON operation_logs(category, created_at DESC);
            CREATE TABLE IF NOT EXISTS operation_log_reads (
                user_id INTEGER NOT NULL,
                log_id INTEGER NOT NULL,
                read_at INTEGER NOT NULL,
                PRIMARY KEY (user_id, log_id)
            );
            CREATE INDEX IF NOT EXISTS idx_operation_log_reads_user
                ON operation_log_reads(user_id, log_id);

            CREATE TABLE IF NOT EXISTS alert_user_settings (
                user_id INTEGER PRIMARY KEY,
                traffic_threshold_bytes INTEGER,
                traffic_whitelist INTEGER NOT NULL DEFAULT 0,
                ip_city_threshold INTEGER,
                ip_whitelist INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_traffic_delta_samples_window
                ON traffic_delta_samples(kind, sampled_at);
            CREATE INDEX IF NOT EXISTS idx_traffic_delta_samples_entity
                ON traffic_delta_samples(kind, entity_id, sampled_at);
            CREATE INDEX IF NOT EXISTS idx_traffic_sample_gaps_window
                ON traffic_sample_gaps(gap_start_at, gap_end_at);
            """
        )
        existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(alert_user_settings)").fetchall()}
        if "traffic_period" not in existing_columns:
            conn.execute("ALTER TABLE alert_user_settings ADD COLUMN traffic_period TEXT")
        if "ip_period" not in existing_columns:
            conn.execute("ALTER TABLE alert_user_settings ADD COLUMN ip_period TEXT")
        active_ip_columns = {row[1] for row in conn.execute("PRAGMA table_info(active_ip_records)").fetchall()}
        if "ignored_at" not in active_ip_columns:
            conn.execute("ALTER TABLE active_ip_records ADD COLUMN ignored_at INTEGER")
        if "ignore_reason" not in active_ip_columns:
            conn.execute("ALTER TABLE active_ip_records ADD COLUMN ignore_reason TEXT")
        if "ignore_note" not in active_ip_columns:
            conn.execute("ALTER TABLE active_ip_records ADD COLUMN ignore_note TEXT")
        geo_cache_columns = {row[1] for row in conn.execute("PRAGMA table_info(ip_geo_cache)").fetchall()}
        for column_name, column_type in {
            "district": "TEXT",
            "stat_area_key": "TEXT",
            "stat_area_name": "TEXT",
            "stat_area_level": "TEXT",
        }.items():
            if column_name not in geo_cache_columns:
                conn.execute(f"ALTER TABLE ip_geo_cache ADD COLUMN {column_name} {column_type}")
        rows_needing_stat_area = conn.execute(
            """
            SELECT ip, raw
            FROM ip_geo_cache
            WHERE (stat_area_key IS NULL OR stat_area_key = '')
              AND raw IS NOT NULL AND raw != ''
            LIMIT 1000
            """
        ).fetchall()
        for geo_row in rows_needing_stat_area:
            try:
                raw_data = json.loads(str(geo_row["raw"] or "{}"))
            except Exception:
                continue
            if not isinstance(raw_data, dict) or raw_data.get("status") not in (None, "success"):
                continue
            stat_area = build_geo_stat_area(raw_data)
            conn.execute(
                """
                UPDATE ip_geo_cache
                SET district = COALESCE(NULLIF(district, ''), ?),
                    stat_area_key = ?,
                    stat_area_name = ?,
                    stat_area_level = ?
                WHERE ip = ?
                """,
                (
                    str(raw_data.get("district") or ""),
                    stat_area["key"],
                    stat_area["name"],
                    stat_area["level"],
                    str(geo_row["ip"]),
                ),
            )
    _INITIALIZED_CACHE_PATHS.add(cache_path)

def ui_pref_get_sync(cache_path: Path, user_id: int, key: str) -> str | None:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        row = conn.execute(
            "SELECT value FROM ui_preferences WHERE user_id = ? AND key = ?",
 (user_id, key),
        ).fetchone()
    return str(row["value"]) if row else None

def ui_pref_set_sync(cache_path: Path, user_id: int, key: str, value: str) -> None:
    init_cache(cache_path)
    now_ts = int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        conn.execute(
            """
            INSERT INTO ui_preferences(user_id, key, value, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
 (user_id, key, value, now_ts),
        )

def ui_pref_delete_sync(cache_path: Path, user_id: int, key: str) -> None:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        conn.execute("DELETE FROM ui_preferences WHERE user_id = ? AND key = ?", (user_id, key))

def actor_name_from_user(user: Any) -> str:
    if not user:
        return "未知用户"
    username = getattr(user, "username", None)
    full_name = getattr(user, "full_name", None)
    if username and full_name:
        return f"{full_name} (@{username})"
    return str(full_name or username or getattr(user, "id", "未知用户"))

def operation_log_add_sync(cache_path: Path, actor_tg_id: int | None, actor_name: str | None, category: str, action: str, detail: str = "") -> None:
    init_cache(cache_path)
    now_ts = int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        conn.execute(
            """
            INSERT INTO operation_logs(created_at, actor_tg_id, actor_name, category, action, detail)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (now_ts, actor_tg_id, actor_name or "", category, action, detail or ""),
        )

def operation_logs_list_sync(cache_path: Path, category: str | None = None, limit: int = 30, viewer_user_id: int | None = None) -> list[dict[str, Any]]:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        if viewer_user_id is not None:
            rows = conn.execute(
                """
                SELECT l.*, CASE WHEN r.log_id IS NULL THEN 0 ELSE 1 END AS is_read
                FROM operation_logs l
                LEFT JOIN operation_log_reads r ON r.log_id = l.id AND r.user_id = ?
                WHERE l.category = ?
                ORDER BY l.created_at DESC, l.id DESC
                LIMIT ?
                """,
                (int(viewer_user_id), category or "", int(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM operation_logs
                WHERE category = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (category or "", int(limit)),
            ).fetchall()
    return [dict(row) for row in rows]

def operation_log_counts_sync(cache_path: Path, viewer_user_id: int, categories: list[str]) -> dict[str, tuple[int, int]]:
    init_cache(cache_path)
    result = {category: (0, 0) for category in categories}
    with cache_connect(cache_path) as conn:
        for category in categories:
            total = int(conn.execute("SELECT COUNT(*) FROM operation_logs WHERE category = ?", (category,)).fetchone()[0] or 0)
            read_count = int(conn.execute(
                """
                SELECT COUNT(*)
                FROM operation_logs l
                JOIN operation_log_reads r ON r.log_id = l.id AND r.user_id = ?
                WHERE l.category = ?
                """,
                (int(viewer_user_id), category),
            ).fetchone()[0] or 0)
            result[category] = (max(total - read_count, 0), total)
    return result

def operation_log_get_sync(cache_path: Path, log_id: int) -> dict[str, Any] | None:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        row = conn.execute("SELECT * FROM operation_logs WHERE id = ?", (int(log_id),)).fetchone()
    return dict(row) if row else None

def operation_log_mark_read_sync(cache_path: Path, viewer_user_id: int, log_id: int) -> None:
    init_cache(cache_path)
    now_ts = int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        conn.execute(
            """
            INSERT INTO operation_log_reads(user_id, log_id, read_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, log_id) DO UPDATE SET read_at = excluded.read_at
            """,
            (int(viewer_user_id), int(log_id), now_ts),
        )

def set_collector_state(conn: sqlite3.Connection, key: str, value: str, now_ts: int) -> None:
    conn.execute(
        """
        INSERT INTO collector_state(key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """,
 (key, value, now_ts),
    )

def get_collector_state_sync(cache_path: Path, key: str) -> tuple[str, int] | None:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        row = conn.execute("SELECT value, updated_at FROM collector_state WHERE key = ?", (key,)).fetchone()
    if not row:
        return None
    return str(row["value"]), int(row["updated_at"] or 0)

def auth_roles_load_sync(cache_path: Path) -> tuple[set[int], set[int]] | None:
    state = get_collector_state_sync(cache_path, "telegram_auth_roles")
    if not state:
        return None
    try:
        data = json.loads(state[0])
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return _as_int_set(data.get("manager_user_ids")), _as_int_set(data.get("authorized_user_ids"))

def auth_roles_save_sync(cache_path: Path, manager_user_ids: set[int], authorized_user_ids: set[int]) -> None:
    init_cache(cache_path)
    now_ts = int(datetime.now().timestamp())
    payload = json.dumps(
        {
            "manager_user_ids": sorted(int(uid) for uid in manager_user_ids),
            "authorized_user_ids": sorted(int(uid) for uid in authorized_user_ids),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    with cache_connect(cache_path) as conn:
        set_collector_state(conn, "telegram_auth_roles", payload, now_ts)

def update_telegram_roles_in_cache_sync(
    cache_path: Path,
    admin_user_id: int | set[int] | None,
    current_manager_user_ids: set[int],
    current_authorized_user_ids: set[int],
    add_authorized_user_id: int | None = None,
    remove_authorized_user_ids: set[int] | None = None,
    promote_manager_user_ids: set[int] | None = None,
    demote_manager_user_ids: set[int] | None = None,
    remove_manager_user_ids: set[int] | None = None,
) -> tuple[set[int], set[int]]:
    managers = set(current_manager_user_ids)
    users = set(current_authorized_user_ids)
    super_admin_ids = set(admin_user_id) if isinstance(admin_user_id, set) else ({admin_user_id} if admin_user_id is not None else set())

    def ensure_not_super_admin(uid: int) -> None:
        if int(uid) in super_admin_ids:
            raise ValueError("超级管理员只允许通过环境变量管理")

    if add_authorized_user_id is not None:
        target = int(add_authorized_user_id)
        ensure_not_super_admin(target)
        if target not in managers:
            users.add(target)

    if remove_authorized_user_ids:
        for uid in remove_authorized_user_ids:
            target = int(uid)
            ensure_not_super_admin(target)
            users.discard(target)

    if promote_manager_user_ids:
        for uid in promote_manager_user_ids:
            target = int(uid)
            ensure_not_super_admin(target)
            users.discard(target)
            managers.add(target)

    if demote_manager_user_ids:
        for uid in demote_manager_user_ids:
            target = int(uid)
            ensure_not_super_admin(target)
            managers.discard(target)
            users.add(target)

    if remove_manager_user_ids:
        for uid in remove_manager_user_ids:
            target = int(uid)
            ensure_not_super_admin(target)
            managers.discard(target)
            users.discard(target)

    managers.difference_update(super_admin_ids)
    users.difference_update(super_admin_ids)
    users.difference_update(managers)
    auth_roles_save_sync(cache_path, managers, users)
    return managers, users

def update_authorized_users_in_cache_sync(cache_path: Path, admin_user_id: int | set[int] | None, current_manager_user_ids: set[int], current_authorized_user_ids: set[int], add_user_id: int | None = None, remove_user_ids: set[int] | None = None) -> set[int]:
    _, users = update_telegram_roles_in_cache_sync(
        cache_path,
        admin_user_id,
        current_manager_user_ids,
        current_authorized_user_ids,
        add_authorized_user_id=add_user_id,
        remove_authorized_user_ids=remove_user_ids,
    )
    return users

def notification_status_sync(cache_path: Path, chat_id: str, default_enabled_kinds: set[str] | None = None) -> dict[str, bool]:
    init_cache(cache_path)
    default_enabled_kinds = default_enabled_kinds or set()
    with cache_connect(cache_path) as conn:
        rows = conn.execute(
            "SELECT kind, enabled FROM notification_subscriptions WHERE chat_id = ?",
 (chat_id,),
        ).fetchall()
    status = {kind: kind in default_enabled_kinds for kind in NOTIFICATION_KINDS}
    for row in rows:
        kind = str(row["kind"] or "")
        if kind in status:
            status[kind] = bool(int(row["enabled"] or 0))
    return status

def notification_ip_alert_mode_sync(cache_path: Path, chat_id: str) -> str:
    """Return ip_alert delivery mode: off/basic/advanced. Stored as enabled 0/1/2 for compatibility."""
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        row = conn.execute(
            "SELECT enabled FROM notification_subscriptions WHERE chat_id = ? AND kind = 'ip_alert'",
            (chat_id,),
        ).fetchone()
    if row is None:
        return "basic" if "ip_alert" in DEFAULT_ALLOWLIST_NOTIFICATION_KINDS else "off"
    value = int(row["enabled"] or 0)
    if value >= 2:
        return "advanced"
    if value == 1:
        return "basic"
    return "off"

def notification_toggle_sync(cache_path: Path, chat_id: str, kind: str) -> bool | str:
    if kind not in NOTIFICATION_KINDS:
        raise ValueError("unknown notification kind")
    init_cache(cache_path)
    now_ts = int(datetime.now().timestamp())
    default_enabled = 1 if kind in DEFAULT_ALLOWLIST_NOTIFICATION_KINDS else 0
    with cache_connect(cache_path) as conn:
        row = conn.execute(
            "SELECT enabled FROM notification_subscriptions WHERE chat_id = ? AND kind = ?",
 (chat_id, kind),
        ).fetchone()
        current_enabled = int(row["enabled"] if row else default_enabled)
        if kind == "ip_alert":
            # 三段式循环：关闭(0) -> 基础(1) -> 高级(2) -> 关闭(0)
            new_enabled = {0: 1, 1: 2, 2: 0}.get(current_enabled, 0)
        else:
            new_enabled = 0 if current_enabled else 1
        conn.execute(
            """
            INSERT INTO notification_subscriptions(chat_id, kind, enabled, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id, kind) DO UPDATE SET
                enabled=excluded.enabled,
                updated_at=excluded.updated_at
            """,
 (chat_id, kind, new_enabled, now_ts),
        )
    if kind == "ip_alert":
        return "advanced" if new_enabled >= 2 else ("basic" if new_enabled == 1 else "off")
    return bool(new_enabled)

def notification_enabled_chats_sync(cache_path: Path, kind: str) -> list[str]:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        rows = conn.execute(
            "SELECT chat_id FROM notification_subscriptions WHERE kind = ? AND enabled = 1",
 (kind,),
        ).fetchall()
    return [str(row["chat_id"]) for row in rows]

def default_allowlist_notification_chats_sync(cache_path: Path, cfg: AppConfig, kind: str) -> list[str]:
    """Notifications in DEFAULT_ALLOWLIST_NOTIFICATION_KINDS are enabled for Telegram allowlist unless explicitly disabled."""
    if kind == "version_update":
        chats: list[str] = []
        for admin_uid in sorted(cfg.telegram.super_admin_user_ids):
            admin_chat = str(admin_uid)
            if notification_status_sync(cache_path, admin_chat, DEFAULT_ALLOWLIST_NOTIFICATION_KINDS).get(kind):
                chats.append(admin_chat)
        return chats
    if kind not in DEFAULT_ALLOWLIST_NOTIFICATION_KINDS:
        return notification_enabled_chats_sync(cache_path, kind)
    init_cache(cache_path)
    chats = {str(uid) for uid in cfg.telegram.allowed_user_ids}
    with cache_connect(cache_path) as conn:
        rows = conn.execute(
            "SELECT chat_id, enabled FROM notification_subscriptions WHERE kind = ?",
 (kind,),
        ).fetchall()
    for row in rows:
        chat_id = str(row["chat_id"])
        if int(row["enabled"] or 0):
            chats.add(chat_id)
        else:
            chats.discard(chat_id)
    return sorted(chats)

def collector_notification_chats_sync(cache_path: Path, cfg: AppConfig) -> list[str]:
    return default_allowlist_notification_chats_sync(cache_path, cfg, "collector")

def alert_notification_chats_sync(cache_path: Path, cfg: AppConfig, alert_type: str) -> list[str]:
    kind = "traffic_alert" if alert_type == "traffic" else "ip_alert"
    return default_allowlist_notification_chats_sync(cache_path, cfg, kind)

def ip_alert_notification_chat_modes_sync(cache_path: Path, cfg: AppConfig) -> dict[str, str]:
    return {chat_id: notification_ip_alert_mode_sync(cache_path, chat_id) for chat_id in alert_notification_chats_sync(cache_path, cfg, "ip")}

def alert_global_period_sync(cache_path: Path, alert_type: str) -> str:
    key = "traffic_alert_global_period" if alert_type == "traffic" else "ip_alert_global_period"
    value = alert_state_get_sync(cache_path, key)
    default_period = TRAFFIC_ALERT_DEFAULT_PERIOD if alert_type == "traffic" else ALERT_DEFAULT_PERIOD
    return value if value in ALERT_PERIOD_LABELS else default_period

def initialization_mark_started_sync(cache_path: Path, reason: str = "startup") -> None:
    init_cache(cache_path)
    now_ts = int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        set_collector_state(conn, "initialization_status", "running", now_ts)
        set_collector_state(conn, "initialization_started_at", str(now_ts), now_ts)
        set_collector_state(conn, "initialization_reason", reason, now_ts)
        conn.execute("DELETE FROM collector_state WHERE key = 'initialization_completed_at'")

def initialization_mark_complete_sync(cache_path: Path, records_count: int, geo_total: int, geo_success: int, geo_failed: int) -> None:
    init_cache(cache_path)
    now_ts = int(datetime.now().timestamp())
    payload = {"records": int(records_count), "geo_total": int(geo_total), "geo_success": int(geo_success), "geo_failed": int(geo_failed)}
    with cache_connect(cache_path) as conn:
        set_collector_state(conn, "initialization_status", "awaiting_ack", now_ts)
        set_collector_state(conn, "initialization_completed_at", str(now_ts), now_ts)
        set_collector_state(conn, "initialization_result", json.dumps(payload, ensure_ascii=False), now_ts)

def initialization_acknowledge_sync(cache_path: Path) -> None:
    init_cache(cache_path)
    now_ts = int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        set_collector_state(conn, "initialization_status", "complete", now_ts)
        set_collector_state(conn, "initialization_acknowledged_at", str(now_ts), now_ts)

def initialization_status_sync(cache_path: Path, queries_per_minute: int = DEFAULT_IP_GEO_QUERIES_PER_MINUTE) -> dict[str, Any]:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        active_ips = int(conn.execute("SELECT COUNT(DISTINCT ip) FROM active_ip_records WHERE ignored_at IS NULL").fetchone()[0] or 0)
        geo_total = int(conn.execute("SELECT COUNT(*) FROM ip_geo_cache").fetchone()[0] or 0)
        geo_pending = int(conn.execute(
            """
            SELECT COUNT(*) FROM ip_geo_cache
            WHERE (queried_at IS NULL OR queried_at <= 0)
              AND (raw IS NULL OR raw = '')
            """
        ).fetchone()[0] or 0)
        geo_finished = int(conn.execute(
            """
            SELECT COUNT(*) FROM ip_geo_cache
            WHERE queried_at IS NOT NULL AND queried_at > 0
            """
        ).fetchone()[0] or 0)
        geo_no_result = int(conn.execute(
            """
            SELECT COUNT(*) FROM ip_geo_cache
            WHERE queried_at IS NOT NULL AND queried_at > 0
              AND (raw IS NOT NULL AND raw <> '')
              AND (country IS NULL OR country = '')
              AND (region IS NULL OR region = '')
              AND (city IS NULL OR city = '')
            """
        ).fetchone()[0] or 0)
    status_state = get_collector_state_sync(cache_path, "initialization_status")
    started_state = get_collector_state_sync(cache_path, "initialization_started_at")
    completed_state = get_collector_state_sync(cache_path, "initialization_completed_at")
    reset_state = get_collector_state_sync(cache_path, "cache_reset_at")
    collect_state = get_collector_state_sync(cache_path, "last_collect_at")
    status = status_state[0] if status_state else "complete"
    reset_ts = int(reset_state[0]) if reset_state and str(reset_state[0]).isdigit() else None
    completed_ts = int(completed_state[0]) if completed_state and str(completed_state[0]).isdigit() else None
    if reset_ts and (not completed_ts or completed_ts < reset_ts):
        status = "running"
    elif status == "running" and geo_pending <= 0 and collect_state:
        status = "awaiting_ack"
    pending = int(geo_pending)
    initializing = status != "complete"
    return {
        "status": status,
        "initializing": initializing,
        "awaiting_ack": status == "awaiting_ack",
        "started_at": int(started_state[0]) if started_state and str(started_state[0]).isdigit() else None,
        "completed_at": completed_ts,
        "last_collect_at": collect_state[1] if collect_state else None,
        "active_ips": int(active_ips),
        "geo_total": int(geo_total),
        "geo_finished": int(geo_finished),
        "geo_no_result": int(geo_no_result),
        "geo_pending": pending,
        "estimated_remaining_seconds": int((pending * 60 + max(1, int(queries_per_minute)) - 1) // max(1, int(queries_per_minute))) if pending > 0 else 0,
    }

def initialization_progress_text_sync(cache_path: Path, cfg: AppConfig) -> str:
    status = initialization_status_sync(cache_path, cfg.ip_geo_queries_per_minute)
    if status.get("awaiting_ack"):
        elapsed = None
        if status.get("started_at") and status.get("completed_at"):
            elapsed = max(0, int(status["completed_at"]) - int(status["started_at"]))
        lines = [
            "✅ <b>Xbot 初始化已完成</b>",
            "────────────",
            f"采集 IP：{status['active_ips']} 条",
            f"查询 IP：{status['geo_finished']}/{status['geo_total']}",
        ]
        if status.get("geo_no_result", 0) > 0:
            lines.append(f"无可用归属地/查询失败：{status['geo_no_result']} 条")
        if elapsed is not None:
            lines.append(f"使用时间：{format_duration(elapsed)}")
        if status.get("completed_at"):
            lines.append(f"完成时间：{format_timestamp(status['completed_at'])}")
        lines.extend(["", "请确认以上结果。点击下方按钮后，将进入主菜单并开始允许 IP 告警判断。"])
        return "\n".join(lines)
    lines = [
        "⏳ <b>正在初始化 Xbot 缓存</b>",
        "────────────",
        "首次使用或重置本地缓存后，需要先完成 Redis IP 采集与 IP 归属地查询。",
        "初始化完成前，暂时只显示进度，避免面板和告警基于不完整数据判断。",
        "",
        f"采集 IP：{status['active_ips']} 条",
        f"查询 IP：{status['geo_finished']}/{status['geo_total']}",
    ]
    if status["geo_pending"] > 0:
        lines.append("当前状态：等待查询或限流重试")
    if status.get("geo_no_result", 0) > 0:
        lines.append(f"已处理但无可用归属地：{status['geo_no_result']} 条（不阻塞初始化）")
    if status["geo_pending"] > 0:
        lines.append(f"预计剩余：约 {format_duration(status['estimated_remaining_seconds'])}")
    else:
        lines.append("预计剩余：等待下一轮采集确认")
    if status.get("started_at"):
        lines.append(f"开始时间：{format_timestamp(status['started_at'])}")
    if status.get("last_collect_at"):
        lines.append(f"最近采集：{format_timestamp(status['last_collect_at'])}")
    lines.extend(["", "请稍后刷新。短期限流会自动等待并重试；不会把限流误判为查询失败。"])
    return "\n".join(lines)

def alert_set_global_period_sync(cache_path: Path, alert_type: str, period: str) -> str:
    if period not in ALERT_PERIOD_LABELS:
        raise ValueError("unknown alert period")
    key = "traffic_alert_global_period" if alert_type == "traffic" else "ip_alert_global_period"
    alert_state_set_sync(cache_path, key, period)
    return period

def alert_global_threshold_sync(cache_path: Path, alert_type: str) -> int:
    key = "traffic_alert_global_threshold_bytes" if alert_type == "traffic" else "ip_alert_global_city_threshold"
    value = alert_state_get_sync(cache_path, key)
    if value is not None:
        try:
            parsed = int(value)
            if parsed > 0:
                return parsed
        except ValueError:
            pass
    return TRAFFIC_ALERT_DEFAULT_THRESHOLD_BYTES if alert_type == "traffic" else IP_ALERT_DEFAULT_CITY_THRESHOLD

def alert_set_global_threshold_sync(cache_path: Path, alert_type: str, value: int) -> int:
    if value <= 0:
        raise ValueError("threshold must be positive")
    key = "traffic_alert_global_threshold_bytes" if alert_type == "traffic" else "ip_alert_global_city_threshold"
    stored = value * 1024 ** 3 if alert_type == "traffic" else value
    alert_state_set_sync(cache_path, key, str(stored))
    return stored

def alert_user_setting_sync(cache_path: Path, xboard_user_id: int) -> dict[str, Any]:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        row = conn.execute(
            """
            SELECT user_id, traffic_threshold_bytes, traffic_whitelist, traffic_period, ip_city_threshold, ip_whitelist, ip_period
            FROM alert_user_settings WHERE user_id = ?
            """,
 (xboard_user_id,),
        ).fetchone()
    if not row:
        return {
            "user_id": xboard_user_id,
            "traffic_threshold_bytes": None,
            "traffic_whitelist": 0,
            "traffic_period": None,
            "ip_city_threshold": None,
            "ip_whitelist": 0,
            "ip_period": None,
        }
    return dict(row)

def alert_upsert_setting_sync(cache_path: Path, xboard_user_id: int, **changes: Any) -> dict[str, Any]:
    init_cache(cache_path)
    current = alert_user_setting_sync(cache_path, xboard_user_id)
    current.update(changes)
    now_ts = int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        conn.execute(
            """
            INSERT INTO alert_user_settings(user_id, traffic_threshold_bytes, traffic_whitelist, traffic_period, ip_city_threshold, ip_whitelist, ip_period, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                traffic_threshold_bytes=excluded.traffic_threshold_bytes,
                traffic_whitelist=excluded.traffic_whitelist,
                traffic_period=excluded.traffic_period,
                ip_city_threshold=excluded.ip_city_threshold,
                ip_whitelist=excluded.ip_whitelist,
                ip_period=excluded.ip_period,
                updated_at=excluded.updated_at
            """,
 (
                xboard_user_id,
                current.get("traffic_threshold_bytes"),
                int(current.get("traffic_whitelist") or 0),
                current.get("traffic_period"),
                current.get("ip_city_threshold"),
                int(current.get("ip_whitelist") or 0),
                current.get("ip_period"),
                now_ts,
            ),
        )
    return alert_user_setting_sync(cache_path, xboard_user_id)

def alert_reset_setting_sync(cache_path: Path, xboard_user_id: int, alert_type: str) -> dict[str, Any]:
    if alert_type == "traffic":
        return alert_upsert_setting_sync(cache_path, xboard_user_id, traffic_threshold_bytes=None, traffic_period=None, traffic_whitelist=0)
    if alert_type == "ip":
        return alert_upsert_setting_sync(cache_path, xboard_user_id, ip_city_threshold=None, ip_period=None, ip_whitelist=0)
    raise ValueError("unknown alert type")

def alert_user_list_sync(cache_path: Path, alert_type: str, limit: int = 500) -> list[dict[str, Any]]:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        rows = conn.execute(
            """
            SELECT u.user_id, u.display_name, u.remarks, u.email,
                   s.traffic_threshold_bytes, s.traffic_whitelist, s.traffic_period,
                   s.ip_city_threshold, s.ip_whitelist, s.ip_period
            FROM users AS u
            LEFT JOIN alert_user_settings AS s ON s.user_id = u.user_id
            ORDER BY u.user_id ASC
            LIMIT ?
            """,
 (limit,),
        ).fetchall()
    result = []
    for row in rows:
        name = str(row["display_name"] or row["remarks"] or row["email"] or f"用户{row['user_id']}").strip()
        setting = dict(row)
        result.append({"user_id": int(row["user_id"]), "name": name, "setting_label": alert_setting_label(setting, alert_type, cache_path)})
    return result

def traffic_alert_rows_sync(cache_path: Path) -> list[dict[str, Any]]:
    init_cache(cache_path)
    now = datetime.now()
    global_period = alert_global_period_sync(cache_path, "traffic")
    global_threshold = alert_global_threshold_sync(cache_path, "traffic")
    with cache_connect(cache_path) as conn:
        users = conn.execute(
            """
            SELECT DISTINCT t.entity_id AS user_id, COALESCE(MAX(t.name), '用户' || t.entity_id) AS name,
                   s.traffic_threshold_bytes, s.traffic_whitelist, s.traffic_period
            FROM traffic_delta_samples AS t
            LEFT JOIN alert_user_settings AS s ON s.user_id = t.entity_id
            WHERE t.kind = 'user'
            GROUP BY t.entity_id
            """
        ).fetchall()
        alerts = []
        for row in users:
            if int(row["traffic_whitelist"] or 0):
                continue
            period = row["traffic_period"] or global_period
            start_ts, end_ts, period_label = alert_period_window(period, now)
            total_row = conn.execute(
                """
                SELECT COALESCE(SUM(delta), 0) AS total
                FROM traffic_delta_samples
                WHERE kind = 'user' AND entity_id = ? AND sampled_at BETWEEN ? AND ?
                """,
 (int(row["user_id"]), start_ts, end_ts),
            ).fetchone()
            threshold = int(row["traffic_threshold_bytes"] or global_threshold)
            total = int(total_row["total"] or 0)
            rule_type = "独立规则" if row["traffic_threshold_bytes"] is not None or row["traffic_period"] is not None else "默认规则"
            if total > threshold:
                alerts.append({"user_id": int(row["user_id"]), "name": str(row["name"] or ""), "total": total, "threshold": threshold, "period": period, "period_label": period_label, "rule_type": rule_type})
    return alerts

def ip_alert_rows_sync(cache_path: Path) -> list[dict[str, Any]]:
    init_cache(cache_path)
    now = datetime.now()
    global_period = alert_global_period_sync(cache_path, "ip")
    global_threshold = alert_global_threshold_sync(cache_path, "ip")
    with cache_connect(cache_path) as conn:
        users = conn.execute(
            """
            SELECT DISTINCT a.user_id, COALESCE(MAX(u.display_name), MAX(u.remarks), MAX(u.email), '用户' || a.user_id) AS name,
                   s.ip_city_threshold, s.ip_whitelist, s.ip_period
            FROM active_ip_records AS a
            LEFT JOIN users AS u ON u.user_id = a.user_id
            LEFT JOIN alert_user_settings AS s ON s.user_id = a.user_id
            WHERE a.ignored_at IS NULL
            GROUP BY a.user_id
            """
        ).fetchall()
        alerts = []
        for row in users:
            if int(row["ip_whitelist"] or 0):
                continue
            period = row["ip_period"] or global_period
            start_ts, end_ts, period_label = alert_period_window(period, now)
            detail = conn.execute(
                """
                SELECT COUNT(DISTINCT COALESCE(NULLIF(g.stat_area_key, ''), NULLIF(g.city, ''), NULLIF(g.region, ''), NULLIF(g.country, ''))) AS city_count,
                       GROUP_CONCAT(DISTINCT COALESCE(NULLIF(g.stat_area_name, ''), NULLIF(g.city, ''), NULLIF(g.region, ''), NULLIF(g.country, ''))) AS cities
                FROM active_ip_records AS a
                LEFT JOIN ip_geo_cache AS g ON g.ip = a.ip
                WHERE a.user_id = ? AND a.ignored_at IS NULL AND a.last_seen_at BETWEEN ? AND ?
                """,
 (int(row["user_id"]), start_ts, end_ts),
            ).fetchone()
            threshold = int(row["ip_city_threshold"] or global_threshold)
            city_count = int(detail["city_count"] or 0)
            rule_type = "独立规则" if row["ip_city_threshold"] is not None or row["ip_period"] is not None else "默认规则"
            if city_count > threshold:
                cities = [c for c in str(detail["cities"] or "").split(",") if c]
                alerts.append({"user_id": int(row["user_id"]), "name": str(row["name"] or ""), "city_count": city_count, "threshold": threshold, "period": period, "period_label": period_label, "cities": cities[:12], "rule_type": rule_type})
    return alerts

def alert_effective_rule_detail_for_user_sync(cache_path: Path, alert_type: str, user_id: int) -> tuple[str, str, int, str]:
    init_cache(cache_path)
    global_period = alert_global_period_sync(cache_path, alert_type)
    global_threshold = alert_global_threshold_sync(cache_path, alert_type)
    with cache_connect(cache_path) as conn:
        row = conn.execute("SELECT * FROM alert_user_settings WHERE user_id = ?", (int(user_id),)).fetchone()
    if alert_type == "traffic":
        custom_threshold = row["traffic_threshold_bytes"] if row else None
        custom_period = row["traffic_period"] if row else None
    else:
        custom_threshold = row["ip_city_threshold"] if row else None
        custom_period = row["ip_period"] if row else None
    period = custom_period or global_period
    threshold = int(custom_threshold or global_threshold)
    rule_type = "独立规则" if custom_threshold is not None or custom_period is not None else "默认规则"
    return period, alert_period_label(period), threshold, rule_type

def alert_effective_rule_for_user_sync(cache_path: Path, alert_type: str, user_id: int) -> tuple[str, int, str]:
    _, period_label, threshold, rule_type = alert_effective_rule_detail_for_user_sync(cache_path, alert_type, user_id)
    return period_label, threshold, rule_type

def current_traffic_alert_value_for_user_sync(cache_path: Path, user_id: int, period: str) -> int:
    start_ts, end_ts, _ = alert_period_window(period, datetime.now())
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(delta), 0) AS total
            FROM traffic_delta_samples
            WHERE kind = 'user' AND entity_id = ? AND sampled_at BETWEEN ? AND ?
            """,
            (int(user_id), start_ts, end_ts),
        ).fetchone()
    return int(row["total"] or 0) if row else 0

def current_ip_alert_detail_for_user_sync(cache_path: Path, user_id: int, period: str) -> tuple[int, list[str]]:
    start_ts, end_ts, _ = alert_period_window(period, datetime.now())
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(DISTINCT COALESCE(NULLIF(g.stat_area_key, ''), NULLIF(g.city, ''), NULLIF(g.region, ''), NULLIF(g.country, ''))) AS city_count,
                   GROUP_CONCAT(DISTINCT COALESCE(NULLIF(g.stat_area_name, ''), NULLIF(g.city, ''), NULLIF(g.region, ''), NULLIF(g.country, ''))) AS cities
            FROM active_ip_records AS a
            LEFT JOIN ip_geo_cache AS g ON g.ip = a.ip
            WHERE a.user_id = ? AND a.ignored_at IS NULL AND a.last_seen_at BETWEEN ? AND ?
            """,
            (int(user_id), start_ts, end_ts),
        ).fetchone()
    if not row:
        return 0, []
    cities = [c for c in str(row["cities"] or "").split(",") if c]
    return int(row["city_count"] or 0), cities[:12]

def alert_state_get_sync(cache_path: Path, key: str) -> str | None:
    state = get_collector_state_sync(cache_path, key)
    return state[0] if state else None

def alert_state_set_sync(cache_path: Path, key: str, value: str) -> None:
    init_cache(cache_path)
    now_ts = int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        set_collector_state(conn, key, value, now_ts)

def alert_state_delete_sync(cache_path: Path, key: str) -> None:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        conn.execute("DELETE FROM collector_state WHERE key = ?", (key,))

def traffic_report_sent_key(kind: str, period_start: int, period_end: int, chat_id: str) -> str:
    return f"traffic_report_sent:{kind}:{period_start}:{period_end}:{chat_id}"

def mark_traffic_report_sent_sync(cache_path: Path, kind: str, period_start: int, period_end: int, chat_id: str) -> None:
    init_cache(cache_path)
    now_ts = int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        set_collector_state(
            conn,
            traffic_report_sent_key(kind, period_start, period_end, chat_id),
            "1",
            now_ts,
        )

def collector_health_key(service: str) -> str:
    return f"collector_health:{service}"

def set_collector_health_status_sync(cache_path: Path, service: str, ok: bool, detail: str = "") -> tuple[str | None, str]:
    """Store health status and return (previous_status, current_status)."""
    init_cache(cache_path)
    now_ts = int(datetime.now().timestamp())
    status = "ok" if ok else "fail"
    payload = json.dumps({"status": status, "detail": detail, "updated_at": now_ts}, ensure_ascii=False)
    with cache_connect(cache_path) as conn:
        row = conn.execute(
            "SELECT value FROM collector_state WHERE key = ?",
 (collector_health_key(service),),
        ).fetchone()
        previous_status: str | None = None
        if row:
            try:
                previous_status = str(json.loads(str(row["value"] or "{}")).get("status") or "") or None
            except json.JSONDecodeError:
                previous_status = str(row["value"] or "") or None
        set_collector_state(conn, collector_health_key(service), payload, now_ts)
    return previous_status, status

def traffic_report_already_sent_sync(cache_path: Path, kind: str, period_start: int, period_end: int, chat_id: str) -> bool:
    return get_collector_state_sync(cache_path, traffic_report_sent_key(kind, period_start, period_end, chat_id)) is not None

def get_stats_floor_ts_sync(cache_path: Path) -> int | None:
    state = get_collector_state_sync(cache_path, "stats_floor_at")
    if not state:
        return None
    try:
        value = int(state[0])
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None

def effective_cache_cutoff_ts_sync(cache_path: Path, retention_days: int) -> int:
    if retention_days <= 0:
        return get_stats_floor_ts_sync(cache_path) or 0
    retention_cutoff = int((datetime.now() - timedelta(days=retention_days)).timestamp())
    stats_floor = get_stats_floor_ts_sync(cache_path)
    return max(retention_cutoff, stats_floor or 0)

def cache_retention_days_sync(cache_path: Path) -> int:
    value = alert_state_get_sync(cache_path, "cache_retention_days")
    if value is not None:
        try:
            parsed = int(value)
            if parsed >= 0:
                return parsed
        except ValueError:
            pass
    return DEFAULT_CACHE_RETENTION_DAYS

def cache_retention_option_key(days: int) -> str:
    for key, (option_days, _) in CACHE_RETENTION_OPTIONS.items():
        if int(days) == int(option_days):
            return key
    return "1m"

def cache_retention_label(days: int) -> str:
    return CACHE_RETENTION_OPTIONS.get(cache_retention_option_key(days), CACHE_RETENTION_OPTIONS["1m"])[1]

def cache_retention_cutoff_ts(days: int) -> int:
    if days <= 0:
        return 0
    return int((datetime.now() - timedelta(days=days)).timestamp())

def cache_retention_preview_sync(cache_path: Path, days: int) -> dict[str, int]:
    init_cache(cache_path)
    cutoff_ts = cache_retention_cutoff_ts(days)
    with cache_connect(cache_path) as conn:
        if cutoff_ts <= 0:
            counts = {"traffic_delta_samples": 0, "traffic_sample_gaps": 0, "traffic_ranges": 0, "active_ip_records": 0, "ip_geo_cache": 0}
        else:
            counts = {
                "traffic_delta_samples": int(conn.execute("SELECT COUNT(*) FROM traffic_delta_samples WHERE sampled_at < ?", (cutoff_ts,)).fetchone()[0] or 0),
                "traffic_sample_gaps": int(conn.execute("SELECT COUNT(*) FROM traffic_sample_gaps WHERE gap_end_at < ?", (cutoff_ts,)).fetchone()[0] or 0),
                "traffic_ranges": int(conn.execute("SELECT COUNT(*) FROM traffic_ranges WHERE end_ts < ?", (cutoff_ts,)).fetchone()[0] or 0),
                "active_ip_records": int(conn.execute("SELECT COUNT(*) FROM active_ip_records WHERE last_seen_at < ?", (cutoff_ts,)).fetchone()[0] or 0),
                "ip_geo_cache": int(conn.execute("""
                    SELECT COUNT(*) FROM ip_geo_cache
                    WHERE ip NOT IN (SELECT DISTINCT ip FROM active_ip_records WHERE last_seen_at >= ?)
                      AND (queried_at = 0 OR queried_at < ?)
                """, (cutoff_ts, cutoff_ts)).fetchone()[0] or 0),
            }
        counts["cutoff_ts"] = cutoff_ts
        return counts

def cache_retention_set_and_prune_sync(cache_path: Path, days: int) -> dict[str, int]:
    init_cache(cache_path)
    now_ts = int(datetime.now().timestamp())
    cutoff_ts = cache_retention_cutoff_ts(days)
    with cache_connect(cache_path) as conn:
        if cutoff_ts <= 0:
            counts = {"traffic_delta_samples": 0, "traffic_sample_gaps": 0, "traffic_ranges": 0, "active_ip_records": 0, "ip_geo_cache": 0}
        else:
            counts = {
                "traffic_delta_samples": int(conn.execute("SELECT COUNT(*) FROM traffic_delta_samples WHERE sampled_at < ?", (cutoff_ts,)).fetchone()[0] or 0),
                "traffic_sample_gaps": int(conn.execute("SELECT COUNT(*) FROM traffic_sample_gaps WHERE gap_end_at < ?", (cutoff_ts,)).fetchone()[0] or 0),
                "traffic_ranges": int(conn.execute("SELECT COUNT(*) FROM traffic_ranges WHERE end_ts < ?", (cutoff_ts,)).fetchone()[0] or 0),
                "active_ip_records": int(conn.execute("SELECT COUNT(*) FROM active_ip_records WHERE last_seen_at < ?", (cutoff_ts,)).fetchone()[0] or 0),
                "ip_geo_cache": int(conn.execute("""
                    SELECT COUNT(*) FROM ip_geo_cache
                    WHERE ip NOT IN (SELECT DISTINCT ip FROM active_ip_records WHERE last_seen_at >= ?)
                      AND (queried_at = 0 OR queried_at < ?)
                """, (cutoff_ts, cutoff_ts)).fetchone()[0] or 0),
            }
            conn.execute("DELETE FROM traffic_delta_samples WHERE sampled_at < ?", (cutoff_ts,))
            conn.execute("DELETE FROM traffic_sample_gaps WHERE gap_end_at < ?", (cutoff_ts,))
            conn.execute("DELETE FROM traffic_ranges WHERE end_ts < ?", (cutoff_ts,))
            conn.execute("DELETE FROM active_ip_records WHERE last_seen_at < ?", (cutoff_ts,))
            conn.execute("""
                DELETE FROM ip_geo_cache
                WHERE ip NOT IN (SELECT DISTINCT ip FROM active_ip_records WHERE last_seen_at >= ?)
                  AND (queried_at = 0 OR queried_at < ?)
            """, (cutoff_ts, cutoff_ts))
        set_collector_state(conn, "cache_retention_days", str(int(days)), now_ts)
        set_collector_state(conn, "last_cleanup_at", str(now_ts), now_ts)
    counts["cutoff_ts"] = cutoff_ts
    return counts

def prune_stats_before_sync(cache_path: Path, floor_ts: int) -> dict[str, int]:
    """Set local statistics floor and delete cached rows before it."""
    init_cache(cache_path)
    now_ts = int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        counts = {
            "traffic_delta_samples": int(conn.execute("SELECT COUNT(*) FROM traffic_delta_samples WHERE sampled_at < ?", (floor_ts,)).fetchone()[0] or 0),
            "traffic_sample_gaps": int(conn.execute("SELECT COUNT(*) FROM traffic_sample_gaps WHERE gap_end_at < ?", (floor_ts,)).fetchone()[0] or 0),
            "traffic_ranges": int(conn.execute("SELECT COUNT(*) FROM traffic_ranges WHERE end_ts < ?", (floor_ts,)).fetchone()[0] or 0),
            "active_ip_records": int(conn.execute("SELECT COUNT(*) FROM active_ip_records WHERE last_seen_at < ?", (floor_ts,)).fetchone()[0] or 0),
        }
        conn.execute("DELETE FROM traffic_delta_samples WHERE sampled_at < ?", (floor_ts,))
        conn.execute("DELETE FROM traffic_sample_gaps WHERE gap_end_at < ?", (floor_ts,))
        conn.execute("DELETE FROM traffic_ranges WHERE end_ts < ?", (floor_ts,))
        conn.execute("DELETE FROM active_ip_records WHERE last_seen_at < ?", (floor_ts,))
        set_collector_state(conn, "stats_floor_at", str(floor_ts), now_ts)
    return counts

def preview_prune_stats_before_sync(cache_path: Path, floor_ts: int) -> dict[str, int]:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        return {
            "traffic_delta_samples": int(conn.execute("SELECT COUNT(*) FROM traffic_delta_samples WHERE sampled_at < ?", (floor_ts,)).fetchone()[0] or 0),
            "traffic_sample_gaps": int(conn.execute("SELECT COUNT(*) FROM traffic_sample_gaps WHERE gap_end_at < ?", (floor_ts,)).fetchone()[0] or 0),
            "traffic_ranges": int(conn.execute("SELECT COUNT(*) FROM traffic_ranges WHERE end_ts < ?", (floor_ts,)).fetchone()[0] or 0),
            "active_ip_records": int(conn.execute("SELECT COUNT(*) FROM active_ip_records WHERE last_seen_at < ?", (floor_ts,)).fetchone()[0] or 0),
        }

def preview_clear_active_ip_records_sync(cache_path: Path) -> dict[str, int]:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS records,
                   COUNT(DISTINCT user_id) AS users,
                   COUNT(DISTINCT ip) AS ips,
                   MIN(first_seen_at) AS first_seen,
                   MAX(last_seen_at) AS last_seen
            FROM active_ip_records
            """
        ).fetchone() or {}
        geo_rows = conn.execute(
            """
            SELECT COUNT(*) AS geo_records
            FROM ip_geo_cache
            WHERE ip IN (SELECT DISTINCT ip FROM active_ip_records)
            """
        ).fetchone() or {}
    return {
        "records": int(row["records"] or 0),
        "users": int(row["users"] or 0),
        "ips": int(row["ips"] or 0),
        "geo_records": int(geo_rows["geo_records"] or 0),
        "first_seen": int(row["first_seen"] or 0),
        "last_seen": int(row["last_seen"] or 0),
    }

def clear_active_ip_records_sync(cache_path: Path) -> dict[str, int]:
    init_cache(cache_path)
    stats = preview_clear_active_ip_records_sync(cache_path)
    now_ts = int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        conn.execute(
            """
            DELETE FROM ip_geo_cache
            WHERE ip IN (SELECT DISTINCT ip FROM active_ip_records)
            """
        )
        conn.execute("DELETE FROM active_ip_records")
        set_collector_state(conn, "last_active_ip_records_cleared_at", str(now_ts), now_ts)
    return stats

def reset_local_cache_sync(cache_path: Path) -> dict[str, int]:
    """Clear local Bot cache/samples while preserving UI preferences."""
    init_cache(cache_path)
    now_ts = int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        counts = {
            "active_ip_records": int(conn.execute("SELECT COUNT(*) FROM active_ip_records").fetchone()[0] or 0),
            "ip_geo_cache": int(conn.execute("SELECT COUNT(*) FROM ip_geo_cache").fetchone()[0] or 0),
            "users": int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] or 0),
            "traffic_delta_samples": int(conn.execute("SELECT COUNT(*) FROM traffic_delta_samples").fetchone()[0] or 0),
            "traffic_sample_gaps": int(conn.execute("SELECT COUNT(*) FROM traffic_sample_gaps").fetchone()[0] or 0),
            "traffic_ranges": int(conn.execute("SELECT COUNT(*) FROM traffic_ranges").fetchone()[0] or 0),
            "pinned_dashboard_messages": int(conn.execute("SELECT COUNT(*) FROM pinned_dashboard_messages").fetchone()[0] or 0),
        }
        for table in (
            "active_ip_records",
            "ip_geo_cache",
            "users",
            "traffic_delta_samples",
            "traffic_sample_gaps",
            "traffic_ranges",
            "pinned_dashboard_messages",
        ):
            conn.execute(f"DELETE FROM {table}")
        conn.execute(
            "DELETE FROM collector_state WHERE key IN ('first_collect_at', 'last_collect_at', 'last_traffic_sample_at', 'stats_floor_at', 'last_active_ip_records_cleared_at', 'initialization_completed_at', 'initialization_result')"
        )
        set_collector_state(conn, "cache_reset_at", str(now_ts), now_ts)
        set_collector_state(conn, "initialization_status", "running", now_ts)
        set_collector_state(conn, "initialization_started_at", str(now_ts), now_ts)
        set_collector_state(conn, "initialization_reason", "cache_reset", now_ts)
    return counts

def list_all_cached_user_buttons_sync(cache_path: Path) -> list[tuple[int, str]]:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        rows = conn.execute(
            """
            SELECT user_id, display_name, remarks, email
            FROM users
            ORDER BY user_id ASC
            """
        ).fetchall()
    return [(int(row["user_id"]), cached_user_button_label(row, int(row["user_id"]))) for row in rows]

def preview_clear_user_ip_records_multi_sync(cache_path: Path, user_ids: list[int]) -> dict[str, Any]:
    init_cache(cache_path)
    clean_ids = sorted({int(uid) for uid in user_ids if int(uid) > 0})
    if not clean_ids:
        return {"users": 0, "records": 0, "ips": 0, "first_seen": None, "last_seen": None, "labels": []}
    placeholders = ",".join("?" for _ in clean_ids)
    with cache_connect(cache_path) as conn:
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS records, COUNT(DISTINCT ip) AS ips,
                   MIN(first_seen_at) AS first_seen, MAX(last_seen_at) AS last_seen
            FROM active_ip_records
            WHERE user_id IN ({placeholders}) AND ignored_at IS NULL
            """,
            clean_ids,
        ).fetchone()
        user_rows = conn.execute(
            f"""
            SELECT user_id, display_name, remarks, email
            FROM users
            WHERE user_id IN ({placeholders})
            ORDER BY user_id ASC
            """,
            clean_ids,
        ).fetchall()
    labels = [cached_user_button_label(r, int(r["user_id"])) for r in user_rows]
    return {
        "users": len(clean_ids),
        "records": int(row["records"] or 0),
        "ips": int(row["ips"] or 0),
        "first_seen": int(row["first_seen"] or 0) or None,
        "last_seen": int(row["last_seen"] or 0) or None,
        "labels": labels,
    }

def clear_user_ip_records_multi_sync(cache_path: Path, user_ids: list[int]) -> dict[str, Any]:
    stats = preview_clear_user_ip_records_multi_sync(cache_path, user_ids)
    clean_ids = sorted({int(uid) for uid in user_ids if int(uid) > 0})
    if not clean_ids:
        return stats
    placeholders = ",".join("?" for _ in clean_ids)
    now_ts = int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        cursor = conn.execute(
            f"""
            UPDATE active_ip_records
            SET ignored_at = ?, ignore_reason = ?, ignore_note = ?
            WHERE user_id IN ({placeholders}) AND ignored_at IS NULL
            """,
            [now_ts, "debug_reset_user_ip", "调试功能：清空用户 IP 记录", *clean_ids],
        )
        active_ips = int(conn.execute("SELECT COUNT(*) FROM active_ip_records WHERE ignored_at IS NULL").fetchone()[0] or 0)
        previous_row = conn.execute("SELECT value FROM collector_state WHERE key = ?", ("ip_alert_active_users",)).fetchone()
        previous_raw = str(previous_row["value"]) if previous_row else "{}"
        try:
            previous = json.loads(previous_raw)
            if isinstance(previous, dict):
                for user_id_value in clean_ids:
                    previous.pop(str(user_id_value), None)
                set_collector_state(conn, "ip_alert_active_users", json.dumps(previous, sort_keys=True), now_ts)
        except (TypeError, ValueError):
            pass
        set_collector_state(conn, "last_active_ip_records_cleared_at", str(now_ts), now_ts)
    stats["remaining_active_ips"] = active_ips
    stats["ignored"] = int(cursor.rowcount or 0)
    return stats

def get_cache_counts_sync(cache_path: Path) -> dict[str, int]:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        active_ip_records = int(conn.execute("SELECT COUNT(*) FROM active_ip_records WHERE ignored_at IS NULL").fetchone()[0] or 0)
        active_ips = int(conn.execute("SELECT COUNT(DISTINCT ip) FROM active_ip_records WHERE ignored_at IS NULL").fetchone()[0] or 0)
        users = int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] or 0)
        geo_total = int(conn.execute("SELECT COUNT(*) FROM ip_geo_cache").fetchone()[0] or 0)
        traffic_samples = int(conn.execute("SELECT COUNT(*) FROM traffic_delta_samples").fetchone()[0] or 0)
        pinned_dashboards = int(conn.execute("SELECT COUNT(*) FROM pinned_dashboard_messages").fetchone()[0] or 0)
    return {
        "active_ips": active_ips,
        "active_ip_records": active_ip_records,
        "users": users,
        "geo_total": geo_total,
        "traffic_samples": traffic_samples,
        "pinned_dashboards": pinned_dashboards,
    }

def upsert_cache_records(cache_path: Path, records: list[tuple[int, str, int, int, str]], retention_days: int) -> set[int]:
    now_ts = int(datetime.now().timestamp())
    cutoff_ts = effective_cache_cutoff_ts_sync(cache_path, retention_days)
    user_ids = {user_id for user_id, *_ in records}
    ips = {ip for _, ip, *_ in records}
    with cache_connect(cache_path) as conn:
        first_state = conn.execute("SELECT value FROM collector_state WHERE key = ?", ("first_collect_at",)).fetchone()
        if not first_state:
            set_collector_state(conn, "first_collect_at", str(now_ts), now_ts)
        conn.executemany(
            """
            INSERT INTO active_ip_records(user_id, ip, first_seen_at, last_seen_at, last_ttl, source_key)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, ip) DO UPDATE SET
                last_seen_at = MAX(active_ip_records.last_seen_at, excluded.last_seen_at),
                last_ttl = excluded.last_ttl,
                source_key = excluded.source_key
            """,
 ((user_id, ip, last_seen_ts, last_seen_ts, ttl, source_key) for user_id, ip, last_seen_ts, ttl, source_key in records),
        )
        conn.executemany(
            """
            INSERT INTO ip_geo_cache(ip, queried_at)
            VALUES (?, 0)
            ON CONFLICT(ip) DO NOTHING
            """,
 ((ip,) for ip in ips),
        )
        apply_ignored_rules_conn(conn, now_ts)
        conn.execute("DELETE FROM active_ip_records WHERE last_seen_at < ?", (cutoff_ts,))
        set_collector_state(conn, "last_collect_at", str(now_ts), now_ts)
        set_collector_state(conn, "last_collect_attempt_at", str(now_ts), now_ts)
        set_collector_state(conn, "last_cleanup_at", str(now_ts), now_ts)
    return user_ids

def upsert_cache_users(cache_path: Path, mysql_cfg: MySQLConfig, user_ids: set[int]) -> None:
    if not user_ids:
        return
    names = fetch_user_display_details_sync(mysql_cfg, user_ids)
    now_ts = int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        conn.executemany(
            """
            INSERT INTO users(user_id, display_name, remarks, email, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                display_name=excluded.display_name,
                remarks=excluded.remarks,
                email=excluded.email,
                updated_at=excluded.updated_at
            """,
 ((user_id, row["display_name"], row["remarks"], row["email"], now_ts) for user_id, row in names.items()),
        )

def pinned_dashboard_set_sync(cache_path: Path, kind: str, chat_id: str, message_id: int, is_pinned: bool) -> None:
    init_cache(cache_path)
    now_ts = int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        conn.execute(
            """
            INSERT INTO pinned_dashboard_messages(kind, chat_id, message_id, is_pinned, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(kind, chat_id) DO UPDATE SET
                message_id=excluded.message_id,
                is_pinned=excluded.is_pinned,
                updated_at=excluded.updated_at
            """,
 (kind, chat_id, message_id, 1 if is_pinned else 0, now_ts),
        )

def pinned_dashboard_delete_sync(cache_path: Path, kind: str, chat_id: str) -> None:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        conn.execute("DELETE FROM pinned_dashboard_messages WHERE kind = ? AND chat_id = ?", (kind, chat_id))

def pinned_dashboard_all_sync(cache_path: Path) -> list[dict[str, Any]]:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        rows = conn.execute(
            """
            SELECT kind, chat_id, message_id, is_pinned, updated_at
            FROM pinned_dashboard_messages
            ORDER BY chat_id ASC, kind ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]

def pinned_dashboard_delete_message_sync(cache_path: Path, chat_id: str, message_id: int) -> None:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        conn.execute("DELETE FROM pinned_dashboard_messages WHERE chat_id = ? AND message_id = ?", (chat_id, message_id))

def auto_delete_message_set_sync(cache_path: Path, chat_id: str, message_id: int, is_pinned: bool) -> None:
    init_cache(cache_path)
    now_ts = int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        conn.execute(
            """
            INSERT INTO dashboard_auto_delete_messages(chat_id, message_id, is_pinned, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id, message_id) DO UPDATE SET
                is_pinned=excluded.is_pinned,
                updated_at=excluded.updated_at
            """,
 (chat_id, message_id, 1 if is_pinned else 0, now_ts),
        )

def auto_delete_message_is_pinned_sync(cache_path: Path, chat_id: str, message_id: int) -> bool:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        row = conn.execute(
            "SELECT is_pinned FROM dashboard_auto_delete_messages WHERE chat_id = ? AND message_id = ?",
 (chat_id, message_id),
        ).fetchone()
    return bool(row and int(row["is_pinned"] or 0))

def auto_delete_message_delete_sync(cache_path: Path, chat_id: str, message_id: int) -> None:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        conn.execute("DELETE FROM dashboard_auto_delete_messages WHERE chat_id = ? AND message_id = ?", (chat_id, message_id))

def clear_message_tracking_for_chat_sync(cache_path: Path, chat_id: str) -> None:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        conn.execute("DELETE FROM pinned_dashboard_messages WHERE chat_id = ?", (chat_id,))
        conn.execute("DELETE FROM dashboard_auto_delete_messages WHERE chat_id = ?", (chat_id,))

def auto_delete_due_messages_sync(cache_path: Path, older_than_ts: int) -> list[dict[str, Any]]:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        rows = conn.execute(
            """
            SELECT chat_id, message_id, is_pinned, updated_at
            FROM dashboard_auto_delete_messages
            WHERE is_pinned = 0 AND updated_at <= ?
            ORDER BY updated_at ASC
            """,
 (older_than_ts,),
        ).fetchall()
    return [dict(row) for row in rows]

def sample_traffic_deltas_sync(cache_path: Path, cfg: MySQLConfig) -> tuple[int, int, int, int, int, int]:
    """Store per-minute traffic deltas. Returns (users, nodes, deltas, gap_seconds, previous_ts, current_ts)."""
    init_cache(cache_path)
    now_ts = int(datetime.now().timestamp())
    user_rows, node_rows = collect_traffic_counters_sync(cfg)
    delta_rows = 0
    gap_seconds = 0
    previous_ts = 0
    # 保留周期由 Bot 参数配置管理；同时尊重人工设置的统计起始点。
    retention_days = cache_retention_days_sync(cache_path)
    retention_cutoff_ts = cache_retention_cutoff_ts(retention_days)
    stats_floor_ts = get_stats_floor_ts_sync(cache_path)
    cutoff_ts = max(retention_cutoff_ts, stats_floor_ts or 0)
    with cache_connect(cache_path) as conn:
        previous_sample = conn.execute(
            "SELECT value FROM collector_state WHERE key = ?",
 ("last_traffic_sample_at",),
        ).fetchone()
        if previous_sample:
            previous_ts = int(previous_sample["value"] or 0)
            gap_seconds = max(0, now_ts - previous_ts)
            if gap_seconds > TRAFFIC_SAMPLE_GAP_TOLERANCE_SECONDS:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO traffic_sample_gaps(gap_start_at, gap_end_at, gap_seconds, detected_at)
                    VALUES (?, ?, ?, ?)
                    """,
 (previous_ts, now_ts, gap_seconds, now_ts),
                )
        for kind, rows in (("user", user_rows), ("node", node_rows)):
            for row in rows:
                entity_id = int(row.get("entity_id") or 0)
                if entity_id <= 0:
                    continue
                name = str(row.get("name") or f"{kind}{entity_id}").strip().replace("\n", " ")[:160]
                total = max(0, int(row.get("total") or 0))
                previous = conn.execute(
                    "SELECT total FROM traffic_counter_snapshots WHERE kind = ? AND entity_id = ?",
 (kind, entity_id),
                ).fetchone()
                if previous is None:
                    delta = 0
                else:
                    previous_total = int(previous["total"] or 0)
                    delta = total - previous_total if total >= previous_total else total
                if delta > 0:
                    conn.execute(
                        """
                        INSERT INTO traffic_delta_samples(sampled_at, kind, entity_id, name, delta)
                        VALUES (?, ?, ?, ?, ?)
                        """,
 (now_ts, kind, entity_id, name, delta),
                    )
                    delta_rows += 1
                conn.execute(
                    """
                    INSERT INTO traffic_counter_snapshots(kind, entity_id, name, total, sampled_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(kind, entity_id) DO UPDATE SET
                        name=excluded.name,
                        total=excluded.total,
                        sampled_at=excluded.sampled_at
                    """,
 (kind, entity_id, name, total, now_ts),
                )
        conn.execute("DELETE FROM traffic_delta_samples WHERE sampled_at < ?", (cutoff_ts,))
        conn.execute("DELETE FROM traffic_sample_gaps WHERE gap_end_at < ?", (cutoff_ts,))
        set_collector_state(conn, "last_traffic_sample_at", str(now_ts), now_ts)
    return len(user_rows), len(node_rows), delta_rows, gap_seconds, previous_ts, now_ts

def earliest_traffic_sample_at_sync(cache_path: Path) -> int | None:
    init_cache(cache_path)
    floor_ts = get_stats_floor_ts_sync(cache_path) or 0
    with cache_connect(cache_path) as conn:
        row = conn.execute(
            """
            SELECT MIN(sampled_at) AS first_sample FROM (
                SELECT sampled_at FROM traffic_delta_samples
                UNION ALL
                SELECT sampled_at FROM traffic_counter_snapshots
            )
            WHERE sampled_at >= ?
            """
            , (floor_ts,)
        ).fetchone() or {}
    return int(row["first_sample"]) if row and row["first_sample"] is not None else None

def query_traffic_deltas_range_from_cache_sync(
    cache_path: Path,
    start_ts: int,
    end_ts: int,
    limit: int = 10,
    dimension: str = "combined",
) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]], int | None]:
    init_cache(cache_path)
    safe_limit = max(1, min(limit, 50))
    dimension = dimension if dimension in {"combined", "users", "nodes"} else "combined"
    total_kind = "node" if dimension == "nodes" else "user"
    with cache_connect(cache_path) as conn:
        total_row = conn.execute(
            """
            SELECT COALESCE(SUM(delta), 0) AS total
            FROM traffic_delta_samples
            WHERE sampled_at BETWEEN ? AND ? AND kind = ?
            """,
 (start_ts, end_ts, total_kind),
        ).fetchone() or {}
        user_rows = []
        node_rows = []
        if dimension in {"combined", "users"}:
            user_rows = conn.execute(
                """
                SELECT entity_id, COALESCE(MAX(name), CONCAT('用户', entity_id)) AS name, SUM(delta) AS total
                FROM traffic_delta_samples
                WHERE sampled_at BETWEEN ? AND ? AND kind = 'user'
                GROUP BY entity_id
                ORDER BY total DESC
                LIMIT ?
                """.replace("CONCAT('用户', entity_id)", "'用户' || entity_id"),
 (start_ts, end_ts, safe_limit),
            ).fetchall()
        if dimension in {"combined", "nodes"}:
            node_rows = conn.execute(
                """
                SELECT entity_id, COALESCE(MAX(name), '节点' || entity_id) AS name, SUM(delta) AS total
                FROM traffic_delta_samples
                WHERE sampled_at BETWEEN ? AND ? AND kind = 'node'
                GROUP BY entity_id
                ORDER BY total DESC
                LIMIT ?
                """,
 (start_ts, end_ts, safe_limit),
            ).fetchall()
    return int(total_row["total"] or 0), [dict(r) for r in user_rows], [dict(r) for r in node_rows], earliest_traffic_sample_at_sync(cache_path)

def traffic_sample_gap_warning_for_range_sync(cache_path: Path, start_ts: int, end_ts: int, period_label: str) -> str | None:
    """Return a warning only when a sampling gap crosses a stats boundary.

    Traffic totals are calculated from cumulative counter deltas. A gap fully
    inside the selected period is normally captured by the next successful
    sample, so warning on every overlap is noisy and misleading. Boundary-crossing
    gaps can shift traffic into or out of the selected window, so only those are
    surfaced.
    """
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        rows = conn.execute(
            """
            SELECT gap_start_at, gap_end_at, gap_seconds
            FROM traffic_sample_gaps
            WHERE gap_start_at < ? AND gap_end_at > ?
            ORDER BY gap_seconds DESC, gap_end_at DESC
            """,
 (end_ts, start_ts),
        ).fetchall()
    boundary_rows = [
        row for row in rows
        if int(row["gap_start_at"] or 0) < start_ts < int(row["gap_end_at"] or 0)
        or int(row["gap_start_at"] or 0) < end_ts < int(row["gap_end_at"] or 0)
    ]
    if not boundary_rows:
        return None

    longest = max(int(row["gap_seconds"] or 0) for row in boundary_rows)
    first_start = min(int(row["gap_start_at"] or 0) for row in boundary_rows)
    last_end = max(int(row["gap_end_at"] or 0) for row in boundary_rows)
    if len(boundary_rows) == 1:
        gap_text = format_duration(longest)
    else:
        gap_text = f"共 {len(boundary_rows)} 次，最长 {format_duration(longest)}"
    return (
        f"⚠️ 统计边界附近存在采样中断 ({gap_text})，"
        f"时段约 {format_timestamp(first_start)} - {format_timestamp(last_end)}；"
        "由于累计值可能被记入相邻窗口，本周期流量可能存在边界偏差。"
    )

def ip_alert_row_for_user_sync(cache_path: Path, xboard_user_id: int) -> dict[str, Any] | None:
    for row in ip_alert_rows_sync(cache_path):
        if int(row.get("user_id") or 0) == int(xboard_user_id):
            return row
    return None

def traffic_range_kind_from_cache_sync(cache_path: Path, kind: str) -> dict[str, Any] | None:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        row = conn.execute(
            "SELECT kind, start_ts, end_ts, label, created_at FROM traffic_ranges WHERE kind = ?",
 (kind,),
        ).fetchone()
    return dict(row) if row else None

def save_traffic_range_sync(cache_path: Path, kind: str, start_ts: int, end_ts: int, label: str) -> None:
    init_cache(cache_path)
    now_ts = int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        conn.execute(
            """
            INSERT INTO traffic_ranges(kind, start_ts, end_ts, label, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(kind) DO UPDATE SET
                start_ts=excluded.start_ts,
                end_ts=excluded.end_ts,
                label=excluded.label,
                created_at=excluded.created_at
            """,
 (kind, start_ts, end_ts, label, now_ts),
        )

def make_range_kind(start_ts: int, end_ts: int, label: str) -> str:
    digest = hashlib.sha1(f"{start_ts}:{end_ts}:{label}".encode("utf-8")).hexdigest()[:12]
    return f"range_{digest}"

def traffic_dimension_from_kind(kind: str) -> str:
    if kind.startswith("users_"):
        return "users"
    if kind.startswith("nodes_"):
        return "nodes"
    return "combined"

def traffic_base_kind(kind: str) -> str:
    if kind.startswith("users_"):
        base = kind.removeprefix("users_")
    elif kind.startswith("nodes_"):
        base = kind.removeprefix("nodes_")
    else:
        base = kind
    legacy_periods = {"1h": "preset_1h", "24h": "preset_24h", "7d": "preset_7d", "30d": "preset_30d"}
    return legacy_periods.get(base, base)

def traffic_kind_for_dimension(dimension: str, base_kind: str) -> str:
    dimension = dimension if dimension in {"combined", "users", "nodes"} else "combined"
    if dimension == "users":
        return f"users_{base_kind}"
    if dimension == "nodes":
        return f"nodes_{base_kind}"
    return base_kind

def upsert_all_cache_users(cache_path: Path, mysql_cfg: MySQLConfig) -> None:
    names = fetch_all_user_display_details_sync(mysql_cfg)
    if not names:
        return
    now_ts = int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        for user_id, row in names.items():
            conn.execute(
                """
                INSERT INTO users(user_id, display_name, remarks, email, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    remarks=excluded.remarks,
                    email=excluded.email,
                    updated_at=excluded.updated_at
                """,
 (user_id, row["display_name"], row["remarks"], row["email"], now_ts),
            )

def earliest_cache_collect_at_sync(cache_path: Path) -> int | None:
    init_cache(cache_path)
    floor_ts = get_stats_floor_ts_sync(cache_path) or 0
    with cache_connect(cache_path) as conn:
        row = conn.execute(
            """
            SELECT MIN(ts) AS first_ts FROM (
                SELECT first_seen_at AS ts FROM active_ip_records WHERE first_seen_at > 0
                UNION ALL
                SELECT updated_at AS ts FROM users WHERE updated_at > 0
                UNION ALL
                SELECT queried_at AS ts FROM ip_geo_cache WHERE queried_at > 0
            )
            WHERE ts >= ?
            """,
 (floor_ts,),
        ).fetchone() or {}
    return int(row["first_ts"]) if row and row["first_ts"] is not None else None

def cached_active_user_rows_between(
    cache_path: Path,
    start_ts: int,
    end_ts: int | None = None,
) -> tuple[list[int], dict[int, list[sqlite3.Row]], dict[int, sqlite3.Row]]:
    init_cache(cache_path)
    end_ts = end_ts or int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        rows = conn.execute(
            """
            SELECT a.user_id, a.ip, a.last_seen_at, u.display_name,
                   g.country, g.region, g.city, g.district, g.isp, g.stat_area_key, g.stat_area_name, g.stat_area_level, g.raw
            FROM active_ip_records AS a
            LEFT JOIN users AS u ON u.user_id = a.user_id
            LEFT JOIN ip_geo_cache AS g ON g.ip = a.ip
            WHERE a.ignored_at IS NULL AND a.last_seen_at BETWEEN ? AND ?
            ORDER BY a.user_id ASC, a.last_seen_at DESC, a.ip ASC
            """,
 (start_ts, end_ts),
        ).fetchall()

    grouped: dict[int, list[sqlite3.Row]] = {}
    user_rows: dict[int, sqlite3.Row] = {}
    for row in rows:
        xboard_user_id = int(row["user_id"])
        grouped.setdefault(xboard_user_id, []).append(row)
        user_rows.setdefault(xboard_user_id, row)
    ordered_user_ids = sorted(grouped, key=lambda uid: (-len(grouped[uid]), uid))
    return ordered_user_ids, grouped, user_rows

def cached_active_user_rows(cache_path: Path, window: timedelta) -> tuple[list[int], dict[int, list[sqlite3.Row]], dict[int, sqlite3.Row]]:
    cutoff_ts = int((datetime.now() - window).timestamp())
    return cached_active_user_rows_between(cache_path, cutoff_ts)

def active_user_button_items_from_cache_sync(
    cache_path: Path,
    window: timedelta | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> list[tuple[int, str]]:
    if start_ts is not None:
        ordered_user_ids, _grouped, user_rows = cached_active_user_rows_between(cache_path, start_ts, end_ts)
    elif window is not None:
        ordered_user_ids, _grouped, user_rows = cached_active_user_rows(cache_path, window)
    else:
        ordered_user_ids, _grouped, user_rows = [], {}, {}
    return [(user_id, cached_user_button_label(user_rows.get(user_id), user_id)[:48]) for user_id in ordered_user_ids]

def list_user_ips_from_cache_sync(
    cache_path: Path,
    label: str,
    window: timedelta | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> str:
    if start_ts is not None:
        ordered_user_ids, grouped, user_rows = cached_active_user_rows_between(cache_path, start_ts, end_ts)
    elif window is not None:
        ordered_user_ids, grouped, user_rows = cached_active_user_rows(cache_path, window)
    else:
        ordered_user_ids, grouped, user_rows = [], {}, {}

    if start_ts is not None and end_ts is not None and label == "自定区间":
        lines = [
            "🗺 <b>自定区间用户活跃度概览</b>",
            f"时间区间：{datetime.fromtimestamp(start_ts).strftime('%Y-%m-%d %H:%M')} - {datetime.fromtimestamp(end_ts).strftime('%Y-%m-%d %H:%M')}",
            "────────────",
        ]
    else:
        lines = [
            f"🌐 <b>{label} 用户活跃度概览</b>",
            "────────────",
        ]
    if not ordered_user_ids:
        lines.extend([
            f"暂无 {label} 在线 IP 记录。",
            "",
            "缓存可能尚未完成首次采集，请稍后再试。",
        ])
    else:
        all_rows = [row for user_id in ordered_user_ids for row in grouped[user_id]]
        lines.extend([
            f"👥 活跃用户：{len(grouped)} 个",
            f"🌐 活跃 IP：{len(all_rows)} 个",
            f"📍 活跃地区：{count_geo_areas(all_rows)} 个",
            "",
            f"🗺 活跃用户<b> Top {len(grouped)}</b>",
        ])
        for xboard_user_id in ordered_user_ids:
            user_ip_rows = grouped[xboard_user_id]
            lines.append(
                f"• {render_cached_user_label(user_rows[xboard_user_id], xboard_user_id)}："
                f"活跃 IP {len(user_ip_rows)} 个，活跃地区 {count_geo_areas(user_ip_rows)} 个；"
            )
    result = "\n".join(lines).strip()
    if len(result) > 3900:
        result = result[:3850].rstrip() + "\n\n……内容过长，已截断。"
    return result

def count_user_ips_from_cache_sync(
    cache_path: Path,
    xboard_user_id: int,
    window: timedelta | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> int:
    init_cache(cache_path)
    if start_ts is None and window is not None:
        start_ts = int((datetime.now() - window).timestamp())
    end_ts = end_ts or int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        if start_ts is not None:
            row = conn.execute(
                """
                SELECT COUNT(DISTINCT ip) AS total
                FROM active_ip_records
                WHERE user_id = ? AND ignored_at IS NULL AND last_seen_at BETWEEN ? AND ?
                """,
 (xboard_user_id, start_ts, end_ts),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(DISTINCT ip) AS total FROM active_ip_records WHERE user_id = ? AND ignored_at IS NULL",
 (xboard_user_id,),
            ).fetchone()
    return int(row["total"] if row and row["total"] is not None else 0)

def query_user_ips_from_cache_sync(
    cache_path: Path,
    xboard_user_id: int,
    label: str | None = None,
    window: timedelta | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
    page: int = 0,
    page_size: int = 10,
) -> str:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        user_row = conn.execute("SELECT display_name FROM users WHERE user_id = ?", (xboard_user_id,)).fetchone()
        rows = conn.execute(
            """
            SELECT a.ip, a.last_seen_at, g.country, g.region, g.city, g.district, g.isp, g.stat_area_key, g.stat_area_name, g.stat_area_level, g.raw
            FROM active_ip_records AS a
            LEFT JOIN ip_geo_cache AS g ON g.ip = a.ip
            WHERE a.user_id = ? AND a.ignored_at IS NULL
            ORDER BY a.last_seen_at DESC, a.ip ASC
            """,
 (xboard_user_id,),
        ).fetchall()

    if label and (window or start_ts is not None):
        if start_ts is None:
            start_ts = int((datetime.now() - window).timestamp()) if window else 0
        end_ts = end_ts or int(datetime.now().timestamp())
        filtered_rows = [row for row in rows if start_ts <= int(row["last_seen_at"]) <= end_ts]
        return render_user_ip_rows_page(
            render_cached_user_label(user_row, xboard_user_id),
            label,
            filtered_rows,
            page,
            page_size,
            start_ts,
            end_ts,
        )
    else:
        now = datetime.now()
        lines = [f"👤 {render_cached_user_label(user_row, xboard_user_id)}", "────────────", ""]
        shown_ips: set[str] = set()
        lines.extend(render_cached_ip_bucket("近 1 小时", rows, shown_ips, int((now - timedelta(hours=1)).timestamp())))
        lines.append("")
        lines.extend(render_cached_ip_bucket("近 24 小时", rows, shown_ips, int((now - timedelta(hours=24)).timestamp())))
        lines.append("")
        lines.extend(render_cached_ip_bucket("近 7 天", rows, shown_ips, int((now - timedelta(days=7)).timestamp())))

    result = "\n".join(lines).strip()
    if len(result) > 3900:
        result = result[:3850].rstrip() + "\n\n……内容过长，已截断。"
    return result

def user_ip_page_rows_sync(
    cache_path: Path,
    xboard_user_id: int,
    kind: str,
    page: int = 0,
    page_size: int = 10,
) -> list[sqlite3.Row]:
    parsed = parse_ip_kind(kind)
    if not parsed:
        return []
    _label, start_ts, end_ts = parsed
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        rows = conn.execute(
            """
            SELECT a.ip, a.last_seen_at, g.country, g.region, g.city, g.district, g.isp, g.stat_area_key, g.stat_area_name, g.stat_area_level, g.raw
            FROM active_ip_records AS a
            LEFT JOIN ip_geo_cache AS g ON g.ip = a.ip
            WHERE a.user_id = ? AND a.ignored_at IS NULL AND a.last_seen_at BETWEEN ? AND ?
            ORDER BY a.last_seen_at DESC, a.ip ASC
            """,
 (xboard_user_id, start_ts, end_ts or int(datetime.now().timestamp())),
        ).fetchall()
    safe_page_size = max(1, min(page_size, 50))
    safe_page = max(0, page)
    return rows[safe_page * safe_page_size:(safe_page + 1) * safe_page_size]

def user_ip_ignore_items_sync(cache_path: Path, xboard_user_id: int, kind: str, page: int, dimension: str) -> list[dict[str, Any]]:
    return ignore_items_from_ip_rows(user_ip_page_rows_sync(cache_path, xboard_user_id, kind, page), dimension)

def ignored_rule_count_sync(cache_path: Path) -> int:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM ignored_ip_rules").fetchone()[0] or 0)

def ignored_rule_counts_by_dimension_sync(cache_path: Path) -> dict[str, int]:
    init_cache(cache_path)
    counts = {"area": 0, "asn": 0, "cidr": 0}
    with cache_connect(cache_path) as conn:
        rows = conn.execute("SELECT dimension, COUNT(*) AS c FROM ignored_ip_rules WHERE dimension IN ('area', 'asn', 'cidr') GROUP BY dimension").fetchall()
    for row in rows:
        counts[str(row["dimension"])] = int(row["c"] or 0)
    return counts

def ignored_rule_values_sync(cache_path: Path, dimension: str) -> set[str]:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        rows = conn.execute("SELECT value FROM ignored_ip_rules WHERE dimension = ?", (dimension,)).fetchall()
    return {str(row["value"] or "") for row in rows}

def ignored_rule_items_sync(cache_path: Path) -> list[dict[str, Any]]:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        rows = conn.execute(
            """
            SELECT dimension, value, updated_at, created_at
            FROM ignored_ip_rules
            WHERE dimension IN ('area', 'asn', 'cidr')
            ORDER BY updated_at DESC, created_at DESC, dimension ASC, value ASC
            """
        ).fetchall()
        asn_labels: dict[str, str] = {}
        if any(str(row["dimension"] or "") == "asn" for row in rows):
            geo_rows = conn.execute("SELECT raw FROM ip_geo_cache WHERE raw IS NOT NULL AND raw != ''").fetchall()
            for geo_row in geo_rows:
                raw = raw_geo_data(geo_row)
                key = asn_key_from_raw(raw)
                if key and key not in asn_labels:
                    asn_labels[key] = asn_label_from_raw(raw) or key
    items: list[dict[str, Any]] = []
    for row in rows:
        dimension = str(row["dimension"] or "")
        value = str(row["value"] or "")
        if dimension == "area":
            label = geo_area_rule_label(value)
            dim_label = "📍"
        elif dimension == "asn":
            label = asn_labels.get(value, value)
            dim_label = "🏷️"
        elif dimension == "cidr":
            label = value
            dim_label = "🌐"
        else:
            continue
        items.append({"dimension": dimension, "value": value, "label": label, "sub": dim_label, "updated_at": int(row["updated_at"] or row["created_at"] or 0)})
    return items

def ignored_list_items_sync(cache_path: Path, dimension: str) -> list[dict[str, Any]]:
    init_cache(cache_path)
    with cache_connect(cache_path) as conn:
        if dimension == "area":
            rows = conn.execute(
                """
                SELECT COALESCE(NULLIF(g.stat_area_name, ''), NULLIF(g.city, ''), NULLIF(g.region, ''), NULLIF(g.country, '')) AS display,
                       g.country, g.region, g.city, g.district, g.stat_area_key, g.stat_area_name, g.stat_area_level, MAX(a.last_seen_at) AS last_seen_at,
                       COUNT(DISTINCT a.ip) AS ip_count, COUNT(DISTINCT a.user_id) AS user_count
                FROM active_ip_records AS a
                JOIN ip_geo_cache AS g ON g.ip = a.ip
                WHERE COALESCE(NULLIF(g.stat_area_key, ''), NULLIF(g.city, ''), NULLIF(g.region, ''), NULLIF(g.country, '')) IS NOT NULL
                GROUP BY COALESCE(NULLIF(g.stat_area_key, ''), NULLIF(g.city, ''), NULLIF(g.region, ''), NULLIF(g.country, '')), g.country, g.region, g.city, g.district, g.stat_area_key, g.stat_area_name, g.stat_area_level
                ORDER BY last_seen_at DESC, display ASC
                """
            ).fetchall()
            items = []
            for row in rows:
                key = geo_area_key(row)
                if not key:
                    continue
                label = geo_area_display_label(row, key)
                items.append({"value": key, "label": label, "sub": f"{int(row['ip_count'] or 0)} IP / {int(row['user_count'] or 0)} 用户", "last_seen_at": int(row["last_seen_at"] or 0)})
            return items
        if dimension == "asn":
            rows = conn.execute(
                """
                SELECT a.ip, a.user_id, a.last_seen_at, g.raw
                FROM active_ip_records AS a
                JOIN ip_geo_cache AS g ON g.ip = a.ip
                WHERE g.raw IS NOT NULL AND g.raw != ''
                ORDER BY a.last_seen_at DESC
                """
            ).fetchall()
            buckets: dict[str, dict[str, Any]] = {}
            for row in rows:
                raw = raw_geo_data(row)
                key = asn_key_from_raw(raw)
                if not key:
                    continue
                bucket = buckets.setdefault(key, {"value": key, "label": asn_label_from_raw(raw) or key, "ips": set(), "users": set(), "last_seen_at": 0})
                bucket["ips"].add(str(row["ip"]))
                bucket["users"].add(int(row["user_id"]))
                bucket["last_seen_at"] = max(int(bucket["last_seen_at"]), int(row["last_seen_at"] or 0))
            return [
                {"value": key, "label": str(bucket["label"]), "sub": f"{len(bucket['ips'])} IP / {len(bucket['users'])} 用户", "last_seen_at": int(bucket["last_seen_at"])}
                for key, bucket in sorted(buckets.items(), key=lambda item: (-int(item[1]["last_seen_at"]), item[0]))
            ]
        if dimension == "cidr":
            rows = conn.execute(
                """
                SELECT ip, user_id, last_seen_at
                FROM active_ip_records
                ORDER BY last_seen_at DESC
                """
            ).fetchall()
            buckets: dict[str, dict[str, Any]] = {}
            for row in rows:
                cidr = ipv4_24_cidr(str(row["ip"] or ""))
                if not cidr:
                    continue
                bucket = buckets.setdefault(cidr, {"value": cidr, "label": cidr, "ips": set(), "users": set(), "last_seen_at": 0})
                bucket["ips"].add(str(row["ip"]))
                bucket["users"].add(int(row["user_id"]))
                bucket["last_seen_at"] = max(int(bucket["last_seen_at"]), int(row["last_seen_at"] or 0))
            return [
                {"value": cidr, "label": cidr, "sub": f"{len(bucket['ips'])} IP / {len(bucket['users'])} 用户", "last_seen_at": int(bucket["last_seen_at"])}
                for cidr, bucket in sorted(buckets.items(), key=lambda item: (-int(item[1]["last_seen_at"]), item[0]))
            ]
    return []

def ignored_rule_toggle_sync(cache_path: Path, dimension: str, value: str) -> bool:
    init_cache(cache_path)
    if dimension not in {"area", "asn", "cidr"}:
        raise ValueError("unsupported ignore dimension")
    now_ts = int(datetime.now().timestamp())
    with cache_connect(cache_path) as conn:
        exists = conn.execute("SELECT 1 FROM ignored_ip_rules WHERE dimension = ? AND value = ?", (dimension, value)).fetchone()
        if exists:
            conn.execute("DELETE FROM ignored_ip_rules WHERE dimension = ? AND value = ?", (dimension, value))
            reason = {"area": "manual_area", "asn": "manual_asn", "cidr": "manual_cidr"}.get(dimension, "")
            if reason:
                conn.execute("UPDATE active_ip_records SET ignored_at = NULL, ignore_reason = NULL, ignore_note = NULL WHERE ignore_reason = ?", (reason,))
            apply_ignored_rules_conn(conn, now_ts)
            return False
        conn.execute(
            """
            INSERT INTO ignored_ip_rules(dimension, value, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(dimension, value) DO UPDATE SET updated_at = excluded.updated_at
            """,
 (dimension, value, now_ts, now_ts),
        )
        apply_ignored_rules_conn(conn, now_ts)
        return True

def apply_ignored_rules_conn(conn: sqlite3.Connection, now_ts: int) -> None:
    conn.execute("DELETE FROM ignored_ip_rules WHERE dimension NOT IN ('area', 'asn', 'cidr')")
    area_rules = [str(row["value"] or "") for row in conn.execute("SELECT value FROM ignored_ip_rules WHERE dimension = 'area'").fetchall()]
    if area_rules:
        placeholders = ",".join("?" for _ in area_rules)
        conn.execute(
            f"""
            UPDATE active_ip_records
            SET ignored_at = ?, ignore_reason = 'manual_area', ignore_note = '忽略列表：地区'
            WHERE ignored_at IS NULL AND ip IN (
                SELECT g.ip FROM ip_geo_cache AS g
                WHERE COALESCE(NULLIF(g.stat_area_key, ''), NULLIF(g.city, ''), NULLIF(g.region, ''), NULLIF(g.country, '')) IS NOT NULL
                  AND (
                    NULLIF(g.stat_area_key, '') IN ({placeholders})
                    OR
                    CASE
                      WHEN NULLIF(g.city, '') IS NOT NULL THEN TRIM(COALESCE(g.country, '') || CASE WHEN COALESCE(g.country, '') != '' THEN '|' ELSE '' END || COALESCE(g.region, '') || CASE WHEN COALESCE(g.region, '') != '' THEN '|' ELSE '' END || COALESCE(g.city, ''))
                      WHEN NULLIF(g.region, '') IS NOT NULL THEN TRIM(COALESCE(g.country, '') || CASE WHEN COALESCE(g.country, '') != '' THEN '|' ELSE '' END || COALESCE(g.region, ''))
                      ELSE g.country
                    END
                  ) IN ({placeholders})
            )
            """,
            [now_ts, *area_rules, *area_rules],
        )
    asn_rules = {str(row["value"] or "") for row in conn.execute("SELECT value FROM ignored_ip_rules WHERE dimension = 'asn'").fetchall()}
    if asn_rules:
        rows = conn.execute(
            """
            SELECT a.user_id, a.ip, g.raw
            FROM active_ip_records AS a
            JOIN ip_geo_cache AS g ON g.ip = a.ip
            WHERE a.ignored_at IS NULL AND g.raw IS NOT NULL AND g.raw != ''
            """
        ).fetchall()
        targets = [(int(row["user_id"]), str(row["ip"])) for row in rows if (asn_key_for_geo_row(row) in asn_rules)]
        if targets:
            conn.executemany(
                """
                UPDATE active_ip_records
                SET ignored_at = ?, ignore_reason = 'manual_asn', ignore_note = '忽略列表：ASN'
                WHERE user_id = ? AND ip = ? AND ignored_at IS NULL
                """,
                [(now_ts, user_id, ip) for user_id, ip in targets],
            )
    cidr_rules = [str(row["value"] or "") for row in conn.execute("SELECT value FROM ignored_ip_rules WHERE dimension = 'cidr'").fetchall()]
    if cidr_rules:
        networks = []
        for rule in cidr_rules:
            try:
                net = ipaddress.ip_network(rule, strict=False)
            except ValueError:
                continue
            if net.version == 4:
                networks.append(net)
        if networks:
            rows = conn.execute("SELECT user_id, ip FROM active_ip_records WHERE ignored_at IS NULL").fetchall()
            targets = []
            for row in rows:
                try:
                    ip_obj = ipaddress.ip_address(str(row["ip"] or ""))
                except ValueError:
                    continue
                if ip_obj.version == 4 and any(ip_obj in net for net in networks):
                    targets.append((int(row["user_id"]), str(row["ip"])))
            if targets:
                conn.executemany(
                    """
                    UPDATE active_ip_records
                    SET ignored_at = ?, ignore_reason = 'manual_cidr', ignore_note = '忽略列表：IP 段'
                    WHERE user_id = ? AND ip = ? AND ignored_at IS NULL
                    """,
                    [(now_ts, user_id, ip) for user_id, ip in targets],
                )
# Export this module's own public symbols for downstream star imports.
__all__ = [name for name in globals() if not name.startswith("_")]

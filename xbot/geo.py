from __future__ import annotations

from .common import (
    Any,
    Path,
    datetime,
    ipaddress,
    json,
    log,
    re,
    sqlite3,
    time,
    urllib,
)
from .db.cache import (
    apply_ignored_rules_conn,
    asn_key_from_raw,
    build_geo_stat_area,
    cache_connect,
    ignored_rule_counts_by_dimension_sync,
    init_cache,
    raw_geo_data,
)

def cache_geo_status_sync(cache_path: Path) -> dict[str, int]:
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
    return {"active_ips": active_ips, "geo_total": geo_total, "geo_pending": geo_pending}

def pending_geo_ips_sync(cache_path: Path, limit: int | None = None) -> list[str]:
    init_cache(cache_path)
    sql = """
        SELECT ip FROM ip_geo_cache
        WHERE (queried_at IS NULL OR queried_at <= 0)
          AND (raw IS NULL OR raw = '')
        ORDER BY queried_at ASC, ip ASC
    """
    params: tuple[Any, ...] = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (limit,)
    with cache_connect(cache_path) as conn:
        return [str(row["ip"]) for row in conn.execute(sql, params).fetchall()]

def estimate_geo_wait_seconds(pending_count: int, queries_per_minute: int) -> int:
    if pending_count <= 0:
        return 0
    return int((pending_count * 60 + max(1, queries_per_minute) - 1) // max(1, queries_per_minute))

def query_ip_api_sync(ip: str) -> dict[str, Any]:
    fields = "status,message,country,countryCode,regionName,city,district,isp,as,asname,org,query"
    url = "http://ip-api.com/json/" + urllib.parse.quote(ip, safe="") + "?" + urllib.parse.urlencode({
        "lang": "zh-CN",
        "fields": fields,
    })
    req = urllib.request.Request(url, headers={"User-Agent": "xbot"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        raw = resp.read(8192).decode("utf-8", errors="replace")
    data = json.loads(raw)
    if data.get("status") != "success":
        raise RuntimeError(str(data.get("message") or "ip-api 查询失败"))
    return data

def normalize_geo_name(value: Any) -> str:
    return str(value or "").strip().replace("臺", "台")

def geo_text_contains(values: list[str], patterns: list[str]) -> bool:
    joined = " ".join(values)
    return any(re.search(pattern, joined, re.IGNORECASE) for pattern in patterns)

def normalize_taiwan_city(region: str, city: str, district: str) -> str:
    county_cities = [
        "台北市", "新北市", "桃园市", "台中市", "台南市", "高雄市",
        "基隆市", "新竹市", "嘉义市",
        "新竹县", "苗栗县", "彰化县", "南投县", "云林县", "嘉义县",
        "屏东县", "宜兰县", "花莲县", "台东县", "澎湖县", "金门县", "连江县",
    ]
    aliases = {
        "台北": "台北市",
        "新北": "新北市",
        "桃园": "桃园市",
        "台中": "台中市",
        "台南": "台南市",
        "高雄": "高雄市",
        "基隆": "基隆市",
        "新竹": "新竹市",
        "嘉义": "嘉义市",
    }
    for item in [region, city, district]:
        name = normalize_geo_name(item)
        if not name:
            continue
        if name in county_cities:
            return name
        if name in aliases:
            return aliases[name]
    return normalize_geo_name(region or city or district) or "台湾未知城市"

def build_geo_stat_area(data: dict[str, Any]) -> dict[str, str]:
    """Build the normalized city-level area used only for active-area statistics."""
    country_code = normalize_geo_name(data.get("countryCode")).upper()
    country = normalize_geo_name(data.get("country"))
    region = normalize_geo_name(data.get("regionName"))
    city = normalize_geo_name(data.get("city"))
    district = normalize_geo_name(data.get("district"))
    values = [country, region, city, district]

    if country_code == "HK" or geo_text_contains(values, [r"香港", r"Hong\s*Kong"]):
        return {"key": "HK:香港", "name": "香港", "level": "sar_city"}
    if country_code == "MO" or geo_text_contains(values, [r"澳门", r"澳門", r"Macau", r"Macao"]):
        return {"key": "MO:澳门", "name": "澳门", "level": "sar_city"}
    if country_code == "TW" or geo_text_contains(values, [r"台湾", r"Taiwan"]):
        stat_name = normalize_taiwan_city(region, city, district)
        return {"key": f"TW:{stat_name}", "name": stat_name, "level": "tw_city"}

    if country_code == "CN" or country == "中国":
        municipalities = {"北京市", "上海市", "天津市", "重庆市"}
        if region in municipalities:
            return {"key": f"CN:{region}", "name": region, "level": "municipality"}
        stat_name = city or region or "未知城市"
        return {"key": f"CN:{region or '未知省份'}:{stat_name}", "name": stat_name, "level": "city"}

    stat_name = city or region or country or "未知地区"
    return {"key": f"{country_code or country or 'UNKNOWN'}:{region}:{stat_name}", "name": stat_name, "level": "city"}

def update_geo_cache_success_sync(cache_path: Path, ip: str, data: dict[str, Any]) -> None:
    now_ts = int(datetime.now().timestamp())
    stat_area = build_geo_stat_area(data)
    with cache_connect(cache_path) as conn:
        conn.execute(
            """
            INSERT INTO ip_geo_cache(ip, country, region, city, district, isp, stat_area_key, stat_area_name, stat_area_level, raw, queried_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ip) DO UPDATE SET
                country=excluded.country,
                region=excluded.region,
                city=excluded.city,
                district=excluded.district,
                isp=excluded.isp,
                stat_area_key=excluded.stat_area_key,
                stat_area_name=excluded.stat_area_name,
                stat_area_level=excluded.stat_area_level,
                raw=excluded.raw,
                queried_at=excluded.queried_at
            """,
 (
                ip,
                str(data.get("country") or ""),
                str(data.get("regionName") or ""),
                str(data.get("city") or ""),
                str(data.get("district") or ""),
                str(data.get("isp") or ""),
                stat_area["key"],
                stat_area["name"],
                stat_area["level"],
                json.dumps(data, ensure_ascii=False),
                now_ts,
            ),
        )
        country = str(data.get("country") or "").strip()
        region = str(data.get("regionName") or "").strip()
        city = str(data.get("city") or "").strip()
        area_keys = []
        if stat_area.get("key"):
            area_keys.append(stat_area["key"])
        if city:
            area_keys.append("|".join(part for part in (country, region, city) if part))
        if region:
            area_keys.append("|".join(part for part in (country, region) if part))
        if country:
            area_keys.append(country)
        area_keys = [key for key in dict.fromkeys(area_keys) if key]
        if area_keys:
            placeholders = ",".join("?" for _ in area_keys)
            conn.execute(
                f"""
                UPDATE active_ip_records
                SET ignored_at = ?, ignore_reason = 'manual_area', ignore_note = '忽略列表：地区'
                WHERE ip = ? AND ignored_at IS NULL AND EXISTS (
                    SELECT 1 FROM ignored_ip_rules AS r
                    WHERE r.dimension = 'area' AND r.value IN ({placeholders})
                )
                """,
                [now_ts, ip, *area_keys],
            )
        apply_ignored_rules_conn(conn, now_ts)

def row_value(row: sqlite3.Row, name: str) -> Any:
    try:
        return row[name]
    except (KeyError, IndexError):
        return None

def ignored_rules_text_sync(cache_path: Path) -> str:
    counts = ignored_rule_counts_by_dimension_sync(cache_path)
    return "\n".join([
        "📎 <b>当前忽略</b>",
        "────────────",
        "当前已忽略：",
        f"📍 地区：{counts['area']}",
        f"🏷 ASN：{counts['asn']}",
        f"🌐 IP ：{counts['cidr']}",
    ])

def update_geo_cache_failure_sync(cache_path: Path, ip: str, error: str) -> None:
    # 失败也写 queried_at，避免坏 IP 在一次初始化里反复阻塞；后续可通过清空 queried_at 重试。
    now_ts = int(datetime.now().timestamp())
    raw = json.dumps({"error": error}, ensure_ascii=False)
    with cache_connect(cache_path) as conn:
        conn.execute(
            """
            INSERT INTO ip_geo_cache(ip, raw, queried_at)
            VALUES (?, ?, ?)
            ON CONFLICT(ip) DO UPDATE SET raw=excluded.raw, queried_at=excluded.queried_at
            """,
 (ip, raw, now_ts),
        )

def backfill_geo_pending_once(cache_path: Path, limit: int = 5) -> tuple[int, int, int]:
    """Best-effort background IP geo backfill. Returns (total, success, failed)."""
    ips = pending_geo_ips_sync(cache_path, limit=max(1, limit))
    success = 0
    failed = 0
    for ip in ips:
        try:
            data = query_ip_api_sync(ip)
            update_geo_cache_success_sync(cache_path, ip, data)
            success += 1
        except urllib.error.HTTPError as exc:
            failed += 1
            # Stop immediately on rate limit; leave this IP pending for a later run.
            if exc.code == 429:
                log.warning("后台 IP 归属地补全触发 ip-api 限流，暂停本轮")
                break
            update_geo_cache_failure_sync(cache_path, ip, f"HTTP {exc.code}")
        except Exception as exc:
            failed += 1
            update_geo_cache_failure_sync(cache_path, ip, type(exc).__name__)
    return len(ips), success, failed

def backfill_geo_pending_rate_limited(
    cache_path: Path,
    limit: int,
    queries_per_minute: int,
    retry_wait_seconds: float = 65.0,
    stop_when_rate_limited: bool = True,
) -> tuple[int, int, int, bool]:
    """Backfill pending IP geo records at a steady, API-friendly pace.

    Returns (total_selected, success, failed, rate_limited). The sleep happens
    between requests, not in a burst, so startup will not intentionally drive the
    free ip-api endpoint into 429 just to finish faster.
    """
    ips = pending_geo_ips_sync(cache_path, limit=max(1, int(limit)))
    if not ips:
        return 0, 0, 0, False
    interval = 60.0 / max(1, int(queries_per_minute))
    success = 0
    failed = 0
    rate_limited = False
    for index, ip in enumerate(ips):
        started = time.monotonic()
        try:
            data = query_ip_api_sync(ip)
            update_geo_cache_success_sync(cache_path, ip, data)
            success += 1
        except urllib.error.HTTPError as exc:
            failed += 1
            if exc.code == 429:
                rate_limited = True
                log.warning("IP 归属地补全触发 ip-api 限流，等待 %.0f 秒后重试", retry_wait_seconds)
                time.sleep(max(5.0, retry_wait_seconds))
                if stop_when_rate_limited:
                    break
            else:
                update_geo_cache_failure_sync(cache_path, ip, f"HTTP {exc.code}")
        except Exception as exc:
            failed += 1
            update_geo_cache_failure_sync(cache_path, ip, type(exc).__name__)
        if index < len(ips) - 1:
            elapsed = time.monotonic() - started
            sleep_for = interval - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)
    return len(ips), success, failed, rate_limited

def backfill_geo_pending_until_complete(cache_path: Path, queries_per_minute: int = 30) -> tuple[int, int, int]:
    """Backfill all pending geo records before startup decisions/notifications run.

    This is intentionally stricter than the periodic collector: startup with a fresh
    SQLite database should not evaluate IP alerts or render initial active-region
    counts while many IPs are still shown as “待查询”. On ip-api rate limiting we
    wait for the next free-window and continue, instead of starting notification
    loops with incomplete geo data. It still respects the configured per-minute
    query pace instead of bursting to the limit.
    """
    total = 0
    success = 0
    failed = 0
    queries_per_minute = max(1, int(queries_per_minute))
    while True:
        pending = cache_geo_status_sync(cache_path)["geo_pending"]
        if pending <= 0:
            break
        current_total, current_success, current_failed, rate_limited = backfill_geo_pending_rate_limited(
            cache_path,
            limit=pending,
            queries_per_minute=queries_per_minute,
            stop_when_rate_limited=True,
        )
        total += current_total
        success += current_success
        failed += current_failed
        pending_after = cache_geo_status_sync(cache_path)["geo_pending"]
        log.info(
            "启动初始化 IP 归属地补全：本轮待处理 %s 个，成功 %s 个，失败 %s 个，剩余 %s 个",
            current_total, current_success, current_failed, pending_after,
        )
        if pending_after <= 0:
            break
        if rate_limited:
            continue
        if pending_after >= pending or current_success <= 0:
            log.warning("启动初始化 IP 归属地补全未取得进展，等待 60 秒后重试")
            time.sleep(60.0)
    return total, success, failed

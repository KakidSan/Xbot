from __future__ import annotations

from ..common import (
    Any,
    AuthenticationError,
    Counter,
    RedisConnectionError,
    RedisError,
    RedisTimeoutError,
    datetime,
    html_code,
    re,
    redis,
    socket,
    timedelta,
)

ONLINE_IP_KEY_SPECS: tuple[tuple[str, str, str], ...] = (
    ("Heki", "heki:ip:*", r"heki:ip:(\d+):(.+)"),
    ("Soga", "soga_conn_*", r"soga_conn_(\d+)_(.+)"),
)

def tcp_check(host: str, port: int, service_name: str) -> tuple[bool, list[str]]:
    """Check TCP reachability without exposing configured host/port in messages."""
    try:
        with socket.create_connection((host, port), timeout=3):
            return True, []
    except socket.timeout:
        return False, [
            f"❌ {service_name} 端口连接超时。",
            f"❌ 可能是防火墙丢弃连接、网络不通，或 {service_name} 未监听外部连接。",
        ]
    except ConnectionRefusedError as exc:
        return False, [
            f"❌ {service_name} 端口拒绝连接。",
            f"❌ 错误类型：{html_code(type(exc).__name__)}",
            f"❌ 目标主机可到达，但没有服务接受连接；常见原因是 {service_name} 未启动、未监听外部连接、端口未映射，或防火墙主动拒绝。",
        ]
    except OSError as exc:
        return False, [
            f"❌ {service_name} 端口无法访问。",
            f"❌ 错误类型：{html_code(type(exc).__name__)}",
            f"❌ 可能是地址错误、路由不可达、防火墙拦截，或 {service_name} 未监听外部连接。",
        ]

def redis_config_missing(cfg: RedisConfig) -> bool:
    return not cfg.host.strip() or not cfg.port

def redis_readable_summary(client: redis.Redis, cfg: RedisConfig) -> list[str]:
    """Read a small Redis summary and render it in human-friendly lines.

    This avoids dumping raw values. It only inspects metadata: DB key count,
    sampled key types, TTL state, and XBoard-like online device key count.
    """
    lines: list[str] = []

    db_size = client.dbsize()
    lines.append(f"✅ Redis 当前 DB Key 数量：{db_size}")

    sample_keys = list(client.scan_iter(match="*", count=100))[:30]
    if not sample_keys:
        lines.append("✅ Redis 当前 DB 暂无可展示 Key。")
        return lines

    type_counter: Counter[str] = Counter()
    ttl_counter: Counter[str] = Counter()
    for key in sample_keys:
        try:
            key_type = client.type(key)
            ttl = client.ttl(key)
        except RedisError:
            continue
        type_counter[str(key_type)] += 1
        if ttl == -2:
            ttl_counter["已过期/不存在"] += 1
        elif ttl == -1:
            ttl_counter["永久"] += 1
        else:
            ttl_counter["有过期时间"] += 1

    if type_counter:
        type_text = "，".join(f"{name}: {count}" for name, count in sorted(type_counter.items()))
        lines.append(f"✅ 抽样 Key 类型分布：{type_text}")
    if ttl_counter:
        ttl_text = "，".join(f"{name}: {count}" for name, count in sorted(ttl_counter.items()))
        lines.append(f"✅ 抽样 Key 过期状态：{ttl_text}")

    online_key_counts: list[str] = []
    for label, pattern, _ in ONLINE_IP_KEY_SPECS:
        count = sum(1 for _ in client.scan_iter(match=pattern, count=100))
        online_key_counts.append(f"{label}: {count}")
    lines.append(f"✅ 在线 IP Key 数量：{'，'.join(online_key_counts)}")

    device_pattern = "user_devices:*"
    device_count = sum(1 for _ in client.scan_iter(match=device_pattern, count=100))
    lines.append(f"✅ XBoard 在线设备 Key 数量：{device_count}")
    return lines

def redis_client(cfg: RedisConfig) -> redis.Redis:
    return redis.Redis(
        host=cfg.host.strip(),
        port=int(cfg.port),
        password=cfg.password,
        db=cfg.db,
        socket_connect_timeout=3,
        socket_timeout=5,
        decode_responses=True,
    )

def redis_failure_message(exc: Exception) -> str:
    if isinstance(exc, AuthenticationError):
        return f"❌ Redis 认证失败。\n❌ 错误类型：{html_code(type(exc).__name__)}\n❌ Redis 密码不正确，或 Redis 要求认证但 Config 未填写密码。"
    if isinstance(exc, RedisTimeoutError):
        return f"❌ Redis 响应超时。\n❌ 错误类型：{html_code(type(exc).__name__)}\n❌ Redis 负载可能过高，或网络质量异常。"
    if isinstance(exc, RedisConnectionError):
        return f"❌ Redis 握手失败。\n❌ 错误类型：{html_code(type(exc).__name__)}\n❌ 端口可能不是 Redis 服务、TLS/SSL 配置不匹配，或连接被服务端关闭。"
    if isinstance(exc, RedisError):
        return f"❌ Redis 返回错误。\n❌ 错误类型：{html_code(type(exc).__name__)}"
    return f"❌ Redis 检查失败。\n❌ 错误类型：{html_code(type(exc).__name__)}"

def test_redis_connection_sync(cfg: RedisConfig) -> str:
    """Return a user-facing Redis diagnosis message.

    TCP is checked first, then Redis PING. When PING succeeds, a small metadata
    summary is read and formatted for humans. No raw Redis values are displayed.
    """
    if redis_config_missing(cfg):
        return "⚠️ Redis 连接失败\n\n❌ Redis 连接信息未输入完整。"

    host = cfg.host.strip()
    port = int(cfg.port)
    ok, tcp_lines = tcp_check(host, port, "Redis")
    if not ok:
        return "\n".join(["⚠️ Redis 连接失败", "", *tcp_lines])

    client = redis_client(cfg)
    try:
        pong = client.ping()
        if pong is not True:
            return f"⚠️ Redis 连接失败\n\n✅ Redis 端口可访问。\n❌ Redis PING 返回异常：{pong}"
        summary_lines = redis_readable_summary(client, cfg)
    except RedisError as exc:
        return "\n".join(["⚠️ Redis 连接失败", "", "✅ Redis 端口可访问。", redis_failure_message(exc)])
    finally:
        client.close()

    return "\n".join([
        "✅ Redis 连接成功",
        "",
        "✅ Redis 端口可访问。",
        "✅ Redis PING 测试成功。",
        *summary_lines,
    ])

def last_seen_from_ttl(ttl: int) -> datetime | None:
    """Roughly estimate last online time from Heki/Soga ip string key TTL."""
    if ttl < 0:
        return None
    base_ttl_seconds = 7 * 24 * 60 * 60
    elapsed_seconds = max(0, base_ttl_seconds - ttl)
    return datetime.now() - timedelta(seconds=elapsed_seconds)

def collect_redis_ip_records_sync(cfg: RedisConfig) -> list[tuple[int, str, int, int, str]] | str:
    """Collect Redis Heki/Soga IP records as (user_id, ip, last_seen_ts, ttl, source_key).

    Heki writes heki:ip:<user_id>:<ip> keys. Current Soga writes
    soga_conn_<user_id>_<ip> keys for connection/device-limit records.
    Mixed Heki + Soga deployments are collected into the same local cache.
    """
    if redis_config_missing(cfg):
        return "Redis 连接信息未输入完整"

    client = redis_client(cfg)
    records: list[tuple[int, str, int, int, str]] = []
    try:
        client.ping()
        # Soga does not mirror Heki's heki:ip:<user_id>:<ip> format by replacing
        # "heki" with "soga". Soga 2.13.x writes Redis connection-limit records as
        # soga_conn_<user_id>_<ip> when device/IP limiting is enabled.
        for _, pattern, key_regex in ONLINE_IP_KEY_SPECS:
            key_batch: list[Any] = []

            def flush_key_batch() -> None:
                if not key_batch:
                    return
                pipe = client.pipeline(transaction=False)
                for redis_key in key_batch:
                    pipe.ttl(redis_key)
                ttls = pipe.execute()
                for redis_key, ttl in zip(key_batch, ttls):
                    key_text = str(redis_key)
                    match = re.fullmatch(key_regex, key_text)
                    if not match:
                        continue
                    last_seen = last_seen_from_ttl(int(ttl))
                    if last_seen is None:
                        continue
                    records.append((
                        int(match.group(1)),
                        match.group(2),
                        int(last_seen.timestamp()),
                        int(ttl),
                        key_text,
                    ))
                key_batch.clear()

            for key in client.scan_iter(match=pattern, count=1000):
                key_text = str(key)
                if not re.fullmatch(key_regex, key_text):
                    continue
                key_batch.append(key)
                if len(key_batch) >= 500:
                    flush_key_batch()
            flush_key_batch()
    except RedisError as exc:
        return redis_failure_message(exc)
    finally:
        client.close()
    return records
# Export this module's own public symbols for downstream star imports.
__all__ = [name for name in globals() if not name.startswith("_")]

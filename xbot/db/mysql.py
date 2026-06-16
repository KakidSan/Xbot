from __future__ import annotations

from ..common import (
    Any,
    MySQLError,
    Path,
    compact_connection_error_lines,
    datetime,
    html_code,
    pymysql,
    re,
)
from .redis import test_redis_connection_sync, tcp_check

def mysql_config_missing(cfg: MySQLConfig) -> bool:
    return not cfg.host.strip() or not cfg.port or not cfg.username.strip() or not cfg.database.strip()

def mysql_connect(cfg: MySQLConfig):
    """Create a MySQL connection used only for SELECT queries in this app."""
    return pymysql.connect(
        host=cfg.host.strip(),
        port=int(cfg.port),
        user=cfg.username.strip(),
        password=cfg.password,
        database=cfg.database.strip(),
        charset="utf8mb4",
        connect_timeout=3,
        read_timeout=5,
        write_timeout=5,
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )

def collect_traffic_counters_sync(cfg: MySQLConfig) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read current Xboard daily cumulative counters for users and nodes using SELECT only."""
    if mysql_config_missing(cfg):
        return [], []
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    record_at = int(today_start.timestamp())
    conn = mysql_connect(cfg)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT su.user_id AS entity_id,
                       COALESCE(NULLIF(MAX(u.remarks), ''), MAX(u.email), CONCAT('用户', su.user_id)) AS name,
                       SUM(su.u + su.d) AS total
                FROM v2_stat_user AS su
                LEFT JOIN v2_user AS u ON u.id = su.user_id
                WHERE su.record_type = 'd' AND su.record_at = %s
                GROUP BY su.user_id
                """,
 (record_at,),
            )
            user_rows = cursor.fetchall()
            cursor.execute(
                """
                SELECT ss.server_id AS entity_id,
                       COALESCE(MAX(s.name), CONCAT(MAX(ss.server_type), '#', ss.server_id)) AS name,
                       SUM(ss.u + ss.d) AS total
                FROM v2_stat_server AS ss
                LEFT JOIN v2_server AS s ON s.id = ss.server_id
                WHERE ss.record_type = 'd' AND ss.record_at = %s
                GROUP BY ss.server_id
                """,
 (record_at,),
            )
            node_rows = cursor.fetchall()
    finally:
        conn.close()
    return list(user_rows), list(node_rows)

def test_mysql_connection_sync(cfg: MySQLConfig) -> str:
    """Return a user-facing MySQL diagnosis message.

    MySQL access is intentionally read-only at application level: every SQL here
    is SELECT against metadata/current database. No INSERT/UPDATE/DELETE/DDL path
    exists in the bot.
    """
    if mysql_config_missing(cfg):
        return "⚠️ MySQL 连接失败\n\n❌ MySQL 连接信息未输入完整。"

    host = cfg.host.strip()
    port = int(cfg.port)
    ok, tcp_lines = tcp_check(host, port, "MySQL")
    if not ok:
        return "\n".join(["⚠️ MySQL 连接失败", "", *tcp_lines])

    try:
        conn = mysql_connect(cfg)
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT VERSION() AS version, DATABASE() AS database_name")
                row = cursor.fetchone() or {}
                cursor.execute(
                    "SELECT COUNT(*) AS table_count FROM information_schema.tables WHERE table_schema = %s",
 (cfg.database.strip(),),
                )
                table_row = cursor.fetchone() or {}
        finally:
            conn.close()
    except MySQLError as exc:
        errno = exc.args[0] if exc.args else "unknown"
        if errno in (1044, 1045):
            reason = "❌ MySQL 账号、密码或数据库权限不正确。"
        elif errno == 1049:
            reason = "❌ MySQL 数据库不存在或当前账号无权访问。"
        elif errno in (2003, 2006, 2013):
            reason = "❌ MySQL 服务连接中断或无法建立连接。"
        else:
            reason = "❌ MySQL 返回错误，请根据错误代码继续分析。"
        return "\n".join([
            "⚠️ MySQL 连接失败",
            "",
            "✅ MySQL 端口可访问。",
            "❌ MySQL 登录或查询失败。",
            f"❌ 错误类型：{html_code(type(exc).__name__)}",
            f"❌ 错误代码：{html_code(errno)}",
            reason,
        ])

    version = str(row.get("version") or "unknown")
    table_count = int(table_row.get("table_count") or 0)
    return "\n".join([
        "✅ MySQL 连接成功",
        "",
        "✅ MySQL 端口可访问。",
        "✅ MySQL 登录成功。",
        "✅ MySQL 数据库可访问。",
        "✅ MySQL 只读查询测试成功。",
        f"✅ 数据表数量：{table_count}",
        f"✅ MySQL 版本：{version}",
    ])

def fetch_user_display_details_sync(cfg: MySQLConfig, user_ids: set[int]) -> dict[int, dict[str, str]]:
    """Fetch Xboard user display fields using read-only SELECT."""
    if not user_ids or mysql_config_missing(cfg):
        return {}

    placeholders = ", ".join(["%s"] * len(user_ids))
    sql = f"SELECT id, remarks, email FROM v2_user WHERE id IN ({placeholders})"
    conn = mysql_connect(cfg)
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, tuple(sorted(user_ids)))
            rows = cursor.fetchall()
    finally:
        conn.close()

    details: dict[int, dict[str, str]] = {}
    for row in rows:
        xboard_user_id = int(row.get("id") or 0)
        remarks = str(row.get("remarks") or "").strip().replace("\n", " ")[:80]
        email = str(row.get("email") or "").strip().replace("\n", " ")[:120]
        display_name = (remarks or email)[:80]
        if xboard_user_id:
            details[xboard_user_id] = {"display_name": display_name, "remarks": remarks, "email": email}
    return details

def fetch_all_user_display_details_sync(cfg: MySQLConfig) -> dict[int, dict[str, str]]:
    """Fetch all Xboard users for configuration lists using read-only SELECT."""
    if mysql_config_missing(cfg):
        return {}

    sql = "SELECT id, remarks, email FROM v2_user ORDER BY id ASC"
    conn = mysql_connect(cfg)
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()
    finally:
        conn.close()

    details: dict[int, dict[str, str]] = {}
    for row in rows:
        xboard_user_id = int(row.get("id") or 0)
        remarks = str(row.get("remarks") or "").strip().replace("\n", " ")[:80]
        email = str(row.get("email") or "").strip().replace("\n", " ")[:120]
        display_name = (remarks or email)[:80]
        if xboard_user_id:
            details[xboard_user_id] = {"display_name": display_name, "remarks": remarks, "email": email}
    return details

def connection_check_lines_sync(cfg: AppConfig, cache_path: Path) -> tuple[list[str], bool, bool, bool]:
    from .cache import cache_connect, init_cache

    mysql_result = test_mysql_connection_sync(cfg.mysql)
    redis_result = test_redis_connection_sync(cfg.redis)
    mysql_ok = mysql_result.startswith("✅")
    redis_ok = redis_result.startswith("✅")
    sqlite_ok = True
    sqlite_detail = "可读写"
    try:
        init_cache(cache_path)
        with cache_connect(cache_path) as conn:
            conn.execute("SELECT 1").fetchone()
            quick = conn.execute("PRAGMA quick_check").fetchone()
            if quick and str(quick[0]).lower() != "ok":
                sqlite_ok = False
                sqlite_detail = html_code(str(quick[0]))
    except Exception as exc:
        sqlite_ok = False
        sqlite_detail = html_code(type(exc).__name__)

    mysql_summary = re.sub(r"^[✅❌]\s*", "", mysql_result.splitlines()[0])
    redis_summary = re.sub(r"^[✅❌]\s*", "", redis_result.splitlines()[0])
    lines = [
        f"{'🟢' if mysql_ok else '🔴'} {mysql_summary}",
    ]
    if not mysql_ok:
        lines.extend(compact_connection_error_lines(mysql_result))
    lines.append(f"{'🟢' if redis_ok else '🔴'} {redis_summary}")
    if not redis_ok:
        lines.extend(compact_connection_error_lines(redis_result))
    lines.append(f"{'🟢' if sqlite_ok else '🔴'} SQLite {sqlite_detail}")
    return (lines, mysql_ok, redis_ok, sqlite_ok)

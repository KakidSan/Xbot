from __future__ import annotations

from .common import (
    Any,
    DEFAULT_CACHE_PATH,
    DEFAULT_CACHE_RETENTION_DAYS,
    DEFAULT_COLLECTOR_INTERVAL_SECONDS,
    DEFAULT_IP_GEO_QUERIES_PER_MINUTE,
    Path,
    _as_int_set,
    dataclass,
    field,
    log,
    os,
)
from .db.cache import auth_roles_load_sync, auth_roles_save_sync


@dataclass
class TelegramConfig:
    bot_token: str
    admin_user_id: int | None = None  # 兼容旧字段：第一个超级管理员。
    super_admin_user_ids: set[int] = field(
        default_factory=set
    )  # 超级管理员，只能通过环境变量修改。
    manager_user_ids: set[int] = field(
        default_factory=set
    )  # 普通管理员，可由超级管理员在 Bot 内管理。
    authorized_user_ids: set[int] = field(default_factory=set)  # 普通授权用户。

    @property
    def admin_user_ids(self) -> set[int]:
        return set(self.super_admin_user_ids) | set(self.manager_user_ids)

    @property
    def allowed_user_ids(self) -> set[int]:
        return (
            set(self.authorized_user_ids)
            | set(self.manager_user_ids)
            | set(self.super_admin_user_ids)
        )


@dataclass
class RedisConfig:
    # host / port 允许留空，用于明确提示“Config 未填写 Redis 信息”。
    host: str = ""
    port: int | None = None
    password: str | None = None
    db: int = 0


@dataclass
class MySQLConfig:
    host: str = ""
    port: int | None = None
    database: str = ""
    username: str = ""
    password: str = ""


@dataclass
class AppConfig:
    telegram: TelegramConfig
    redis: RedisConfig
    mysql: MySQLConfig
    cache_path: Path = Path("data/xbot.sqlite3")
    collector_interval_seconds: float = DEFAULT_COLLECTOR_INTERVAL_SECONDS
    traffic_dashboard_refresh_seconds: float = 60.0
    cache_retention_days: int = DEFAULT_CACHE_RETENTION_DAYS
    ip_geo_queries_per_minute: int = DEFAULT_IP_GEO_QUERIES_PER_MINUTE


def _optional_int(value: Any) -> int | None:
    return int(value) if value not in (None, "") else None


def env_value(name: str, default: Any = None) -> Any:
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def env_int(name: str, default: Any = None) -> Any:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return int(value)


def apply_env_overrides(raw: dict[str, Any]) -> dict[str, Any]:
    """Populate the internal runtime Config from Docker/Compose environment variables."""
    telegram_raw = raw.setdefault("telegram", {})
    redis_raw = raw.setdefault("redis", {})
    mysql_raw = raw.setdefault("mysql", {})
    raw.setdefault("app", {})

    telegram_raw["bot_token"] = env_value(
        "TELEGRAM_BOT_TOKEN", telegram_raw.get("bot_token")
    )
    telegram_raw["admin_user_id"] = env_value(
        "TELEGRAM_ADMIN_USER_ID", telegram_raw.get("admin_user_id")
    )
    telegram_raw["manager_user_ids"] = env_value(
        "TELEGRAM_MANAGER_USER_IDS", telegram_raw.get("manager_user_ids")
    )
    telegram_raw["authorized_user_ids"] = env_value(
        "TELEGRAM_AUTHORIZED_USER_IDS", telegram_raw.get("authorized_user_ids")
    )

    redis_raw["host"] = env_value("REDIS_HOST", redis_raw.get("host"))
    redis_raw["port"] = env_int("REDIS_PORT", redis_raw.get("port"))
    redis_raw["password"] = env_value("REDIS_PASSWORD", redis_raw.get("password"))
    redis_raw["db"] = env_int("REDIS_DB", redis_raw.get("db", 0))
    mysql_raw["host"] = env_value("MYSQL_HOST", mysql_raw.get("host"))
    mysql_raw["port"] = env_int("MYSQL_PORT", mysql_raw.get("port"))
    mysql_raw["database"] = env_value("MYSQL_DATABASE", mysql_raw.get("database"))
    mysql_raw["username"] = env_value("MYSQL_USERNAME", mysql_raw.get("username"))
    mysql_raw["password"] = env_value("MYSQL_PASSWORD", mysql_raw.get("password"))

    return raw


def build_config_from_env() -> AppConfig:
    raw: dict[str, Any] = {"telegram": {}, "redis": {}, "mysql": {}, "app": {}}
    raw = apply_env_overrides(raw)
    telegram_raw = raw.get("telegram") or {}
    redis_raw = raw.get("redis") or {}
    mysql_raw = raw.get("mysql") or {}
    app_raw = raw.get("app") or {}

    token = str(telegram_raw.get("bot_token") or "").strip()
    if not token or token == "123456:replace_me":
        raise ValueError("TELEGRAM_BOT_TOKEN 不能为空，请填写 BotFather 提供的 Token")

    cache_path = Path(str(app_raw.get("cache_path") or DEFAULT_CACHE_PATH)).expanduser()

    admin_raw = telegram_raw.get("admin_user_id")
    super_admin_user_ids = _as_int_set(admin_raw)
    admin_user_id = min(super_admin_user_ids) if super_admin_user_ids else None
    manager_user_ids = _as_int_set(telegram_raw.get("manager_user_ids"))
    authorized_user_ids = _as_int_set(telegram_raw.get("authorized_user_ids"))
    stored_roles = auth_roles_load_sync(cache_path)
    if stored_roles is None:
        auth_roles_save_sync(cache_path, manager_user_ids, authorized_user_ids)
    else:
        manager_user_ids, authorized_user_ids = stored_roles
    manager_user_ids.difference_update(super_admin_user_ids)
    authorized_user_ids.difference_update(super_admin_user_ids)
    authorized_user_ids.difference_update(manager_user_ids)
    allowed_user_ids = (
        set(authorized_user_ids) | set(manager_user_ids) | set(super_admin_user_ids)
    )
    if not allowed_user_ids:
        log.warning("Telegram 授权用户为空，当前将拒绝所有 Telegram 用户访问")

    return AppConfig(
        telegram=TelegramConfig(
            bot_token=token,
            admin_user_id=admin_user_id,
            super_admin_user_ids=super_admin_user_ids,
            manager_user_ids=manager_user_ids,
            authorized_user_ids=authorized_user_ids,
        ),
        redis=RedisConfig(
            host=str(redis_raw.get("host", "") or ""),
            port=_optional_int(redis_raw.get("port")),
            password=redis_raw.get("password") or None,
            db=int(redis_raw.get("db", 0)),
        ),
        mysql=MySQLConfig(
            host=str(mysql_raw.get("host", "") or ""),
            port=_optional_int(mysql_raw.get("port")),
            database=str(mysql_raw.get("database", "") or ""),
            username=str(mysql_raw.get("username", "") or ""),
            password=str(mysql_raw.get("password", "") or ""),
        ),
        cache_path=cache_path,
        collector_interval_seconds=DEFAULT_COLLECTOR_INTERVAL_SECONDS,
        traffic_dashboard_refresh_seconds=60.0,
        cache_retention_days=DEFAULT_CACHE_RETENTION_DAYS,
        ip_geo_queries_per_minute=DEFAULT_IP_GEO_QUERIES_PER_MINUTE,
    )

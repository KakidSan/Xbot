from __future__ import annotations

from collections.abc import Collection, Iterable


def _ids_text(user_ids: Iterable[int]) -> str:
    values = [str(uid) for uid in user_ids]
    return ", ".join(values) or "空"


def alert_category(alert_type: str) -> str:
    return "traffic_alert" if alert_type == "traffic" else "ip_alert"


def alert_type_label(alert_type: str) -> str:
    return "流量告警" if alert_type == "traffic" else "IP 监控"


def alert_setting_before_after_detail(alert_type: str, scope: str, before: str, after: str, xboard_user_id: int | None = None) -> str:
    target = f"XBoard 用户 {xboard_user_id}" if xboard_user_id is not None else "默认规则"
    return f"对象：{target}\n范围：{scope}\n类型：{alert_type_label(alert_type)}\n修改前：{before}\n修改后：{after}"


def auth_change_detail(
    before_managers: Iterable[int],
    after_managers: Iterable[int],
    before_users: Iterable[int],
    after_users: Iterable[int],
    *,
    added_user_id: int | None = None,
    deleted_user_ids: Iterable[int] | None = None,
) -> str:
    lines = [
        f"修改前管理员：{_ids_text(before_managers)}",
        f"修改后管理员：{_ids_text(after_managers)}",
        f"修改前普通用户：{_ids_text(before_users)}",
        f"修改后普通用户：{_ids_text(after_users)}",
    ]
    if added_user_id is not None:
        lines.append(f"新增：{added_user_id}")
    if deleted_user_ids is not None:
        lines.append(f"删除：{_ids_text(sorted(deleted_user_ids))}")
    return "\n".join(lines)


def ip_ignore_detail(
    dimension: str,
    value: str,
    before_values: Collection[str],
    after_values: Collection[str],
    *,
    xboard_user_id: int | None = None,
) -> str:
    lines = [
        f"维度：{dimension}",
        f"对象：{value}",
    ]
    if xboard_user_id is not None:
        lines.append(f"XBoard 用户：{xboard_user_id}")
    lines.extend([
        f"修改前：{'已忽略' if value in before_values else '未忽略'}",
        f"修改后：{'已忽略' if value in after_values else '未忽略'}",
    ])
    return "\n".join(lines)

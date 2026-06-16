from __future__ import annotations

from .common import (
    APP_DIR,
    Any,
    Application,
    FALLBACK_VERSION,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Path,
    TAGS_API_URL,
    UPDATE_SCRIPT,
    UPDATE_STATUS_FILE,
    VERSION_FILE,
    VERSION_TAG_RE,
    asyncio,
    beijing_now,
    html,
    json,
    log,
    re,
    subprocess,
    urllib,
)
from .db.cache import alert_state_set_sync, default_allowlist_notification_chats_sync, get_collector_state_sync

def run_command_sync(args: list[str], cwd: Path = APP_DIR, timeout: int = 20) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(args, cwd=str(cwd), text=True, capture_output=True, timeout=timeout)
    except Exception as exc:
        return 1, "", f"{type(exc).__name__}: {exc}"
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()

def current_git_version_sync() -> tuple[str | None, str | None, bool, str]:
    rc, _, _ = run_command_sync(["git", "rev-parse", "--is-inside-work-tree"])
    if rc != 0:
        return None, None, False, "not_git"
    rc, desc, err = run_command_sync(["git", "describe", "--tags", "--always", "--dirty"])
    if rc != 0:
        return None, None, False, err or "git describe failed"
    rc, commit, _ = run_command_sync(["git", "rev-parse", "--short", "HEAD"])
    dirty = desc.endswith("-dirty")
    return desc, commit if rc == 0 else None, dirty, "git"

def read_app_version() -> str:
    git_version, _, _, source = current_git_version_sync()
    if source == "git" and git_version:
        return git_version
    try:
        version = VERSION_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return FALLBACK_VERSION
    return version or FALLBACK_VERSION

def parse_version_tuple(tag: str) -> tuple[int, int, int, str]:
    tag = tag.strip()
    m = re.match(r"^v(\d+)\.(\d+)\.(\d+)(.*)$", tag)
    if not m:
        return (-1, -1, -1, tag)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4) or "")

def latest_remote_version_sync() -> tuple[str | None, str | None]:
    try:
        req = urllib.request.Request(
            TAGS_API_URL,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "xbot-version-check"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tags = [str(item.get("name") or "").strip() for item in data if isinstance(item, dict)]
        tags = [tag for tag in tags if VERSION_TAG_RE.fullmatch(tag)]
        if tags:
            tags.sort(key=parse_version_tuple, reverse=True)
            return tags[0], None
        return None, "GitHub tags 中没有符合 vX.Y.Z 格式的版本标签。"
    except Exception as exc:
        return None, f"读取 GitHub tags 失败：{type(exc).__name__}: {exc}"

def current_release_tag(version: str | None = None) -> str | None:
    version = version or read_app_version()
    m = re.match(r"^(v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)", version)
    return m.group(1) if m else None

def is_remote_newer(current: str | None, latest: str | None) -> bool:
    if not latest:
        return False
    if not current:
        return True
    return parse_version_tuple(latest) > parse_version_tuple(current)

def version_check_sync() -> dict[str, Any]:
    git_version, commit, dirty, source = current_git_version_sync()
    current = git_version or read_app_version()
    current_tag = current_release_tag(current)
    latest, error = latest_remote_version_sync()
    return {
        "current": current,
        "current_tag": current_tag,
        "commit": commit,
        "dirty": dirty,
        "source": source,
        "latest": latest,
        "error": error,
        "has_update": bool(latest and is_remote_newer(current_tag, latest)),
    }

def version_text(check: dict[str, Any] | None = None, admin_view: bool = True) -> str:
    check = check or {"current": read_app_version(), "latest": None, "error": None, "has_update": False, "source": "local"}
    lines = [
        "🔖 <b>Xbot 版本信息</b>",
        "────────────",
        f"当前版本：<code>{html.escape(str(check.get('current') or read_app_version()))}</code>",
    ]
    if not admin_view:
        return "\n".join(lines)
    commit = check.get("commit")
    if commit:
        lines.append(f"当前提交：<code>{html.escape(str(commit))}</code>")
    if check.get("source"):
        lines.append(f"版本来源：{html.escape(str(check.get('source')))}")
    lines.append(f"本地修改：{'是' if check.get('dirty') else '否'}")
    lines.append("")
    if check.get("error"):
        lines.append(f"更新检查：⚠️ {html.escape(str(check['error']))}")
    elif check.get("latest"):
        lines.append(f"最新版本：<code>{html.escape(str(check['latest']))}</code>")
        lines.append("状态：⬆️ 发现新版本" if check.get("has_update") else "状态：✅ 当前已是最新版本")
    else:
        lines.append("更新检查：未发现远程版本信息")
    lines.append(f"检查时间：{beijing_now().strftime('%Y-%m-%d %H:%M:%S')} 北京时间")
    return "\n".join(lines)

def version_keyboard(check: dict[str, Any] | None = None, admin_view: bool = True) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    latest = str(check.get("latest") or "") if check else ""
    if admin_view and check and check.get("has_update") and VERSION_TAG_RE.fullmatch(latest):
        rows.append([InlineKeyboardButton(f"⬆️ 后台更新到 {latest}", callback_data=f"version_update:start:{latest}")])
    rows.append([InlineKeyboardButton("⬅️ 返回主菜单", callback_data="main_menu"), InlineKeyboardButton("❌ 关闭", callback_data="close_message")])
    return InlineKeyboardMarkup(rows)

def update_confirm_keyboard(target_version: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ 确认后台更新", callback_data=f"version_update:confirm:{target_version}")],
        [InlineKeyboardButton("❌ 取消", callback_data="version_update:cancel")],
    ])

def update_started_text(target_version: str) -> str:
    return "\n".join([
        "⬆️ <b>即将开始后台更新</b>",
        "────────────",
        f"目标版本：<code>{html.escape(target_version)}</code>",
        "",
        "更新过程将会拉取远程代码、更新 Python 依赖并重启 xbot.service。",
        "",
        "⚠️ 即将开始更新，可能会影响数据采集连续性。",
        "请再次确认是否继续。",
    ])

def start_background_update_sync(target_version: str, chat_id: str) -> tuple[bool, str]:
    if not VERSION_TAG_RE.fullmatch(target_version):
        return False, "目标版本无效。"
    if not UPDATE_SCRIPT.exists():
        return False, "更新脚本不存在：scripts/update.sh"
    try:
        UPDATE_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(
            [str(UPDATE_SCRIPT), target_version, chat_id],
            cwd=str(APP_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        return False, f"启动后台更新失败：{type(exc).__name__}: {exc}"
    return True, "后台更新已启动。"

def consume_update_status_sync() -> dict[str, Any] | None:
    if not UPDATE_STATUS_FILE.exists():
        return None
    try:
        data = json.loads(UPDATE_STATUS_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("读取更新状态失败：%s", exc)
        return None
    status = str(data.get("status") or "")
    if status not in {"restarting", "failed"}:
        return None
    if status == "restarting":
        data["status"] = "success"
        data["message"] = "Xbot 已重启，后台更新完成。"
        data["current_version"] = read_app_version()
    try:
        UPDATE_STATUS_FILE.unlink()
    except OSError:
        pass
    return data

def update_result_text(data: dict[str, Any]) -> str:
    status = str(data.get("status") or "")
    if status == "success":
        return "\n".join([
            "✅ <b>Xbot 更新完成</b>",
            "────────────",
            f"目标版本：<code>{html.escape(str(data.get('target_version') or 'unknown'))}</code>",
            f"当前版本：<code>{html.escape(str(data.get('current_version') or read_app_version()))}</code>",
            f"完成时间：{beijing_now().strftime('%Y-%m-%d %H:%M:%S')} 北京时间",
        ])
    return "\n".join([
        "❌ <b>Xbot 更新失败</b>",
        "────────────",
        f"目标版本：<code>{html.escape(str(data.get('target_version') or 'unknown'))}</code>",
        f"失败阶段：<code>{html.escape(str(data.get('stage') or 'unknown'))}</code>",
        f"错误信息：{html.escape(str(data.get('message') or 'unknown'))}",
        "",
        "请通过 SSH 查看：",
        "<code>sudo journalctl -u xbot -f</code>",
    ])

def version_notice_sent_key(latest: str, date_text: str) -> str:
    return f"version_update_notice:{latest}:{date_text}"

def version_notice_already_sent_sync(cache_path: Path, latest: str, date_text: str) -> bool:
    return get_collector_state_sync(cache_path, version_notice_sent_key(latest, date_text)) is not None

def mark_version_notice_sent_sync(cache_path: Path, latest: str, date_text: str) -> None:
    alert_state_set_sync(cache_path, version_notice_sent_key(latest, date_text), "1")

def version_update_notice_text(check: dict[str, Any]) -> str:
    return "\n".join([
        "⬆️ <b>发现 Xbot 新版本</b>",
        "────────────",
        f"当前版本：<code>{html.escape(str(check.get('current') or 'unknown'))}</code>",
        f"最新版本：<code>{html.escape(str(check.get('latest') or 'unknown'))}</code>",
        "",
        "你可以点击下方按钮执行后台更新。",
        "更新前会再次确认。",
    ])

async def send_update_result_notice(app: Application) -> None:
    data = await asyncio.to_thread(consume_update_status_sync)
    if not data:
        return
    chat_id = str(data.get("chat_id") or "")
    if not chat_id:
        log.info("更新状态已读取，但没有 chat_id：%s", data)
        return
    try:
        await app.bot.send_message(chat_id=chat_id, text=update_result_text(data), parse_mode="HTML")
    except Exception as exc:
        log.warning("发送更新结果通知失败 chat=%s：%s", chat_id, exc)

async def version_update_check_loop(app: Application, cfg: AppConfig, cache_path: Path, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            now = beijing_now()
            if now.hour == 12 and now.minute == 0:
                date_text = now.strftime("%Y-%m-%d")
                check = await asyncio.to_thread(version_check_sync)
                latest = str(check.get("latest") or "")
                if check.get("has_update") and latest and not await asyncio.to_thread(version_notice_already_sent_sync, cache_path, latest, date_text):
                    chats = await asyncio.to_thread(default_allowlist_notification_chats_sync, cache_path, cfg, "version_update")
                    for chat_id in chats:
                        try:
                            admin_view = str(chat_id) in {str(uid) for uid in cfg.telegram.super_admin_user_ids}
                            await app.bot.send_message(chat_id=chat_id, text=version_update_notice_text(check), parse_mode="HTML", reply_markup=version_keyboard(check, admin_view=admin_view))
                        except Exception as exc:
                            log.warning("发送版本更新通知失败 chat=%s：%s", chat_id, exc)
                    await asyncio.to_thread(mark_version_notice_sent_sync, cache_path, latest, date_text)
        except Exception as exc:
            log.exception("版本更新检查任务异常：%s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=60)
        except asyncio.TimeoutError:
            continue

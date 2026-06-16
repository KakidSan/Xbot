from __future__ import annotations

from ..common import Update, re
from ..config import AppConfig


def user_id(update: Update) -> int | None:
    return update.effective_user.id if update.effective_user else None


def bot_id_from_token(token: str) -> int | None:
    match = re.match(r"^(\d+):", token.strip())
    return int(match.group(1)) if match else None


def is_bot_self_update(update: Update, cfg: AppConfig) -> bool:
    user = update.effective_user
    if not user:
        return False
    token_bot_id = bot_id_from_token(cfg.telegram.bot_token)
    return bool(getattr(user, "is_bot", False)) or (
        token_bot_id is not None and user.id == token_bot_id
    )


def is_allowed(update: Update, cfg: AppConfig) -> bool:
    uid = user_id(update)
    return uid is not None and uid in cfg.telegram.allowed_user_ids


__all__ = ["bot_id_from_token", "is_allowed", "is_bot_self_update", "user_id"]

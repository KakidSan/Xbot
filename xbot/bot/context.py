from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import AppConfig


@dataclass(slots=True, frozen=True)
class BotContext:
    """Shared immutable runtime dependencies for bot handlers.

    Telegram's per-update ContextTypes.DEFAULT_TYPE is still passed separately by
    python-telegram-bot; this object only carries application-level dependencies
    that were previously captured directly from build_application closures.
    """

    cfg: AppConfig
    cache_path: Path

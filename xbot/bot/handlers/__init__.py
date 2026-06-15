"""Telegram bot handlers package.

The legacy application factory is kept here while the large handler module is
split into focused command/callback modules.
"""

from .legacy import build_application, main

__all__ = ["build_application", "main"]

"""Telegram bot handlers package.

Compatibility exports for older imports that used ``xbot.bot.handlers`` as the
application entrypoint. New code should import from ``xbot.bot.application``.
"""

from typing import Any

__all__ = ["build_application", "main"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from .. import application

        return getattr(application, name)
    raise AttributeError(name)

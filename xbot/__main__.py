from __future__ import annotations
"""Docker/CLI entry point for Xbot."""

from . import config, updater, geo, alerts, collector  # noqa: F401
from .db import cache, mysql, redis  # noqa: F401
from .bot import formatters, keyboards, handlers  # noqa: F401
from .bot.handlers import main

if __name__ == "__main__":
    main()

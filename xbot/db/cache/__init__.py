from __future__ import annotations

# Compatibility facade: keep ``from xbot.db.cache import name`` stable while
# domain modules expose grouped cache helpers for new code.
from ._core import *  # noqa: F403
from .alerts import *  # noqa: F403
from .geo import *  # noqa: F403
from .ip_ignored import *  # noqa: F403
from .notifications import *  # noqa: F403
from .operation_logs import *  # noqa: F403
from .traffic import *  # noqa: F403

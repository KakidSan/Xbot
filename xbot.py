#!/usr/bin/env python3
"""Compatibility wrapper for local `python xbot.py` runs.

Xbot 2.0 Docker images use `python -m xbot`; this wrapper is kept so existing
local development commands still enter the same package runtime.
"""

from xbot.__main__ import main

if __name__ == "__main__":
    main()

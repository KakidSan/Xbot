from __future__ import annotations
"""Runtime symbol linker for the modularized Xbot package.

The former single-file bot had many cross-references.  During the real split,
functions now live in responsibility modules; this linker exposes public symbols
across those modules so behavior remains unchanged while dependencies are
further tightened in future refactors.
"""

REGISTRY: dict[str, object] = {}
MODULE_GLOBALS: list[dict[str, object]] = []

def install_module_symbols(ns: dict[str, object]) -> None:
    MODULE_GLOBALS.append(ns)
    public = {k: v for k, v in ns.items() if not k.startswith("__")}
    REGISTRY.update(public)
    for target in MODULE_GLOBALS:
        target.update(REGISTRY)

"""Fixture cases: build each case's input from a committed base plus a patch (p4 spec AC #6, #14).

A patch has optional `remove` (JSON pointers), `set` (pointer -> value) and `rename`
(pointer -> new last-segment name). The base is deep-copied, so cases never affect each other.
"""

from __future__ import annotations

import copy
from typing import Any


def _parts(ptr: str) -> list[str]:
    if ptr == "":
        return []
    if not ptr.startswith("/"):
        raise ValueError(f"bad JSON pointer {ptr!r}")
    return [p.replace("~1", "/").replace("~0", "~") for p in ptr[1:].split("/")]


def _parent(doc: Any, ptr: str) -> tuple[Any, str]:
    parts = _parts(ptr)
    node = doc
    for p in parts[:-1]:
        node = node[int(p)] if isinstance(node, list) else node[p]
    return node, parts[-1]


def apply_patch(base: Any, patch: dict[str, Any] | None) -> Any:
    doc = copy.deepcopy(base)
    patch = patch or {}
    for ptr in patch.get("remove", []):
        parent, key = _parent(doc, ptr)
        del parent[key]
    for ptr, new_name in (patch.get("rename") or {}).items():
        parent, key = _parent(doc, ptr)
        parent[new_name] = parent.pop(key)
    for ptr, value in (patch.get("set") or {}).items():
        parent, key = _parent(doc, ptr)
        parent[key] = copy.deepcopy(value)
    return doc

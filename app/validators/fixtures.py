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


def signature_case(cases_doc: dict[str, Any], case_id: str) -> dict[str, Any] | None:
    """The case with this ID from signatures/cases.yaml, with its named components resolved."""
    for case in cases_doc.get("cases", []):
        if case["id"] == case_id:
            return {**case, "components": dict(cases_doc["components"][case["components"]])}
    return None


def build_candidate(case: dict[str, Any], keystore: Any) -> str | None:
    """The signature a simulated client would send for this case; None when its key is missing."""
    import base64

    from app.validators import canonical, signature

    recipe = case["candidate"]
    if "literal" in recipe:
        return str(recipe["literal"])
    key = keystore.get(recipe["signed_with"])
    if key is None:
        return None
    signed = {**case["components"], **(recipe.get("sign_overrides") or {})}
    if recipe.get("mistake"):
        message = canonical.MISTAKES[recipe["profile"]][recipe["mistake"]](signed)
    else:
        message = canonical.string_to_sign(recipe["profile"], signed)
    raw = signature.sign(key, message)
    return raw.hex() if recipe.get("encoding") == "hex" else base64.b64encode(raw).decode("ascii")


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

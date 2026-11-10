"""Fixture cases: build each case's input from a committed base plus a patch (p4 spec AC #6, #14).

A patch has optional `remove` (JSON pointers), `rename` (pointer -> new last-segment name) and
`set` (pointer -> value), applied in that order. The base is deep-copied, so cases never affect each other.
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


def response_case(case: dict[str, Any], *, tables_cfg: dict[str, Any], root: Any, excerpt_cap: Any) -> Any:
    """Run one response-code case. `excerpt_cap(synthetic: bool) -> int`. Returns a LookupResult."""
    from app.validators import response_codes

    meta = tables_cfg[case["api_family"]][case["spec_version"]]
    table = None if case.get("table_override") == "missing" else response_codes.load_table(
        root / meta["path"].replace("\\", "/"))
    return response_codes.lookup(case["code"], case["api_family"], case["spec_version"], table=table,
                                 table_meta=meta, excerpt_cap=excerpt_cap(bool(meta.get("synthetic"))))


def response_case_matches(case: dict[str, Any], res: Any) -> bool:
    exp = case["expect"]
    if res.status != exp["status"]:
        return False
    if "message" in exp and (res.match or {}).get("message") != exp["message"]:
        return False
    for k in ("http_status", "case_code"):
        if k in exp and (res.match or {}).get(k) != exp[k]:
            return False
    return "suggestions" not in exp or len(res.suggestions) == exp["suggestions"]


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


# --- the whole fixture set (scripts/eval_validators.py, tests) ----------------------------------

def _pairs(items: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return sorted((i["code"], i["pointer"]) for i in items)


def run_all(index_path: Any, *, schemas_dir: Any, keystore: Any, tables_cfg: dict[str, Any], root: Any,
            excerpt_cap: Any, rules_version: str) -> dict[str, Any]:
    """Run every payload, signature and response-code case named by the fixture index.

    Returns {"validators": {name: {passed, failed, skipped, total, accuracy}}, "cases": [...]}.
    `accuracy` = passed / (passed + failed). A skipped case (SNAP table not extracted) never counts as passed.
    Case records hold IDs, statuses and codes only: no payload values, keys or signatures.
    """
    import yaml

    from app.validators import payload as pv
    from app.validators import signature

    base = index_path.parent
    index = yaml.safe_load(index_path.read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = []

    for rel in index["payloads"]:
        suite = yaml.safe_load((base / rel).read_text(encoding="utf-8"))
        for case in suite["cases"]:
            data = apply_patch(suite["base"], case.get("patch"))
            res = pv.validate(data, suite["endpoint_id"], suite["spec_version"], schemas_dir=schemas_dir,
                              rules_version=rules_version)
            exp = case["expect"]
            ok = (res.valid is exp["valid"] and _pairs(res.errors) == _pairs(exp.get("errors", []))
                  and _pairs(res.warnings) == _pairs(exp.get("warnings", [])))
            cases.append({"validator": "payload", "id": case["id"], "expected": exp["valid"], "actual": res.valid,
                          "actual_codes": [e["code"] for e in res.errors], "result": "passed" if ok else "failed"})

    sig_doc = yaml.safe_load((base / index["signatures"]["cases"]).read_text(encoding="utf-8"))
    for c in sig_doc["cases"]:
        case = signature_case(sig_doc, c["id"])
        cand = build_candidate(case, keystore)
        chk = case["check"]
        if cand is None:
            actual = ("INCONCLUSIVE", "SIG_KEY_UNAVAILABLE")
        else:
            res = signature.verify(algorithm=chk["algorithm"], profile=chk["profile"], fixture_id=chk["fixture_id"],
                                   components=case["components"], candidate=cand, keystore=keystore)
            actual = (res.status, res.diagnostic_code)
        exp = (case["expect"]["status"], case["expect"]["diagnostic_code"])
        cases.append({"validator": "signature", "id": c["id"], "expected": list(exp), "actual": list(actual),
                      "result": "passed" if actual == exp else "failed"})

    rc_doc = yaml.safe_load((base / index["response_codes"]["cases"]).read_text(encoding="utf-8"))
    for case in rc_doc["cases"]:
        meta = tables_cfg[case["api_family"]][case["spec_version"]]
        if case.get("requires_table") and not (root / meta["path"]).is_file():
            cases.append({"validator": "response_codes", "id": case["id"], "expected": case["expect"]["status"],
                          "actual": None, "result": "skipped"})
            continue
        res = response_case(case, tables_cfg=tables_cfg, root=root, excerpt_cap=excerpt_cap)
        cases.append({"validator": "response_codes", "id": case["id"], "expected": case["expect"]["status"],
                      "actual": res.status, "result": "passed" if response_case_matches(case, res) else "failed"})

    summary: dict[str, Any] = {}
    for name in ("payload", "signature", "response_codes"):
        mine = [c for c in cases if c["validator"] == name]
        passed = sum(c["result"] == "passed" for c in mine)
        failed = sum(c["result"] == "failed" for c in mine)
        summary[name] = {"passed": passed, "failed": failed, "skipped": len(mine) - passed - failed,
                         "total": len(mine), "accuracy": round(passed / (passed + failed), 4) if passed + failed else None}
    return {"fixture_set_version": index.get("fixture_set_version"), "validators": summary, "cases": cases}

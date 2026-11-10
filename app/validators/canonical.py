"""Named, versioned canonicalization profiles and named common mistakes (TRD §14.2; p4 spec AC #8-#10).

The LLM never builds or edits a string-to-sign: only the functions here do. A profile change
is a new profile name (`-v2`), never an edit to an existing one.

`snap-asymmetric-access-token-v1`
  stringToSign = clientKey + "|" + X-TIMESTAMP, signed SHA256withRSA.
  Source: snap-technical-security-standard 1.0.2 §2.1.5(a).
  ASSUMPTION (not stated on the cited page): PKCS#1 v1.5 padding, base64 signature.

`synthetic-snap-symmetric-v1`
  stringToSign = METHOD:path:accessToken:lower(hex(sha256(minified body))):X-TIMESTAMP, HMAC_SHA512, base64.
  Source: synthetic-qris-mpm-service-spec 0.1.0 §1.4 (SYNTHETIC).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

Components = dict[str, Any]


class CanonicalError(ValueError):
    """Components cannot produce a string-to-sign (missing field, body not JSON). Maps to INCONCLUSIVE."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def minify(body: str, *, sort_keys: bool = False, ensure_ascii: bool = False) -> str:
    """Re-serialise JSON with no insignificant whitespace, key order as received (§1.4 step 1)."""
    try:
        obj = json.loads(body)
    except (TypeError, ValueError):
        raise CanonicalError("SIG_BODY_NOT_JSON") from None
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=ensure_ascii, sort_keys=sort_keys)


def body_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _need(c: Components, *names: str) -> None:
    for n in names:
        if not isinstance(c.get(n), str) or c[n] == "":
            raise CanonicalError("SIG_MISSING_COMPONENT")


# --- profiles --------------------------------------------------------------------------------

def _asym_v1(c: Components) -> str:
    _need(c, "client_key", "timestamp")
    return f"{c['client_key']}|{c['timestamp']}"


def _sym_v1(c: Components) -> str:
    _need(c, "method", "path", "access_token", "timestamp", "body")
    return ":".join([c["method"].upper(), c["path"], c["access_token"], body_hash(minify(c["body"])), c["timestamp"]])


@dataclass(frozen=True)
class Profile:
    name: str
    version: str
    algorithm: str
    key_type: str                       # rsa | hmac
    build: Callable[[Components], str]
    source: dict[str, str]
    assumptions: tuple[str, ...] = ()


PROFILES: dict[str, Profile] = {
    "snap-asymmetric-access-token-v1": Profile(
        "snap-asymmetric-access-token-v1", "1", "SHA256withRSA", "rsa", _asym_v1,
        {"document_id": "snap-technical-security-standard", "document_version": "1.0.2", "section": "2.1.5(a)"},
        ("PKCS#1 v1.5 padding and base64 encoding are project assumptions; the cited page does not state them",)),
    "synthetic-snap-symmetric-v1": Profile(
        "synthetic-snap-symmetric-v1", "1", "HMAC_SHA512", "hmac", _sym_v1,
        {"document_id": "synthetic-qris-mpm-service-spec", "document_version": "0.1.0", "section": "1.4"}),
}


# --- named mistakes: each builds the string a client with that bug would have signed -----------

def _with(c: Components, **kw: Any) -> Components:
    return {**c, **kw}


def _sym_from_body_text(c: Components, text: str) -> str:
    return ":".join([c["method"].upper(), c["path"], c["access_token"], body_hash(text), c["timestamp"]])


MISTAKES: dict[str, dict[str, Callable[[Components], str | None]]] = {
    "synthetic-snap-symmetric-v1": {
        "SIG_BODY_NOT_MINIFIED": lambda c: _sym_from_body_text(c, c["body"]),
        "SIG_BODY_KEYS_REORDERED": lambda c: _sym_from_body_text(c, minify(c["body"], sort_keys=True)),
        "SIG_BODY_ASCII_ESCAPED": lambda c: _sym_from_body_text(c, minify(c["body"], ensure_ascii=True)),
        "SIG_PATH_INCLUDES_HOST": lambda c: _sym_v1(_with(c, path=f"https://{c['host']}{c['path']}"))
        if isinstance(c.get("host"), str) and c["host"] else None,
        "SIG_METHOD_NOT_UPPERCASE": lambda c: _sym_v1(c).replace(c["method"].upper(), c["method"].lower(), 1),
        "SIG_BODY_HASH_UPPERCASE": lambda c: ":".join([c["method"].upper(), c["path"], c["access_token"],
                                                       body_hash(minify(c["body"])).upper(), c["timestamp"]]),
        "SIG_TRAILING_NEWLINE": lambda c: _sym_v1(c) + "\n",
    },
    "snap-asymmetric-access-token-v1": {
        "SIG_WRONG_SEPARATOR": lambda c: f"{c['client_key']}:{c['timestamp']}",
        "SIG_COMPONENTS_SWAPPED": lambda c: f"{c['timestamp']}|{c['client_key']}",
        "SIG_TRAILING_NEWLINE": lambda c: _asym_v1(c) + "\n",
    },
}


def string_to_sign(profile: str, c: Components) -> str:
    return PROFILES[profile].build(c)


def mistake_strings(profile: str, c: Components) -> list[tuple[str, str]]:
    """(code, string) for every named mistake that yields a string different from the canonical one."""
    canonical = string_to_sign(profile, c)
    out = []
    for code, fn in MISTAKES[profile].items():
        try:
            s = fn(c)
        except CanonicalError:
            continue
        if s is not None and s != canonical:
            out.append((code, s))
    return out

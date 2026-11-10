"""Test-signature validator (TRD §14.2; p4 spec AC #9, #10).

Verifies a candidate signature against a test key with a named profile:
  VALID         canonical string-to-sign, base64, the named key
  INVALID       anything else; the diagnostic names the common mistake that reproduces it, if one does
  INCONCLUSIVE  missing key, algorithm/profile/key mismatch, missing component or body not JSON

A result never contains the string-to-sign, a key or a signature. It carries the SHA-256 of the
canonical string-to-sign for traceability.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from app.validators import canonical
from app.validators.keys import Keystore, TestKey

VALID, INVALID, INCONCLUSIVE = "VALID", "INVALID", "INCONCLUSIVE"
ALGORITHMS = ("SHA256withRSA", "HMAC_SHA512")


@dataclass
class SignatureResult:
    status: str
    diagnostic_code: str
    algorithm: str
    profile: str
    profile_version: str | None
    fixture_id: str
    string_to_sign_sha256: str | None = None
    assumptions: list[str] = field(default_factory=list)
    source: dict[str, str] | None = None
    synthetic_test_material: bool = True

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def sign(key: TestKey, message: str) -> bytes:
    data = message.encode("utf-8")
    if key.key_type == "rsa":
        return key.material.sign(data, padding.PKCS1v15(), hashes.SHA256())
    return hmac.new(key.material, data, hashlib.sha512).digest()


def _matches(key: TestKey, message: str, sig: bytes) -> bool:
    data = message.encode("utf-8")
    if key.key_type == "rsa":
        try:
            key.material.public_key().verify(sig, data, padding.PKCS1v15(), hashes.SHA256())
            return True
        except InvalidSignature:
            return False
    return hmac.compare_digest(hmac.new(key.material, data, hashlib.sha512).digest(), sig)


def _b64(candidate: str) -> bytes | None:
    try:
        return base64.b64decode(candidate, validate=True) if candidate else None
    except (binascii.Error, ValueError):
        return None


def _hex(candidate: str) -> bytes | None:
    try:
        return bytes.fromhex(candidate) if candidate else None
    except ValueError:
        return None


def verify(*, algorithm: str, profile: str, fixture_id: str, components: canonical.Components,
           candidate: str, keystore: Keystore) -> SignatureResult:
    prof = canonical.PROFILES.get(profile)
    result = SignatureResult(INCONCLUSIVE, "", algorithm, profile, prof.version if prof else None, fixture_id,
                             assumptions=list(prof.assumptions) if prof else [], source=prof.source if prof else None)

    def inconclusive(code: str) -> SignatureResult:
        result.status, result.diagnostic_code = INCONCLUSIVE, code
        return result

    if prof is None or algorithm not in ALGORITHMS or prof.algorithm != algorithm:
        return inconclusive("SIG_PROFILE_ALGORITHM_MISMATCH")
    key = keystore.get(fixture_id)
    if key is None:
        return inconclusive("SIG_KEY_UNAVAILABLE")
    if key.key_type != prof.key_type:
        return inconclusive("SIG_KEY_TYPE_MISMATCH")
    try:
        sts = canonical.string_to_sign(profile, components)
    except canonical.CanonicalError as exc:
        return inconclusive(exc.code)
    result.string_to_sign_sha256 = hashlib.sha256(sts.encode("utf-8")).hexdigest()
    result.status = INVALID

    raw = _b64(candidate)
    if raw is not None and _matches(key, sts, raw):
        result.status, result.diagnostic_code = VALID, "SIG_VALID"
        return result
    as_hex = _hex(candidate)
    if as_hex is not None and _matches(key, sts, as_hex):
        result.diagnostic_code = "SIG_HEX_NOT_BASE64"
        return result
    if raw is not None:
        for code, wrong in canonical.mistake_strings(profile, components):
            if _matches(key, wrong, raw):
                result.diagnostic_code = code
                return result
        for other_id in keystore.ids(prof.key_type):
            other = keystore.get(other_id) if other_id != fixture_id else None
            if other is not None and _matches(other, sts, raw):
                result.diagnostic_code = "SIG_WRONG_KEY"
                return result
        result.diagnostic_code = "SIG_NO_KNOWN_MISTAKE"
        return result
    result.diagnostic_code = "SIG_NOT_BASE64"
    return result

"""Test-only key material, generated at runtime and never committed (scope.md §4; p4 spec Data).

Layout of the key directory (git-ignored `knowledge_base/fixtures/signatures/keys/`, or a temp dir):
  index.json             {fixture_id: "rsa" | "hmac"}
  <fixture_id>.pem       RSA-2048 private key, PKCS#8, unencrypted (test only)
  <fixture_id>.hmac      64 random bytes, hex

File names are built from a fixture ID that matched FIXTURE_ID; nothing read from a file or an
argument is ever used as a path.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

FIXTURE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
KEY_TYPES = ("rsa", "hmac")


@dataclass(frozen=True)
class TestKey:
    fixture_id: str
    key_type: str
    material: Any = None               # RSAPrivateKey or bytes; never serialised into a result

    def __repr__(self) -> str:          # keep key material out of logs and tracebacks
        return f"TestKey({self.fixture_id!r}, {self.key_type!r})"


def _write_private(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    try:
        os.chmod(path, 0o600)
    except OSError:                     # Windows: ACLs, not mode bits
        pass


def generate(keys_dir: Path, wanted: dict[str, str], *, force: bool = False) -> list[str]:
    """Create any missing keys in `wanted` ({fixture_id: type}). Returns the IDs written."""
    keys_dir.mkdir(parents=True, exist_ok=True)
    index_path = keys_dir / "index.json"
    index: dict[str, str] = {} if force or not index_path.is_file() else json.loads(index_path.read_text("utf-8"))
    written = []
    for fid, ktype in wanted.items():
        if not FIXTURE_ID.match(fid) or ktype not in KEY_TYPES:
            raise ValueError(f"bad key spec: {fid!r} {ktype!r}")
        if index.get(fid) == ktype and (keys_dir / f"{fid}.{'pem' if ktype == 'rsa' else 'hmac'}").is_file():
            continue
        if ktype == "rsa":
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
            _write_private(keys_dir / f"{fid}.pem", pem)
        else:
            _write_private(keys_dir / f"{fid}.hmac", secrets.token_bytes(64).hex().encode("ascii"))
        index[fid] = ktype
        written.append(fid)
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return written


class Keystore:
    def __init__(self, keys_dir: Path):
        self.dir = keys_dir
        index_path = keys_dir / "index.json"
        try:
            raw = json.loads(index_path.read_text("utf-8")) if index_path.is_file() else {}
        except (OSError, ValueError):
            raw = {}
        self._index = {k: v for k, v in raw.items() if isinstance(k, str) and FIXTURE_ID.match(k) and v in KEY_TYPES}
        self._cache: dict[str, TestKey] = {}

    def ids(self, key_type: str | None = None) -> list[str]:
        return sorted(k for k, v in self._index.items() if key_type is None or v == key_type)

    def get(self, fixture_id: str) -> TestKey | None:
        if not FIXTURE_ID.match(fixture_id) or fixture_id not in self._index:
            return None
        if fixture_id in self._cache:
            return self._cache[fixture_id]
        ktype = self._index[fixture_id]
        try:
            if ktype == "rsa":
                material = serialization.load_pem_private_key((self.dir / f"{fixture_id}.pem").read_bytes(), None)
            else:
                material = bytes.fromhex((self.dir / f"{fixture_id}.hmac").read_text("ascii").strip())
        except (OSError, ValueError):
            return None
        key = TestKey(fixture_id, ktype, material)
        self._cache[fixture_id] = key
        return key

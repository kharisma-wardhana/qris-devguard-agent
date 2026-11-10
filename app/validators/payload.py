"""Payload validator: versioned JSON Schema plus separate business rules (TRD §14.1; p4 spec AC #6, #7).

- Input is `{"headers": {...}, "body": {...}}`, already sanitized by the caller.
- Header names match case-insensitively; the caller's object is never modified.
- Errors carry a machine-readable code, an RFC 6901 JSON pointer and a fixed message. They never
  echo the submitted value (a header may be a token or signature).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from app.validators import rules

_CODES = {
    "required": "SCHEMA_REQUIRED",
    "type": "SCHEMA_TYPE",
    "const": "SCHEMA_ENUM",
    "enum": "SCHEMA_ENUM",
    "pattern": "SCHEMA_FORMAT",
    "maxLength": "SCHEMA_MAX_LENGTH",
    "minLength": "SCHEMA_MIN_LENGTH",
    "additionalProperties": "SCHEMA_ADDITIONAL_PROPERTY",
}
_MESSAGES = {
    "SCHEMA_REQUIRED": "required field is missing",
    "SCHEMA_TYPE": "field has the wrong type",
    "SCHEMA_ENUM": "field is not one of the allowed values",
    "SCHEMA_FORMAT": "field does not match the required format",
    "SCHEMA_MAX_LENGTH": "field is longer than allowed",
    "SCHEMA_MIN_LENGTH": "field is shorter than allowed",
    "SCHEMA_ADDITIONAL_PROPERTY": "field is not defined for this endpoint",
    "SCHEMA_INVALID": "field is invalid",
    "HEADER_DUPLICATE": "header appears more than once (names are case-insensitive)",
}


class UnknownSchema(LookupError):
    """No schema for this endpoint/version pair. The caller must refuse, never pick a 'closest' one."""


@dataclass
class PayloadResult:
    endpoint_id: str
    spec_version: str
    valid: bool
    schema_id: str
    schema_version: str
    rules_version: str
    synthetic: bool
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def pointer(parts: list[Any]) -> str:
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


@lru_cache(maxsize=16)
def load_schema(schemas_dir: Path, endpoint_id: str, spec_version: str) -> dict[str, Any]:
    path = schemas_dir / endpoint_id / f"{spec_version}.json"
    if not path.is_file():
        raise UnknownSchema(f"{endpoint_id} {spec_version}")
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


def _normalise_headers(headers: Any, declared: list[str]) -> tuple[Any, list[dict], list[dict]]:
    """New dict with declared header names in their canonical case; the input is not touched."""
    if not isinstance(headers, dict):
        return headers, [], []
    canon = {name.lower(): name for name in declared}
    out: dict[str, Any] = {}
    errors, warnings, seen = [], [], set()
    for name, value in headers.items():
        key = canon.get(str(name).lower(), name)
        if str(key).lower() in seen:
            errors.append({"code": "HEADER_DUPLICATE", "pointer": pointer(["headers", key]),
                           "message": _MESSAGES["HEADER_DUPLICATE"]})
            continue
        seen.add(str(key).lower())
        out[key] = value
        if key not in declared:
            warnings.append({"code": "HEADER_NOT_IN_SPEC", "pointer": pointer(["headers", key]),
                             "message": "header is not defined for this endpoint"})
    return out, errors, warnings


def _schema_errors(validator: Draft202012Validator, instance: Any) -> list[dict[str, Any]]:
    out = []
    for err in validator.iter_errors(instance):
        code = _CODES.get(err.validator, "SCHEMA_INVALID")
        path = list(err.absolute_path)
        if err.validator == "required":
            for name in err.validator_value:
                if isinstance(err.instance, dict) and name not in err.instance:
                    out.append({"code": code, "pointer": pointer(path + [name]), "message": _MESSAGES[code]})
        elif err.validator == "additionalProperties" and err.validator_value is False:
            allowed = set(err.schema.get("properties", {}))
            for name in err.instance:
                if name not in allowed:
                    out.append({"code": code, "pointer": pointer(path + [name]), "message": _MESSAGES[code]})
        else:
            out.append({"code": code, "pointer": pointer(path), "message": _MESSAGES[code]})
    return sorted(out, key=lambda e: (e["pointer"], e["code"]))


def _declared_headers(schema: dict[str, Any]) -> list[str]:
    node = schema["properties"]["headers"]
    if "$ref" in node:
        node = schema["$defs"][node["$ref"].rsplit("/", 1)[-1]]
    return list(node.get("properties", {}))


def validate(payload: Any, endpoint_id: str, spec_version: str, *, schemas_dir: Path,
             rules_version: str) -> PayloadResult:
    schema = load_schema(schemas_dir, endpoint_id, spec_version)
    result = PayloadResult(
        endpoint_id=endpoint_id, spec_version=spec_version, valid=False, schema_id=schema["$id"],
        schema_version=schema["x-schema-version"], rules_version=rules_version,
        synthetic=bool(schema.get("x-synthetic", False)),
    )
    if isinstance(payload, dict):
        headers, h_errors, h_warnings = _normalise_headers(payload.get("headers"), _declared_headers(schema))
        normalised = {**payload, "headers": headers} if "headers" in payload else dict(payload)
    else:
        normalised, h_errors, h_warnings = payload, [], []
    errors = h_errors + _schema_errors(Draft202012Validator(schema), normalised)
    if isinstance(normalised, dict) and isinstance(normalised.get("headers"), dict):
        errors += rules.run(endpoint_id, normalised)
    result.errors = errors
    result.warnings = h_warnings
    result.valid = not errors
    return result

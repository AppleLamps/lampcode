from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def load_output_schema(source: str | Path) -> dict[str, Any]:
    if isinstance(source, Path) or (isinstance(source, str) and Path(source).is_file()):
        path = Path(source)
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = json.loads(str(source))
    if not isinstance(data, dict):
        raise ValueError("output schema must be a JSON object")
    return data


def extract_json_from_text(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if text.startswith("{") and text.endswith("}"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass
    brace = re.search(r"(\{.*\})", text, re.S)
    if brace:
        try:
            return json.loads(brace.group(1))
        except json.JSONDecodeError:
            pass
    return None


def validate_against_schema(data: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected_type = schema.get("type")
    if expected_type == "object":
        if not isinstance(data, dict):
            return ["root must be an object"]
        required = schema.get("required", [])
        if isinstance(required, list):
            for key in required:
                if key not in data:
                    errors.append(f"missing required field: {key}")
        props = schema.get("properties", {})
        if isinstance(props, dict):
            for key, prop_schema in props.items():
                if key in data and isinstance(prop_schema, dict):
                    ptype = prop_schema.get("type")
                    val = data[key]
                    if ptype == "string" and not isinstance(val, str):
                        errors.append(f"{key} must be string")
                    elif ptype == "array" and not isinstance(val, list):
                        errors.append(f"{key} must be array")
                    elif ptype == "number" and not isinstance(val, (int, float)):
                        errors.append(f"{key} must be number")
                    elif ptype == "integer" and not isinstance(val, int):
                        errors.append(f"{key} must be integer")
                    elif ptype == "boolean" and not isinstance(val, bool):
                        errors.append(f"{key} must be boolean")
                    elif ptype == "object" and not isinstance(val, dict):
                        errors.append(f"{key} must be object")
    return errors


def parse_final_output(text: str, schema: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    data = extract_json_from_text(text)
    if data is None:
        return None, ["could not parse JSON from assistant message"]
    errors = validate_against_schema(data, schema)
    if errors:
        return None, errors
    return data, []

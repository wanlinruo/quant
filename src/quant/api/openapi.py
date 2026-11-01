"""输出接口的 OpenAPI 文档，或由它生成前端的 TypeScript 类型。

用法：
  python -m quant.api.openapi          # OpenAPI JSON
  python -m quant.api.openapi --ts     # TypeScript 类型（即 make api-types → web/src/api/types.ts）
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from typing import Any

from quant.api.app import create_app
from quant.api.schemas import MODES

HEADER = "// 由 python -m quant.api.openapi --ts 生成（make api-types），请勿手改\n\n"


def render() -> str:
    return json.dumps(create_app().openapi(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def _ts_type(schema: Mapping[str, Any] | bool) -> str:
    if schema is True or schema == {}:
        return "unknown"
    assert isinstance(schema, Mapping)
    if "$ref" in schema:
        return str(schema["$ref"]).rsplit("/", 1)[-1]
    if "enum" in schema:
        return " | ".join(json.dumps(v, ensure_ascii=False) for v in schema["enum"])
    if "const" in schema:
        return json.dumps(schema["const"], ensure_ascii=False)
    for key in ("anyOf", "oneOf"):
        if key in schema:
            return " | ".join(_ts_type(s) for s in schema[key])
    t = schema.get("type")
    if t == "array":
        inner = _ts_type(schema.get("items", {}))
        return f"({inner})[]" if " | " in inner else f"{inner}[]"
    if t == "object":
        extra = schema.get("additionalProperties")
        if extra is not None and not schema.get("properties"):
            return f"Record<string, {_ts_type(extra)}>"
        if not schema.get("properties"):
            return "Record<string, unknown>"
    return {
        "string": "string",
        "integer": "number",
        "number": "number",
        "boolean": "boolean",
        "null": "null",
    }.get(str(t), "unknown")


def render_ts_schemas(schemas: Mapping[str, Mapping[str, Any]]) -> str:
    out = []
    for name in sorted(schemas):
        sch = schemas[name]
        if "properties" not in sch:
            out.append(f"export type {name} = {_ts_type(sch)};\n")
            continue
        required = set(sch.get("required", []))
        lines = [f"export interface {name} {{"]
        for prop, ps in sch["properties"].items():
            opt = "" if prop in required else "?"
            lines.append(f"  {prop}{opt}: {_ts_type(ps)};")
        lines.append("}\n")
        out.append("\n".join(lines))
    return "\n".join(out)


def render_ts() -> str:
    schemas = create_app().openapi().get("components", {}).get("schemas", {})
    mode = " | ".join(json.dumps(m) for m in MODES)
    return HEADER + f"export type Mode = {mode};\n\n" + render_ts_schemas(schemas)


if __name__ == "__main__":
    sys.stdout.write(render_ts() if "--ts" in sys.argv[1:] else render())

"""输出接口的 OpenAPI 文档，前端类型由此生成。

用法：python -m quant.api.openapi > web/src/api/openapi.json（即 make api-types）
"""

from __future__ import annotations

import json
import sys

from quant.api.app import create_app


def render() -> str:
    return json.dumps(create_app().openapi(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"


if __name__ == "__main__":
    sys.stdout.write(render())

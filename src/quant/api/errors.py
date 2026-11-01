"""请求校验错误（422）转成“字段名：中文说明”的列表，与回测提交的业务校验格式一致。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

FIELD_LABELS = {
    "strategy": "策略",
    "start": "开始日期",
    "end": "结束日期",
    "universe": "标的池",
    "safe_asset": "避险资产",
    "lookback": "动量回看",
    "top_k": "持有数量",
    "sma_window": "均线窗口",
    "cash": "初始资金",
    "costs": "成本参数",
    "commission_per_share": "每股佣金",
    "min_commission": "最低佣金",
    "slippage": "滑点",
    "ids": "回测编号",
    "mode": "账户",
    "limit": "条数",
    "month": "月份",
    "period": "区间",
}
_LOCATIONS = {"body", "query", "path", "header", "cookie"}


def _label(loc: Sequence[Any]) -> str:
    names = [str(x) for x in loc if isinstance(x, str) and x not in _LOCATIONS]
    if not names:
        return "请求"
    return FIELD_LABELS.get(names[-1], names[-1])


def _reason(err: dict[str, Any]) -> str:
    t = str(err.get("type", ""))
    if t == "missing":
        return "必填"
    if (
        t.endswith("_type")
        or t.endswith("_parsing")
        or t in {"decimal_type", "date_from_datetime_parsing"}
    ):
        return "格式不正确"
    if t in {"literal_error", "enum"}:
        return "取值无效"
    if t == "string_pattern_mismatch":
        return "格式不正确"
    if t == "too_long":
        return "数量过多"
    if t == "json_invalid":
        return "请求内容不是合法的 JSON"
    return str(err.get("msg", "不合法"))


def to_messages(errors: Sequence[Any]) -> list[str]:
    return [f"{_label(e.get('loc', ()))}：{_reason(e)}" for e in errors]


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    return JSONResponse(status_code=422, content={"detail": to_messages(exc.errors())})

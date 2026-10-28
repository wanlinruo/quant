"""对账（PROJECT_PLAN 6.4、第 8 章）：本地记录的持仓 vs 券商持仓。

本地应有持仓 = 最近一次持仓快照 + 快照日之后（美东日期）记录的成交。
不一致时：把熔断开关 trading_enabled 置为 false 并记录原因，停止交易，等待人工处理。
自动操作只会关闭开关（更保守），重新开启必须人工在数据库中修改。

执行前（daily_execute）和日报（daily_report）各对账一次；日报对账通过后写入当日快照。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from quant.broker.base import Broker
from quant.core.models import AccountSnapshot, Position
from quant.db.models import AccountSnapshotRow, Fill, PositionSnapshot, SystemFlag

ET = ZoneInfo("America/New_York")
TRADING_ENABLED = "trading_enabled"
HALT_REASON = "halt_reason"


@dataclass(frozen=True)
class Mismatch:
    symbol: str
    expected: int
    actual: int

    def __str__(self) -> str:
        return f"{self.symbol}: 本地 {self.expected} 股，券商 {self.actual} 股"


@dataclass
class ReconcileResult:
    expected: dict[str, int]
    actual: dict[str, int]
    mismatches: list[Mismatch] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.mismatches

    def summary(self) -> str:
        return "对账一致" if self.ok else "对账不一致：" + "；".join(map(str, self.mismatches))


def expected_positions(session: Session, mode: str) -> dict[str, int]:
    last = session.scalar(
        select(func.max(PositionSnapshot.snapshot_date)).where(PositionSnapshot.mode == mode)
    )
    qty: dict[str, int] = {}
    if last is not None:
        for p in session.scalars(
            select(PositionSnapshot).where(
                PositionSnapshot.mode == mode, PositionSnapshot.snapshot_date == last
            )
        ):
            qty[p.symbol] = p.qty
    fills = session.scalars(select(Fill).where(Fill.mode == mode).order_by(Fill.filled_at))
    for f in fills:
        if last is not None and f.filled_at.astimezone(ET).date() <= last:
            continue  # 已包含在快照中
        sign = 1 if f.side == "BUY" else -1
        qty[f.symbol] = qty.get(f.symbol, 0) + sign * f.qty
    return {s: q for s, q in qty.items() if q != 0}


def compare(expected: Mapping[str, int], actual: Mapping[str, int]) -> list[Mismatch]:
    return [
        Mismatch(s, expected.get(s, 0), actual.get(s, 0))
        for s in sorted(set(expected) | set(actual))
        if expected.get(s, 0) != actual.get(s, 0)
    ]


def _set_flag(session: Session, key: str, value: str) -> None:
    stmt = insert(SystemFlag).values(key=key, value=value)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=[SystemFlag.key], set_={"value": value, "updated_at": func.now()}
        )
    )


def halt_trading(session: Session, reason: str) -> None:
    """关闭熔断开关并记录原因（只会让系统更保守）。"""
    _set_flag(session, TRADING_ENABLED, "false")
    _set_flag(session, HALT_REASON, f"{datetime.now(UTC):%Y-%m-%d %H:%M}Z {reason}"[:256])


def trading_enabled(session: Session) -> bool:
    flag = session.get(SystemFlag, TRADING_ENABLED)
    return flag is not None and flag.value == "true"


def reconcile(session: Session, broker: Broker, mode: str) -> ReconcileResult:
    """对账；不一致时关闭熔断开关。调用方负责提交事务并告警。"""
    expected = expected_positions(session, mode)
    actual = {s: p.qty for s, p in broker.get_positions().items()}
    result = ReconcileResult(expected, actual, compare(expected, actual))
    if not result.ok:
        halt_trading(session, result.summary())
    return result


def record_snapshot(
    session: Session,
    mode: str,
    day: date,
    account: AccountSnapshot,
    positions: Mapping[str, Position],
) -> None:
    """写入（或覆盖）当日账户与持仓快照。"""
    acct = insert(AccountSnapshotRow).values(
        snapshot_date=day, mode=mode, cash=account.cash, net_liquidation=account.net_liquidation
    )
    session.execute(
        acct.on_conflict_do_update(
            index_elements=[AccountSnapshotRow.snapshot_date, AccountSnapshotRow.mode],
            set_={"cash": account.cash, "net_liquidation": account.net_liquidation},
        )
    )
    session.query(PositionSnapshot).filter_by(snapshot_date=day, mode=mode).delete()
    session.add_all(
        PositionSnapshot(
            snapshot_date=day,
            mode=mode,
            symbol=p.symbol,
            qty=p.qty,
            avg_cost=p.avg_cost,
            market_value=p.market_value,
        )
        for p in positions.values()
    )

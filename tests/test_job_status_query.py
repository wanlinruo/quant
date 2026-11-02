"""系统任务状态：每个账户、每个任务最近一次运行，低频任务也要显示（#73）。"""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api_fixtures import needs_db
from quant.api.queries import job_status
from quant.db.models import JobRun


@needs_db
def test_low_frequency_job_beyond_500_rows(db_url: str) -> None:
    eng = create_engine(db_url)
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    with Session(eng) as s, s.begin():
        s.add(
            JobRun(
                mode="paper",
                job_name="rare",
                trade_date=date(2025, 1, 2),
                status="success",
                started_at=t0 - timedelta(days=300),
            )
        )
        for i in range(600):
            d = date(2024, 1, 1) + timedelta(days=i)
            s.add(
                JobRun(
                    mode="paper",
                    job_name="often",
                    trade_date=d,
                    status="success",
                    started_at=t0 + timedelta(hours=i),
                )
            )
    with Session(eng) as s:
        rows = {r.job_name: r for r in job_status(s, "paper")}
        assert set(rows) == {"often", "rare"}
        assert rows["often"].trade_date == date(2024, 1, 1) + timedelta(days=599)
        assert [r.job_name for r in job_status(s)] == ["often", "rare"]  # 新的在前
    eng.dispose()

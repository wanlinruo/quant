"""模块分层（#79）：任务与领域模块不导入接口层；接口层不导入任务模块。"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "quant"


def imports(rel: str) -> set[str]:
    tree = ast.parse((SRC / rel).read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
        elif isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
    return out


def test_market_does_not_import_api() -> None:
    for rel in ("market/board.py", "market/job.py", "market/stock_job.py", "market/extras_job.py"):
        assert not any(m.startswith("quant.api") for m in imports(rel)), rel


def test_api_does_not_import_jobs() -> None:
    for rel in ("api/stock_extras.py", "api/routes/market.py"):
        bad = {
            "quant.market.stock_job",
            "quant.market.extras_job",
            "quant.market.job",
            "quant.market.board",
        }
        assert not (imports(rel) & bad), rel

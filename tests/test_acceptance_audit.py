"""Phase 4 生产验收审计必须保持只读，并完整消费远端 stdin 脚本。"""

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "acceptance" / "audit_phase4.sh"


def test_phase4_audit_is_read_only() -> None:
    text = SCRIPT.read_text()
    sql = "\n".join(re.findall(r'-c "(.*?)" < /dev/null', text, flags=re.DOTALL))

    assert text.count("exec -T postgres psql") == 4
    assert len(re.findall(r'-c ".*?" < /dev/null', text, flags=re.DOTALL)) == 4
    assert not re.search(r"\b(insert|update|delete|alter|drop|truncate|copy)\b", sql, re.I)
    assert "quant job" not in text
    assert "remote_deploy" not in text

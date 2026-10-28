import pytest

from quant.core import safety
from quant.core.safety import (
    OrderNotAllowedError,
    check_order_allowed,
    is_paper_account,
    mask_account,
)

PAPER = "2" * 17
LIVE = "U1234567"


@pytest.fixture(autouse=True)
def _server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(safety, "is_dev_machine", lambda: False)


def test_paper_account_detection() -> None:
    assert is_paper_account(PAPER)
    assert not is_paper_account(LIVE)
    assert not is_paper_account("2" * 16)
    assert not is_paper_account(None)


def test_mask_account() -> None:
    assert mask_account(PAPER) == "***2222"
    assert mask_account("123") == "***"
    assert mask_account(None) == "***"


@pytest.mark.parametrize("mode", ["sim", "paper", "live"])
def test_sim_broker_always_allowed(mode: str) -> None:
    check_order_allowed(mode, "sim", None)  # type: ignore[arg-type]


def test_tiger_refused_in_sim_mode() -> None:
    with pytest.raises(OrderNotAllowedError, match="sim"):
        check_order_allowed("sim", "tiger", PAPER)


def test_paper_mode_requires_paper_account() -> None:
    check_order_allowed("paper", "tiger", PAPER)
    with pytest.raises(OrderNotAllowedError) as e:
        check_order_allowed("paper", "tiger", LIVE)
    assert LIVE not in str(e.value)
    with pytest.raises(OrderNotAllowedError):
        check_order_allowed("paper", "tiger", None)


def test_live_mode_requires_live_account() -> None:
    check_order_allowed("live", "tiger", LIVE)
    with pytest.raises(OrderNotAllowedError) as e:
        check_order_allowed("live", "tiger", PAPER)
    assert PAPER not in str(e.value)
    with pytest.raises(OrderNotAllowedError):
        check_order_allowed("live", "tiger", None)


def test_live_refused_on_dev_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(safety, "is_dev_machine", lambda: True)
    with pytest.raises(OrderNotAllowedError, match="开发机"):
        check_order_allowed("live", "tiger", LIVE)


def test_unknown_values_refused() -> None:
    with pytest.raises(OrderNotAllowedError):
        check_order_allowed("paper", "ib", PAPER)
    with pytest.raises(OrderNotAllowedError):
        check_order_allowed("prod", "tiger", LIVE)  # type: ignore[arg-type]

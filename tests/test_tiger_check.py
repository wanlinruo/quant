import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "phase0" / "tiger_check.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("tiger_check", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["tiger_check"] = module
    spec.loader.exec_module(module)
    return module


tc = _load()

PAPER = "2" * 17


@pytest.mark.parametrize(
    ("account", "expected"),
    [
        (PAPER, True),
        ("51230321", False),
        ("U12300123", False),
        ("", False),
        (None, False),
        ("2" * 16, False),
        ("2" * 18, False),
    ],
)
def test_is_paper_account(account: str | None, expected: bool) -> None:
    assert tc.is_paper_account(account) is expected


def test_order_permission_allows_only_paper_mode_and_paper_account() -> None:
    assert tc.order_permission("paper", PAPER)[0] is True
    assert tc.order_permission("live", PAPER)[0] is False
    assert tc.order_permission("sim", PAPER)[0] is False
    assert tc.order_permission(None, PAPER)[0] is False
    assert tc.order_permission("paper", "51230321")[0] is False
    assert tc.order_permission("paper", "U12300123")[0] is False


def test_order_permission_message_masks_account() -> None:
    _, reason = tc.order_permission("paper", "51230321")
    assert "51230321" not in reason


def test_mask_account() -> None:
    assert tc.mask_account("20191106192858300") == "***8300"
    assert "5123" not in tc.mask_account("51230321")
    assert tc.mask_account(None) == "***"


def test_far_limit_price_is_well_below_reference() -> None:
    assert tc.far_limit_price(769.66) == 384.83
    assert tc.far_limit_price(0.01) == 0.01
    for bad in (0.0, -1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            tc.far_limit_price(bad)


def test_status_name_handles_sdk_enum_and_str() -> None:
    from tigeropen.common.consts import OrderStatus

    assert tc.status_name(OrderStatus.CANCELLED) == "CANCELLED"
    assert tc.status_name("CANCELLED") == "CANCELLED"
    assert tc.status_name(OrderStatus.CANCELLED) in tc.TERMINAL_STATUSES

import importlib

import pytest

import quant

SUBPACKAGES = [
    "core",
    "broker",
    "data",
    "data.providers",
    "strategies",
    "risk",
    "engine",
    "jobs",
    "db",
    "notify",
    "dashboard",
]


def test_version() -> None:
    assert quant.__version__ == "0.1.0"


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_importable(name: str) -> None:
    importlib.import_module(f"quant.{name}")

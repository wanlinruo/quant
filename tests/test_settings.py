from pathlib import Path

import pytest

from quant.core import settings as settings_mod
from quant.core.settings import ConfigError, load_settings, read_trading_mode

REPO_CONFIGS = Path(__file__).resolve().parents[1] / "configs"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for k in list(os.environ):
        if k.startswith("QUANT_") or k in ("TRADING_MODE", "TIGER_PROPS_PATH"):
            monkeypatch.delenv(k)
    monkeypatch.setattr(settings_mod, "is_dev_machine", lambda: False)


@pytest.mark.parametrize(
    ("mode", "broker"), [("sim", "sim"), ("paper", "tiger"), ("live", "tiger")]
)
def test_modes_load_repo_configs(monkeypatch: pytest.MonkeyPatch, mode: str, broker: str) -> None:
    monkeypatch.setenv("TRADING_MODE", mode)
    s = load_settings(REPO_CONFIGS)
    assert s.trading_mode == mode
    assert s.broker == broker
    assert s.market.timezone == "America/New_York"
    assert s.market.calendar == "XNYS"
    assert s.schedule.daily_execute == "09:50"
    assert "SPY" in s.risk.symbol_whitelist


def test_default_mode_is_paper() -> None:
    assert read_trading_mode({}) == "paper"
    assert read_trading_mode({"TRADING_MODE": "  "}) == "paper"
    assert load_settings(REPO_CONFIGS).trading_mode == "paper"


@pytest.mark.parametrize("bad", ["LIVE", "real", "prod", "Paper", "simulate"])
def test_invalid_mode_rejected(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    monkeypatch.setenv("TRADING_MODE", bad)
    with pytest.raises(ConfigError, match="非法"):
        load_settings(REPO_CONFIGS)


def test_live_refused_on_dev_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_mod, "is_dev_machine", lambda: True)
    monkeypatch.setenv("TRADING_MODE", "live")
    with pytest.raises(ConfigError, match="Mac"):
        load_settings(REPO_CONFIGS)


def test_env_overrides_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADING_MODE", "sim")
    monkeypatch.setenv("QUANT_EXECUTOR__CASH_BUFFER", "0.05")
    monkeypatch.setenv("QUANT_SCHEDULE__DAILY_EXECUTE", "9:35")
    s = load_settings(REPO_CONFIGS)
    assert s.executor.cash_buffer == 0.05
    assert s.executor.limit_offset == 0.002  # 同一节内其他字段保留 YAML 值
    assert s.schedule.daily_execute == "09:35"


def test_env_cannot_switch_broker_against_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADING_MODE", "sim")
    monkeypatch.setenv("QUANT_BROKER", "tiger")
    with pytest.raises(ConfigError, match="broker"):
        load_settings(REPO_CONFIGS)


def _write(d: Path, name: str, text: str) -> None:
    (d / name).write_text(text, encoding="utf-8")


def test_mode_in_yaml_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "base.yaml", "trading_mode: live\n")
    _write(tmp_path, "paper.yaml", "broker: tiger\n")
    with pytest.raises(ConfigError, match="环境变量"):
        load_settings(tmp_path)


def test_unknown_key_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "base.yaml", "executor:\n  cash_bufer: 0.1\n")
    _write(tmp_path, "paper.yaml", "broker: tiger\n")
    with pytest.raises(ValueError, match="cash_bufer"):
        load_settings(tmp_path)


def test_invalid_schedule_time(tmp_path: Path) -> None:
    _write(tmp_path, "base.yaml", "schedule:\n  daily_execute: '25:00'\n")
    _write(tmp_path, "paper.yaml", "broker: tiger\n")
    with pytest.raises(ValueError, match="HH:MM"):
        load_settings(tmp_path)


def test_secrets_not_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "postgresql://u:s3cr3t-pass@db/quant"
    hook = "https://hooks.example/abc-token-123"
    monkeypatch.setenv("QUANT_DATABASE_URL", secret)
    monkeypatch.setenv("QUANT_NOTIFY_WEBHOOK_URL", hook)
    s = load_settings(REPO_CONFIGS)
    assert s.database_url is not None
    assert s.database_url.get_secret_value() == secret
    for text in (repr(s), str(s), s.model_dump_json()):
        assert "s3cr3t" not in text
        assert "abc-token" not in text


def test_tiger_props_file_permissions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    props = tmp_path / "tiger_openapi_config.properties"
    props.write_text("x=1\n")
    monkeypatch.setenv("TIGER_PROPS_PATH", str(tmp_path))
    s = load_settings(REPO_CONFIGS)

    props.chmod(0o644)
    with pytest.raises(ConfigError, match="600"):
        s.tiger_props_file()
    props.chmod(0o600)
    assert s.tiger_props_file() == props


def test_tiger_props_missing() -> None:
    s = load_settings(REPO_CONFIGS)
    with pytest.raises(ConfigError, match="TIGER_PROPS_PATH"):
        s.tiger_props_file()

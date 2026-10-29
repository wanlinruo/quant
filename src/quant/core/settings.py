"""配置加载：configs/base.yaml + 按交易模式叠加的 YAML + 环境变量覆盖。

优先级从低到高：base.yaml → {dev,paper,live}.yaml → 环境变量。
- 交易模式只从环境变量 TRADING_MODE 读取（sim / paper / live，默认 paper），配置文件中不得出现。
- 其他字段可用 QUANT_ 前缀的环境变量覆盖，嵌套用 __，如 QUANT_EXECUTOR__CASH_BUFFER=0.03。
- 密钥只来自环境变量（SecretStr，repr 与日志中显示为 **********）或权限 600 的文件。
"""

from __future__ import annotations

import os
import stat
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, get_args

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

TradingMode = Literal["sim", "paper", "live"]
TRADING_MODES: tuple[str, ...] = get_args(TradingMode)
DEFAULT_TRADING_MODE: TradingMode = "paper"

# 交易模式 → 叠加的配置文件
MODE_CONFIG_FILES: dict[str, str] = {"sim": "dev.yaml", "paper": "paper.yaml", "live": "live.yaml"}
TIGER_PROPS_FILENAME = "tiger_openapi_config.properties"

# src/quant/core/settings.py → 仓库根目录/configs
DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[3] / "configs"


class ConfigError(RuntimeError):
    """配置非法，程序应拒绝启动。"""


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MarketSettings(_Section):
    timezone: str = "America/New_York"
    calendar: str = "XNYS"


class ScheduleSettings(_Section):
    """各任务的美东时间，HH:MM。"""

    daily_data: str = "16:30"
    daily_signal: str = "17:00"
    daily_execute: str = "09:50"

    @field_validator("daily_data", "daily_signal", "daily_execute")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        hh, sep, mm = v.partition(":")
        if not (sep and hh.isdigit() and mm.isdigit() and int(hh) < 24 and int(mm) < 60):
            raise ValueError(f"时间格式应为 HH:MM：{v!r}")
        return f"{int(hh):02d}:{int(mm):02d}"


class ExecutorSettings(_Section):
    cash_buffer: float = Field(0.02, ge=0, lt=1)
    limit_offset: float = Field(0.002, ge=0, lt=0.1)
    min_trade_usd: float = Field(100, ge=0)
    min_weight_diff: float = Field(0.02, ge=0, le=1)
    # 限价单超时未成交则撤单、按最新报价重挂；最多重试 max_retries 次（#30 确认：5 分钟、3 次）
    order_timeout_minutes: float = Field(5, gt=0, le=60)
    max_retries: int = Field(3, ge=0, le=10)
    poll_seconds: float = Field(10, gt=0, le=300)
    # 收盘前多少分钟仍未完成则撤单并告警（PROJECT_PLAN 第 8 章）
    stop_before_close_minutes: int = Field(30, ge=0, le=120)


class RiskSettings(_Section):
    symbol_whitelist: tuple[str, ...] = ()
    max_symbol_weight: float = Field(1.0, gt=0, le=1)
    max_total_weight: float = Field(1.0, gt=0, le=1)
    # 单笔订单金额上限（美元）；paper/live 必须配置，未配置时拒绝执行（#30）
    max_order_usd: Decimal | None = Field(None, gt=0)
    # 单日换手上限：当日成交额 / 净值（#30 确认：2.2）
    max_daily_turnover: float = Field(2.2, gt=0, le=10)


class CostSettings(_Section):
    """回测成本模型（PROJECT_PLAN 第 9 章）。

    老虎美股佣金【待确认】（issue #5），确认前不设默认值；未配置时 SimBroker 拒绝构造。
    """

    commission_per_share: Decimal | None = Field(None, ge=0)
    min_commission: Decimal | None = Field(None, ge=0)
    slippage: Decimal = Field(Decimal("0.0005"), ge=0, lt=Decimal("0.1"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="QUANT_",
        env_nested_delimiter="__",
        extra="forbid",
        frozen=True,
    )

    trading_mode: TradingMode = Field(DEFAULT_TRADING_MODE, validation_alias="TRADING_MODE")
    broker: Literal["sim", "tiger"]
    tiger_props_path: Path | None = Field(None, validation_alias="TIGER_PROPS_PATH")
    database_url: SecretStr | None = None
    # Parquet 行情数据根目录（PROJECT_PLAN 7.2）；容器内为 /data/parquet
    data_dir: Path = Path("data/parquet")
    notify_webhook_url: SecretStr | None = None

    market: MarketSettings = MarketSettings()
    schedule: ScheduleSettings = ScheduleSettings()
    executor: ExecutorSettings = ExecutorSettings()
    risk: RiskSettings = RiskSettings()
    costs: CostSettings = CostSettings()

    @field_validator("tiger_props_path")
    @classmethod
    def _expand(cls, v: Path | None) -> Path | None:
        return v.expanduser() if v is not None else None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # 环境变量优先于 YAML（YAML 以 init 参数传入）；.env 由 uv/docker 注入，这里不读
        return env_settings, init_settings

    def tiger_props_file(self) -> Path:
        """老虎配置文件路径；不存在或权限不是仅属主可读写时报错。"""
        if self.tiger_props_path is None:
            raise ConfigError("未设置 TIGER_PROPS_PATH")
        path = self.tiger_props_path
        if path.is_dir():
            path = path / TIGER_PROPS_FILENAME
        if not path.is_file():
            raise ConfigError(f"找不到老虎配置文件：{path}")
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            raise ConfigError(f"老虎配置文件权限为 {oct(mode)}，必须为 600：chmod 600 {path}")
        return path


def read_trading_mode(environ: dict[str, str] | None = None) -> TradingMode:
    """从环境变量读取交易模式；未设置取默认 paper，非法值报错。"""
    env = os.environ if environ is None else environ
    raw = env.get("TRADING_MODE", "").strip()
    if not raw:
        return DEFAULT_TRADING_MODE
    if raw not in TRADING_MODES:
        raise ConfigError(f"TRADING_MODE={raw!r} 非法，只能是 {' / '.join(TRADING_MODES)}")
    mode: TradingMode = raw  # type: ignore[assignment]
    return mode


def is_dev_machine() -> bool:
    """Mac 只做开发，不允许 live。"""
    return sys.platform == "darwin"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"找不到配置文件：{path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"配置文件顶层必须是映射：{path}")
    if {"trading_mode", "TRADING_MODE"} & data.keys():
        raise ConfigError(f"交易模式只能通过环境变量 TRADING_MODE 设置，不能写在 {path.name}")
    return data


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_settings(config_dir: Path | None = None) -> Settings:
    mode = read_trading_mode()
    if mode == "live" and is_dev_machine():
        raise ConfigError("Mac 只用于开发，禁止 TRADING_MODE=live")
    if config_dir is None:
        config_dir = Path(os.environ.get("QUANT_CONFIG_DIR", DEFAULT_CONFIG_DIR))
    data = _deep_merge(
        _load_yaml(config_dir / "base.yaml"), _load_yaml(config_dir / MODE_CONFIG_FILES[mode])
    )
    settings = Settings(**data)
    if settings.trading_mode != mode:  # 防御：两处读取结果必须一致
        raise ConfigError("交易模式读取不一致")
    if mode == "sim" and settings.broker != "sim":
        raise ConfigError("TRADING_MODE=sim 时 broker 必须为 sim")
    if mode in ("paper", "live") and settings.broker != "tiger":
        raise ConfigError(f"TRADING_MODE={mode} 时 broker 必须为 tiger")
    return settings

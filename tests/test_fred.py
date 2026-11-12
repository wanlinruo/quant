"""FRED 客户端（#69 第 2 批 Task 1）。不访问网络。"""

from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any

import pytest
from pydantic import SecretStr

from fakes import FakeFetch
from quant.data.providers.base import DataUnavailableError
from quant.data.providers.fred import (
    FredAuthError,
    FredClient,
    FredHttpError,
    FredParseError,
    FredUnreachable,
    Obs,
)


def client(fetch: object, key: str = "k") -> FredClient:
    return FredClient(SecretStr(key), fetch, sleep=lambda s: None)  # type: ignore[arg-type]


def test_observations_parses_and_skips_missing() -> None:
    body = {"observations": [{"date": "2026-10-28", "value": "17.1"},
                             {"date": "2026-10-29", "value": "."},
                             {"date": "2026-10-30", "value": "17.2"}]}  # fmt: skip
    fetch = FakeFetch((200, body))
    got = client(fetch).observations("VIXCLS", date(2026, 8, 1))
    assert got == [Obs(date(2026, 10, 28), 17.1), Obs(date(2026, 10, 30), 17.2)]
    assert "series_id=VIXCLS" in fetch.calls[0][0]
    assert "observation_start=2026-08-01" in fetch.calls[0][0]


def test_last_updated_parses_offset() -> None:
    body = {"seriess": [{"last_updated": "2026-10-02 08:24:33-05"}]}
    got = client(FakeFetch((200, body))).last_updated("PAYEMS")
    assert got == datetime(2026, 10, 2, 13, 24, 33, tzinfo=UTC)


def test_bad_key_is_auth_error_without_key_in_message() -> None:
    msg = "Bad Request.  The value for variable api_key is not registered."
    body = {"error_code": 400, "error_message": msg}
    with pytest.raises(FredAuthError) as e:
        client(FakeFetch((400, body)), "SECRET").observations("X", date(2026, 1, 1))
    assert "SECRET" not in str(e.value) and "api_key=" not in str(e.value)


def test_other_error_is_unavailable() -> None:
    with pytest.raises(DataUnavailableError) as e:
        client(FakeFetch((500, "oops")), "SECRET").observations("X", date(2026, 1, 1))
    assert not isinstance(e.value, FredAuthError) and "SECRET" not in str(e.value)


def test_network_error_message_has_no_url() -> None:
    def boom(url: str, headers: object) -> None:
        raise OSError(f"failed {url}")

    with pytest.raises(DataUnavailableError) as e:
        client(boom, "SECRET").observations("X", date(2026, 1, 1))
    assert "SECRET" not in str(e.value)
    assert e.value.__cause__ is None and e.value.__suppress_context__


@pytest.mark.parametrize("status", [500, 503, 429])
def test_server_errors_are_unreachable(status: int) -> None:
    with pytest.raises(FredUnreachable):
        client(FakeFetch((status, "oops")), "SECRET").observations("X", date(2026, 1, 1))


def test_client_error_is_not_unreachable() -> None:
    with pytest.raises(FredHttpError) as e:
        client(FakeFetch((404, {"error_message": "Bad series"})), "SECRET").observations(
            "X", date(2026, 1, 1)
        )
    assert not isinstance(e.value, FredUnreachable)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"observations": [{"date": "bad", "value": "1"}]},
        {"observations": [{"date": "2026-10-30", "value": "bad"}]},
    ],
)
def test_observation_parse_errors_are_distinct(body: object) -> None:
    with pytest.raises(FredParseError):
        client(FakeFetch((200, body))).observations("X", date(2026, 1, 1))


def test_non_json_success_body_is_parse_error() -> None:
    with pytest.raises(FredParseError):
        client(FakeFetch((200, "<html>"))).observations("X", date(2026, 1, 1))


def test_network_error_is_unreachable() -> None:
    def boom(url: str, headers: Mapping[str, str]) -> tuple[int, Any]:
        raise OSError("timeout")

    with pytest.raises(FredUnreachable):
        FredClient(SecretStr("K"), fetch=boom, sleep=lambda s: None).observations(
            "X", date(2026, 1, 1)
        )

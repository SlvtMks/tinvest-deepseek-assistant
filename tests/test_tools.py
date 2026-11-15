"""Схемы инструментов и диспетчер вызовов."""

from __future__ import annotations

import json
from typing import Any

from tinvest_ai.tools import (
    TOOLS,
    TOOLS_BY_NAME,
    ToolExecutor,
    normalize_arguments,
    summarize_result,
)


class StubClient:
    """Двойник клиента: пишет вызовы и умеет падать по требованию."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_accounts(self) -> dict[str, Any]:
        self.calls.append(("list_accounts", {}))
        return {"accounts": [{"account_id": "acc-1"}], "count": 1}

    def get_portfolio(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get_portfolio", kwargs))
        return {"positions_count": 2, "positions": [], "total_value": 100.0}

    def get_operations(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get_operations", kwargs))
        return {"count": 5, "operations": []}

    def get_fundamentals(self, instruments: list[str]) -> dict[str, Any]:
        self.calls.append(("get_fundamentals", {"instruments": instruments}))
        return {"fundamentals": [{"ticker": instruments[0]}], "count": 1}

    def get_cash_flows(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get_cash_flows", kwargs))
        return {"totals": {"RUB": {"deposits": 1000.0}}}

    def boom(self, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("API недоступен")


def test_every_tool_has_valid_schema() -> None:
    names = [tool["function"]["name"] for tool in TOOLS]
    assert len(names) == len(set(names))
    for tool in TOOLS:
        assert tool["type"] == "function"
        function = tool["function"]
        assert function["name"] and function["description"]
        params = function["parameters"]
        assert params["type"] == "object"
        for required in params["required"]:
            assert required in params["properties"], f"{function['name']}: {required}"


def test_expected_tools_present() -> None:
    expected = {
        "list_accounts",
        "get_portfolio",
        "get_operations",
        "get_cash_flows",
        "get_performance",
        "search_instrument",
        "get_instrument_info",
        "get_fundamentals",
        "get_candles",
        "get_last_prices",
        "get_watchlist",
    }
    assert expected == set(TOOLS_BY_NAME)


def test_executor_dispatches_and_normalizes(fake_services) -> None:
    client = StubClient()
    executor = ToolExecutor(client=client)
    result = executor.execute("get_operations", {"days": "30", "limit": "5", "unknown": "x"})
    assert result.ok is True
    assert client.calls == [("get_operations", {"days": 30, "limit": 5})]
    assert result.result["count"] == 5


def test_executor_drops_empty_account_id() -> None:
    client = StubClient()
    executor = ToolExecutor(client=client)
    executor.execute("get_portfolio", {"account_id": "  "})
    assert client.calls == [("get_portfolio", {})]


def test_executor_unknown_tool() -> None:
    executor = ToolExecutor(client=StubClient())
    result = executor.execute("drop_database", {})
    assert result.ok is False
    assert result.result["error"] == "unknown_tool"


def test_executor_invalid_json() -> None:
    executor = ToolExecutor(client=StubClient())
    result = executor.execute_json("get_portfolio", "{not json")
    assert result.ok is False
    assert result.result["error"] == "invalid_json"


def test_executor_empty_arguments_are_allowed() -> None:
    executor = ToolExecutor(client=StubClient())
    result = executor.execute_json("list_accounts", "")
    assert result.ok is True
    assert result.result["count"] == 1


def test_executor_invalid_argument_type() -> None:
    executor = ToolExecutor(client=StubClient())
    result = executor.execute("get_operations", {"days": "не число"})
    assert result.ok is False
    assert result.result["error"] == "invalid_arguments"


def test_executor_wraps_client_exceptions() -> None:
    class Failing(StubClient):
        def get_fundamentals(self, instruments: list[str]) -> dict[str, Any]:
            raise RuntimeError("API недоступен")

    executor = ToolExecutor(client=Failing())
    result = executor.execute("get_fundamentals", {"instruments": ["SBER"]})
    assert result.ok is False
    assert result.result["error"] == "tool_failed"
    assert "API недоступен" in result.result["message"]
    assert executor.history and executor.history[-1].name == "get_fundamentals"


def test_payload_is_json_serializable() -> None:
    executor = ToolExecutor(client=StubClient())
    result = executor.execute("list_accounts", {})
    payload = json.loads(result.to_payload())
    assert payload["count"] == 1


def test_normalize_arguments_handles_lists_and_bools() -> None:
    normalized = normalize_arguments("get_fundamentals", {"instruments": "SBER, GAZP", "extra": 1})
    assert normalized == {"instruments": ["SBER", "GAZP"]}

    normalized = normalize_arguments("get_portfolio", {"include_zero_positions": "true"})
    assert normalized == {"include_zero_positions": True}


def test_normalize_int_from_float_string() -> None:
    assert normalize_arguments("get_performance", {"days": "90.0"}) == {"days": 90}


def test_summarize_result() -> None:
    executor = ToolExecutor(client=StubClient())
    result = executor.execute("get_portfolio", {})
    summary = summarize_result(result)
    assert "позиций: 2" in summary
    failure = executor.execute("nope", {})
    assert "ошибка" in summarize_result(failure)


def test_tool_descriptions_mention_core_data() -> None:
    fundamentals_description = TOOLS_BY_NAME["get_fundamentals"]["function"]["description"]
    assert "P/E" in fundamentals_description
    assert "ROE" in fundamentals_description
    assert "GetAssetFundamentals" in fundamentals_description

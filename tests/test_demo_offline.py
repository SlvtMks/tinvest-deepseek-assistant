"""Демо-клиент и офлайн-ассистент: приёмочные сценарии без ключей."""

from __future__ import annotations

import pytest

from tinvest_ai.demo import DEMO_INSTRUMENTS, build_demo_client
from tinvest_ai.offline_agent import OfflineAgent
from tinvest_ai.tools import ToolExecutor


@pytest.fixture()
def demo_client():
    return build_demo_client()


@pytest.fixture()
def agent(demo_settings):
    client = build_demo_client()
    executor = ToolExecutor(client=client)
    return OfflineAgent(settings=demo_settings, executor=executor)


# ------------------------------------------------------------- демо-клиент
def test_demo_portfolio_is_consistent(demo_client) -> None:
    portfolio = demo_client.get_portfolio()
    assert portfolio["positions_count"] == len(portfolio["positions"])
    assert portfolio["total_value"] == pytest.approx(
        portfolio["positions_value"] + portfolio["cash_value"], abs=0.01
    )
    shares = sum(position["share_in_portfolio_pct"] for position in portfolio["positions"])
    assert 0 < shares < 100
    assert portfolio["positions"] == sorted(portfolio["positions"], key=lambda p: p["value"], reverse=True)


def test_demo_performance_has_positive_and_negative(demo_client) -> None:
    data = demo_client.get_performance(days=30)
    returns = [item["return_pct"] for item in data["items"]]
    assert returns == sorted(returns, reverse=True)
    assert data["best"]["ticker"] == "YDEX"
    assert data["worst"]["return_pct"] < 0


def test_demo_cash_flows(demo_client) -> None:
    data = demo_client.get_cash_flows(days=30)
    totals = data["totals"]["RUB"]
    assert totals["deposits"] == 30_000.0  # пополнение 32 дня назад вне периода
    assert totals["withdrawals"] == 20_000.0
    assert totals["net_deposits"] == 10_000.0


def test_demo_operations_period_filter(demo_client) -> None:
    month = demo_client.get_operations(days=30)
    quarter = demo_client.get_operations(days=90)
    assert quarter["count"] > month["count"]


def test_demo_fundamentals(demo_client) -> None:
    data = demo_client.get_fundamentals(["SBER", "OZON"])
    tickers = {item["ticker"]: item for item in data["fundamentals"]}
    assert tickers["SBER"]["pe_ratio_ttm"] == 4.1
    assert tickers["SBER"]["roe_pct"] == 24.1
    assert tickers["OZON"]["pe_ratio_ttm"] < 0


def test_demo_search_and_info(demo_client) -> None:
    assert demo_client.search_instrument("газпром")["results"][0]["ticker"] == "GAZP"
    info = demo_client.get_instrument_info("TMOS")
    assert info["instrument_type"] == "etf"
    assert info["last_price"] == 8.42


def test_demo_unknown_ticker(demo_client) -> None:
    with pytest.raises(ValueError):
        demo_client.get_instrument_info("AAPL")


def test_demo_watchlist(demo_client) -> None:
    data = demo_client.get_watchlist()
    assert data["tickers"] == ["SBER", "GAZP", "LKOH", "YDEX", "OZON"]
    assert len(data["prices"]) == 5


def test_demo_all_tickers_have_fundamentals() -> None:
    client = build_demo_client()
    tickers = [ticker for ticker, meta in DEMO_INSTRUMENTS.items() if meta["asset_uid"]]
    data = client.get_fundamentals(tickers)
    assert data["count"] == len(tickers)


# --------------------------------------------------------- офлайн-ассистент
@pytest.mark.parametrize(
    "question,expected_tools",
    [
        ("Покажи портфель", ["get_portfolio"]),
        ("Какая бумага дала лучшую доходность за месяц?", ["get_performance"]),
        ("Что там по SBER?", ["get_instrument_info", "get_fundamentals", "get_candles"]),
        ("Сколько я пополнил за месяц?", ["get_cash_flows"]),
        (
            "Какие бумаги из моего вотчлиста недооценены по P/E и ROE?",
            ["get_watchlist", "get_fundamentals"],
        ),
    ],
)
def test_acceptance_scenarios(agent: OfflineAgent, question: str, expected_tools: list[str]) -> None:
    answer = agent.ask(question)
    assert answer.tools_used == expected_tools
    assert len(answer.text) > 100
    assert "₽" in answer.text or "%" in answer.text


def test_portfolio_answer_contains_tickers(agent: OfflineAgent) -> None:
    answer = agent.ask("покажи портфель")
    for ticker in ("SBER", "GAZP", "LKOH"):
        assert ticker in answer.text
    assert "Итого по портфелю" in answer.text


def test_watchlist_answer_marks_candidates(agent: OfflineAgent) -> None:
    answer = agent.ask("что недооценено из вотчлиста по P/E и ROE")
    assert "Потенциально недооценённые" in answer.text
    assert "OZON" in answer.text  # без осмысленного P/E


def test_cash_flow_answer(agent: OfflineAgent) -> None:
    answer = agent.ask("сколько я пополнил за месяц")
    assert "Пополнения" in answer.text
    assert "Чистое пополнение" in answer.text


def test_unknown_question_returns_help(agent: OfflineAgent) -> None:
    answer = agent.ask("расскажи анекдот")
    assert "демо-ассистент" in answer.text.lower() or "Попробуйте" in answer.text


def test_streaming_callback(agent: OfflineAgent) -> None:
    tokens: list[str] = []
    answer = agent.ask("покажи портфель", on_token=tokens.append)
    assert "".join(tokens) == answer.text


def test_accounts_scenario(agent: OfflineAgent) -> None:
    answer = agent.ask("какие у меня счета")
    assert answer.tools_used == ["list_accounts"]
    assert "sbx-1001" in answer.text


def test_agent_history(agent: OfflineAgent) -> None:
    agent.ask("покажи портфель")
    assert len(agent.history) == 2
    agent.reset()
    assert agent.history == []

"""Логика обёртки T-Invest API на моках сервисов SDK (без сети)."""

from __future__ import annotations

import pytest
from conftest import now

from tinvest_ai import tinvest_client as tc_module
from tinvest_ai.tinvest_client import TInvestClient, TInvestError


@pytest.fixture()
def client(fake_services, monkeypatch) -> TInvestClient:
    instance = TInvestClient(
        token="t.test-token",
        sandbox=True,
        retries=1,
        cache_ttl=0,
        watchlist=("SBER", "GAZP"),
        clock=now,
    )
    monkeypatch.setattr(instance, "_services", lambda: fake_services)
    return instance


# ------------------------------------------------------------------ accounts
def test_list_accounts_maps_types_and_status(client: TInvestClient) -> None:
    data = client.list_accounts()
    assert data["count"] == 2
    assert data["accounts"][0] == {
        "account_id": "acc-1",
        "name": "Брокерский",
        "type": "Брокерский",
        "status": "открыт",
        "opened_date": "2026-11-15T12:00:00+00:00",
    }
    assert data["contour"] == "sandbox"


def test_resolve_account_prefers_explicit(client: TInvestClient, fake_services) -> None:
    portfolio = client.get_portfolio(account_id="acc-2")
    assert portfolio["account_id"] == "acc-2"
    assert ("operations.get_portfolio", {"account_id": "acc-2"}) in fake_services.calls


def test_account_hint_when_multiple(client: TInvestClient) -> None:
    client.list_accounts()
    portfolio = client.get_portfolio()
    assert "note" in portfolio
    assert "первый из 2" in portfolio["note"]


def test_resolve_account_without_accounts_raises(client: TInvestClient, fake_services) -> None:
    fake_services.accounts_response = type("R", (), {"accounts": []})()
    client.list_accounts()
    with pytest.raises(TInvestError):
        client.get_portfolio()


# ----------------------------------------------------------------- portfolio
def test_get_portfolio_calculations(client: TInvestClient) -> None:
    portfolio = client.get_portfolio()
    assert portfolio["total_value"] == 500_000.0
    assert portfolio["cash_value"] == 100_000.0
    assert portfolio["positions_count"] == 2  # валюта и нулевая позиция отфильтрованы

    tickers = [position["ticker"] for position in portfolio["positions"]]
    assert tickers == ["SBER", "GAZP"]  # отсортировано по доле

    sber = portfolio["positions"][0]
    assert sber["value"] == 30_000.0
    assert sber["invested"] == 25_000.0
    assert sber["expected_yield"] == 5_000.0
    assert sber["expected_yield_pct"] == 20.0
    assert sber["share_in_portfolio_pct"] == 6.0
    assert sber["currency"] == "RUB"

    gazp = portfolio["positions"][1]
    assert gazp["value"] == 24_000.0
    assert gazp["expected_yield"] == -6_000.0
    assert gazp["expected_yield_pct"] == -20.0

    assert portfolio["expected_yield"] == 45_000.0
    assert portfolio["expected_yield_pct"] == pytest.approx(81.8182, abs=1e-3)


def test_get_portfolio_includes_zero_positions(client: TInvestClient) -> None:
    portfolio = client.get_portfolio(include_zero_positions=True)
    assert "ZERO" in [position["ticker"] for position in portfolio["positions"]]


# ---------------------------------------------------------------- operations
def test_get_operations_sorted_and_filtered(client: TInvestClient) -> None:
    data = client.get_operations(days=30)
    assert data["count"] == 5
    dates = [operation["date"] for operation in data["operations"]]
    assert dates == sorted(dates, reverse=True)
    assert data["operations"][0]["type"] == "OPERATION_TYPE_BUY"
    assert data["operations"][0]["type_label"] == "Покупка"

    only_input = client.get_operations(days=30, operation_types=["OPERATION_TYPE_INPUT"])
    assert only_input["count"] == 1
    assert only_input["operations"][0]["payment"] == 50_000.0


def test_get_operations_truncates(client: TInvestClient) -> None:
    data = client.get_operations(days=30, limit=2)
    assert data["returned"] == 2
    assert data["truncated"] is True
    assert len(data["operations"]) == 2


def test_get_cash_flows_aggregation(client: TInvestClient) -> None:
    data = client.get_cash_flows(days=30)
    totals = data["totals"]["RUB"]
    assert totals["deposits"] == 50_000.0
    assert totals["withdrawals"] == 20_000.0
    assert totals["net_deposits"] == 30_000.0
    assert totals["dividends"] == 1_500.0
    assert totals["fees"] == 45.0
    assert totals["trades_net"] == -30_000.0
    assert data["operations_count"] == 5
    assert data["primary_currency"] == "RUB"


# --------------------------------------------------------------- performance
def test_get_performance_ranks_positions(client: TInvestClient) -> None:
    data = client.get_performance(days=30)
    assert [item["ticker"] for item in data["items"]] == ["SBER", "GAZP"]
    best, worst = data["items"][0], data["items"][1]
    assert best["ticker"] == "SBER"
    assert best["price_start"] == 101.0
    assert best["price_end"] == 131.0
    assert best["return_pct"] == pytest.approx(29.7, abs=0.01)
    assert best["pnl"] == pytest.approx(3000.0, abs=0.01)
    assert worst["return_pct"] < 0
    assert data["portfolio_weighted_return_pct"] is not None
    assert "дивиденды" in data["method"].lower()


def test_get_performance_skips_instruments_without_candles(client: TInvestClient, fake_services) -> None:
    fake_services.candles_by_figi["FIGI_GAZP"] = []
    data = client.get_performance(days=30)
    assert [item["ticker"] for item in data["items"]] == ["SBER"]
    assert data["skipped_instruments"] == ["GAZP"]


# ------------------------------------------------------------------- candles
def test_get_candles_summary(client: TInvestClient) -> None:
    data = client.get_candles("SBER", days=30, interval="day")
    assert data["ticker"] == "SBER"
    assert data["summary"]["candles_total"] == 30
    assert data["summary"]["change_pct"] == pytest.approx(29.7, abs=0.01)
    assert len(data["candles"]) == 30


def test_get_candles_clamps_interval_limit(client: TInvestClient) -> None:
    data = client.get_candles("SBER", days=30, interval="1min")
    assert data["period"]["days"] == 1
    assert data["notes"]


def test_get_candles_uses_cache(fake_services, monkeypatch) -> None:
    instance = TInvestClient(token="t", retries=1, cache_ttl=60, clock=now)
    monkeypatch.setattr(instance, "_services", lambda: fake_services)
    instance.get_candles("SBER", days=30)
    instance.get_candles("SBER", days=30)
    assert fake_services.count("market_data.get_candles") == 1


def test_get_candles_unknown_ticker(client: TInvestClient) -> None:
    with pytest.raises(TInvestError):
        client.get_candles("NOPE", days=30)


# --------------------------------------------------------------- instruments
def test_resolve_instrument_is_cached(client: TInvestClient, fake_services) -> None:
    first = client.get_instrument_info("SBER")
    second = client.get_instrument_info("sber")
    assert first["ticker"] == "SBER"
    assert first["asset_uid"] == "asset-sber"
    assert second == first
    assert fake_services.count("instruments.find_instrument") == 1
    assert fake_services.count("instruments.get_instrument_by") == 1


def test_search_instrument(client: TInvestClient) -> None:
    data = client.search_instrument("SBER")
    assert data["count"] == 1
    assert data["results"][0]["ticker"] == "SBER"
    assert data["results"][0]["tradable"] is True


def test_search_instrument_falls_back_to_ticker_lookup(client: TInvestClient) -> None:
    data = client.search_instrument("SBER")
    assert data["results"][0]["figi"] == "FIGI_SBER"


def test_get_last_prices(client: TInvestClient) -> None:
    data = client.get_last_prices(["SBER", "GAZP"])
    assert data["count"] == 2
    prices = {item["ticker"]: item["price"] for item in data["prices"]}
    assert prices["SBER"] == pytest.approx(318.75)
    assert prices["GAZP"] == pytest.approx(121.35)


def test_get_last_prices_unknown_instrument(client: TInvestClient) -> None:
    data = client.get_last_prices(["НЕТТАКОЙ"])
    assert data["count"] == 0
    assert data["errors"]


# -------------------------------------------------------------- fundamentals
def test_get_fundamentals_maps_fields(client: TInvestClient, fake_services) -> None:
    data = client.get_fundamentals(["SBER"])
    assert data["count"] == 1
    item = data["fundamentals"][0]
    assert item["ticker"] == "SBER"
    assert item["pe_ratio_ttm"] == 4.1
    assert item["price_to_book_ttm"] == 1.0
    assert item["roe"] == 0.241
    assert item["roe_pct"] == 24.1
    assert item["dividend_yield_daily_ttm_pct"] == 10.5
    assert item["market_capitalization"] == 5_760_000_000_000.0
    assert item["total_debt_to_equity_mrq"] is None  # 0 в API = нет данных
    assert item["currency"] == "RUB"
    # батч-запрос: один вызов на все бумаги
    fundamentals_calls = [c for c in fake_services.calls if c[0] == "instruments.get_asset_fundamentals"]
    assert len(fundamentals_calls) == 1


def test_get_fundamentals_legacy_sdk_signature(client: TInvestClient, fake_services, monkeypatch) -> None:
    monkeypatch.setattr(tc_module.sdk, "GetAssetFundamentalsRequest", None)
    data = client.get_fundamentals(["SBER"])
    assert data["count"] == 1
    assert any(call[0] == "instruments.get_asset_fundamentals" for call in fake_services.calls)


def test_get_fundamentals_reports_instruments_without_asset_uid(client: TInvestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        client,
        "_resolve_instrument",
        lambda query: {
            "ticker": "TMOS",
            "name": "Фонд",
            "figi": "FIGI_TMOS",
            "uid": "uid-tmos",
            "asset_uid": "",
            "currency": "RUB",
            "instrument_type": "etf",
        },
    )
    data = client.get_fundamentals(["TMOS"])
    assert data["count"] == 0
    assert data["errors"] and "asset_uid" in data["errors"][0]["error"]


def test_get_fundamentals_empty_request(client: TInvestClient) -> None:
    assert client.get_fundamentals([])["error"] == "empty_request"


def test_get_fundamentals_unknown_ticker(client: TInvestClient) -> None:
    data = client.get_fundamentals(["НЕТТАКОЙ"])
    assert data["count"] == 0
    assert data["errors"]


# ----------------------------------------------------------------- watchlist
def test_get_watchlist(client: TInvestClient) -> None:
    data = client.get_watchlist()
    assert data["tickers"] == ["SBER", "GAZP"]
    assert data["count"] == 2
    assert {item["ticker"] for item in data["prices"]} == {"SBER", "GAZP"}


def test_get_watchlist_empty(client: TInvestClient) -> None:
    client.watchlist = ()
    data = client.get_watchlist()
    assert data["tickers"] == []
    assert "WATCHLIST" in data["note"]


# ------------------------------------------------------------------- helpers
def test_looks_like_uid() -> None:
    assert tc_module._looks_like_uid("e6123145-9665-43e0-8413-cd61b8aa9b13")
    assert not tc_module._looks_like_uid("SBER")


def test_period_clamps() -> None:
    instance = TInvestClient(token="t", clock=now)
    date_from, date_to = instance._period(10_000)
    assert (date_to - date_from).days == 3650


def test_requires_token() -> None:
    with pytest.raises(TInvestError):
        TInvestClient(token="").get_portfolio()

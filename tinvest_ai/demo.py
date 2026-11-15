"""Демо-режим: синтетические данные без обращения к T-Invest API.

Зачем это нужно:

* можно запустить проект и посмотреть на UX, не имея токена Т-Банка;
* Streamlit-демо работает на Railway без секретов;
* тесты получают реалистичный источник данных.

:class:`DemoTInvestClient` — подкласс настоящего клиента: он подменяет только
источники данных (счета, позиции, свечи, операции, фундаменталку), а вся
логика агрегации (доходность, кэш-флоу, маппинг мультипликаторов) остаётся
рабочей и проверяемой.

ВАЖНО: цифры вымышленные и не являются котировками Мосбиржи.
"""

from __future__ import annotations

import random
import zlib
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from .tinvest_client import TInvestClient

DEMO_NOTE = "ДЕМО-ДАННЫЕ: значения синтетические, не котировки Мосбиржи."

DEMO_INSTRUMENTS: dict[str, dict[str, Any]] = {
    "SBER": {
        "name": "Сбербанк",
        "figi": "BBG004730N88",
        "uid": "e6123145-9665-43e0-8413-cd61b8aa9b13",
        "asset_uid": "5f7a1b1e-9c99-4ec4-9e0e-0b0c7a1a0001",
        "class_code": "TQBR",
        "isin": "RU0009029540",
        "lot": 10,
        "instrument_type": "share",
        "sector": "Финансы",
    },
    "GAZP": {
        "name": "Газпром",
        "figi": "BBG004730RP0",
        "uid": "962e2a95-02a9-4171-abd7-aa198dbe643a",
        "asset_uid": "5f7a1b1e-9c99-4ec4-9e0e-0b0c7a1a0002",
        "class_code": "TQBR",
        "isin": "RU0007661625",
        "lot": 10,
        "instrument_type": "share",
        "sector": "Нефть и газ",
    },
    "LKOH": {
        "name": "Лукойл",
        "figi": "BBG004731032",
        "uid": "1b3b6f4a-6f4d-4f2e-9d1e-5f2b6c9f0a04",
        "asset_uid": "5f7a1b1e-9c99-4ec4-9e0e-0b0c7a1a0003",
        "class_code": "TQBR",
        "isin": "RU0009024277",
        "lot": 1,
        "instrument_type": "share",
        "sector": "Нефть и газ",
    },
    "YDEX": {
        "name": "Яндекс",
        "figi": "TCS60A1017R6",
        "uid": "52fbbbe6-6b7b-4d1f-8b0f-9f0c9f0c0005",
        "asset_uid": "5f7a1b1e-9c99-4ec4-9e0e-0b0c7a1a0004",
        "class_code": "TQBR",
        "isin": "RU000A107662",
        "lot": 1,
        "instrument_type": "share",
        "sector": "Технологии",
    },
    "OZON": {
        "name": "Ozon",
        "figi": "TCS60A1012R4",
        "uid": "52fbbbe6-6b7b-4d1f-8b0f-9f0c9f0c0006",
        "asset_uid": "5f7a1b1e-9c99-4ec4-9e0e-0b0c7a1a0005",
        "class_code": "TQBR",
        "isin": "RU000A107RZ0",
        "lot": 1,
        "instrument_type": "share",
        "sector": "Потребительский сектор",
    },
    "TMOS": {
        "name": "Т-Капитал Индекс Мосбиржи",
        "figi": "TCS30A1014R3",
        "uid": "52fbbbe6-6b7b-4d1f-8b0f-9f0c9f0c0007",
        "asset_uid": "",
        "class_code": "TQTF",
        "isin": "RU000A101QL8",
        "lot": 1,
        "instrument_type": "etf",
        "sector": "Фонды",
    },
    "GMKN": {
        "name": "ГМК Норильский никель",
        "figi": "BBG004731489",
        "uid": "962e2a95-02a9-4171-abd7-aa198dbe6f1a",
        "asset_uid": "5f7a1b1e-9c99-4ec4-9e0e-0b0c7a1a0006",
        "class_code": "TQBR",
        "isin": "RU0007288411",
        "lot": 1,
        "instrument_type": "share",
        "sector": "Металлургия",
    },
    "MTSS": {
        "name": "МТС",
        "figi": "BBG004S681W1",
        "uid": "962e2a95-02a9-4171-abd7-aa198dbe6f2b",
        "asset_uid": "5f7a1b1e-9c99-4ec4-9e0e-0b0c7a1a0007",
        "class_code": "TQBR",
        "isin": "RU0007775219",
        "lot": 10,
        "instrument_type": "share",
        "sector": "Телеком",
    },
}

DEMO_PRICES: dict[str, float] = {
    "SBER": 318.75,
    "GAZP": 121.35,
    "LKOH": 7412.50,
    "YDEX": 4356.00,
    "OZON": 4285.50,
    "TMOS": 8.42,
    "GMKN": 132.40,
    "MTSS": 268.90,
}

DEMO_RETURN_30D: dict[str, float] = {
    "SBER": 3.2,
    "GAZP": -6.4,
    "LKOH": 1.8,
    "YDEX": 12.5,
    "OZON": 9.1,
    "TMOS": 2.1,
    "GMKN": -2.7,
    "MTSS": 4.6,
}

# Позиции демо-портфеля: тикер -> (количество, средняя цена)
DEMO_POSITIONS: dict[str, tuple[float, float]] = {
    "SBER": (400, 265.40),
    "GAZP": (500, 132.10),
    "LKOH": (12, 6800.00),
    "YDEX": (20, 4100.00),
    "OZON": (15, 3950.00),
    "TMOS": (3000, 8.10),
}

DEMO_CASH = 152_300.00
DEMO_DAILY_YIELD = -1_840.55

# Фундаментальные показатели (значения в долях: roe=0.24 -> 24 %)
DEMO_FUNDAMENTALS: dict[str, dict[str, float]] = {
    "SBER": {
        "market_capitalization": 5_760_000_000_000,
        "pe_ratio_ttm": 4.1,
        "price_to_book_ttm": 1.0,
        "price_to_sales_ttm": 1.6,
        "ev_to_ebitda_mrq": 3.4,
        "roe": 0.241,
        "roa": 0.031,
        "roic": 0.18,
        "net_margin_mrq": 0.38,
        "eps_ttm": 77.7,
        "revenue_ttm": 3_100_000_000_000,
        "ebitda_ttm": 1_420_000_000_000,
        "net_income_ttm": 1_410_000_000_000,
        "dividend_yield_daily_ttm": 0.105,
        "dividends_per_share": 33.3,
        "dividend_payout_ratio_fy": 0.5,
        "total_debt_to_equity_mrq": 0.0,
        "current_ratio_mrq": 1.2,
        "beta": 1.05,
        "high_price_last_52_weeks": 340.0,
        "low_price_last_52_weeks": 245.0,
        "one_year_annual_revenue_growth_rate": 0.14,
        "three_year_annual_revenue_growth_rate": 0.09,
        "five_year_annual_revenue_growth_rate": 0.08,
        "number_of_employees": 210_000,
        "free_float": 0.48,
    },
    "GAZP": {
        "market_capitalization": 2_870_000_000_000,
        "pe_ratio_ttm": 3.2,
        "price_to_book_ttm": 0.4,
        "price_to_sales_ttm": 0.5,
        "ev_to_ebitda_mrq": 2.1,
        "roe": 0.121,
        "roa": 0.061,
        "roic": 0.09,
        "net_margin_mrq": 0.19,
        "eps_ttm": 37.9,
        "revenue_ttm": 8_900_000_000_000,
        "ebitda_ttm": 2_400_000_000_000,
        "net_income_ttm": 890_000_000_000,
        "dividend_yield_daily_ttm": 0.0,
        "dividends_per_share": 0.0,
        "dividend_payout_ratio_fy": 0.0,
        "total_debt_to_equity_mrq": 0.18,
        "current_ratio_mrq": 1.05,
        "beta": 1.21,
        "high_price_last_52_weeks": 168.0,
        "low_price_last_52_weeks": 108.0,
        "one_year_annual_revenue_growth_rate": 0.03,
        "three_year_annual_revenue_growth_rate": 0.05,
        "five_year_annual_revenue_growth_rate": 0.06,
        "number_of_employees": 492_000,
        "free_float": 0.47,
    },
    "LKOH": {
        "market_capitalization": 5_130_000_000_000,
        "pe_ratio_ttm": 5.5,
        "price_to_book_ttm": 0.9,
        "price_to_sales_ttm": 0.7,
        "ev_to_ebitda_mrq": 3.0,
        "roe": 0.182,
        "roa": 0.11,
        "roic": 0.15,
        "net_margin_mrq": 0.13,
        "eps_ttm": 1_347.0,
        "revenue_ttm": 7_600_000_000_000,
        "ebitda_ttm": 1_750_000_000_000,
        "net_income_ttm": 990_000_000_000,
        "dividend_yield_daily_ttm": 0.128,
        "dividends_per_share": 965.0,
        "dividend_payout_ratio_fy": 0.72,
        "total_debt_to_equity_mrq": 0.12,
        "current_ratio_mrq": 1.6,
        "beta": 0.95,
        "high_price_last_52_weeks": 8_120.0,
        "low_price_last_52_weeks": 6_310.0,
        "one_year_annual_revenue_growth_rate": 0.07,
        "three_year_annual_revenue_growth_rate": 0.11,
        "five_year_annual_revenue_growth_rate": 0.09,
        "number_of_employees": 107_000,
        "free_float": 0.42,
    },
    "YDEX": {
        "market_capitalization": 1_570_000_000_000,
        "pe_ratio_ttm": 22.4,
        "price_to_book_ttm": 4.6,
        "price_to_sales_ttm": 2.3,
        "ev_to_ebitda_mrq": 11.8,
        "roe": 0.094,
        "roa": 0.035,
        "roic": 0.12,
        "net_margin_mrq": 0.11,
        "eps_ttm": 194.5,
        "revenue_ttm": 1_360_000_000_000,
        "ebitda_ttm": 190_000_000_000,
        "net_income_ttm": 71_000_000_000,
        "dividend_yield_daily_ttm": 0.0,
        "dividends_per_share": 0.0,
        "dividend_payout_ratio_fy": 0.0,
        "total_debt_to_equity_mrq": 0.55,
        "current_ratio_mrq": 1.1,
        "beta": 1.35,
        "high_price_last_52_weeks": 4_690.0,
        "low_price_last_52_weeks": 3_020.0,
        "one_year_annual_revenue_growth_rate": 0.31,
        "three_year_annual_revenue_growth_rate": 0.34,
        "five_year_annual_revenue_growth_rate": 0.29,
        "number_of_employees": 26_000,
        "free_float": 0.86,
    },
    "OZON": {
        "market_capitalization": 914_000_000_000,
        "pe_ratio_ttm": -58.0,
        "price_to_book_ttm": 9.8,
        "price_to_sales_ttm": 1.4,
        "ev_to_ebitda_mrq": 24.0,
        "roe": -0.21,
        "roa": -0.04,
        "roic": -0.08,
        "net_margin_mrq": -0.03,
        "eps_ttm": -74.0,
        "revenue_ttm": 650_000_000_000,
        "ebitda_ttm": 32_000_000_000,
        "net_income_ttm": -19_500_000_000,
        "dividend_yield_daily_ttm": 0.0,
        "dividends_per_share": 0.0,
        "dividend_payout_ratio_fy": 0.0,
        "total_debt_to_equity_mrq": 1.9,
        "current_ratio_mrq": 0.85,
        "beta": 1.52,
        "high_price_last_52_weeks": 5_120.0,
        "low_price_last_52_weeks": 3_180.0,
        "one_year_annual_revenue_growth_rate": 0.37,
        "three_year_annual_revenue_growth_rate": 0.41,
        "five_year_annual_revenue_growth_rate": 0.45,
        "number_of_employees": 15_000,
        "free_float": 0.34,
    },
    "GMKN": {
        "market_capitalization": 1_980_000_000_000,
        "pe_ratio_ttm": 9.8,
        "price_to_book_ttm": 1.7,
        "price_to_sales_ttm": 1.9,
        "ev_to_ebitda_mrq": 5.2,
        "roe": 0.164,
        "roa": 0.07,
        "roic": 0.13,
        "net_margin_mrq": 0.19,
        "eps_ttm": 13.5,
        "revenue_ttm": 1_040_000_000_000,
        "ebitda_ttm": 380_000_000_000,
        "net_income_ttm": 199_000_000_000,
        "dividend_yield_daily_ttm": 0.087,
        "dividends_per_share": 11.5,
        "dividend_payout_ratio_fy": 0.83,
        "total_debt_to_equity_mrq": 0.72,
        "current_ratio_mrq": 1.9,
        "beta": 1.12,
        "high_price_last_52_weeks": 178.0,
        "low_price_last_52_weeks": 115.0,
        "one_year_annual_revenue_growth_rate": 0.02,
        "three_year_annual_revenue_growth_rate": 0.04,
        "five_year_annual_revenue_growth_rate": 0.05,
        "number_of_employees": 82_000,
        "free_float": 0.36,
    },
    "MTSS": {
        "market_capitalization": 537_000_000_000,
        "pe_ratio_ttm": 7.4,
        "price_to_book_ttm": 2.9,
        "ev_to_ebitda_mrq": 4.1,
        "roe": 0.28,
        "roa": 0.09,
        "roic": 0.16,
        "net_margin_mrq": 0.09,
        "price_to_sales_ttm": 0.7,
        "eps_ttm": 36.3,
        "revenue_ttm": 740_000_000_000,
        "ebitda_ttm": 290_000_000_000,
        "net_income_ttm": 66_000_000_000,
        "dividend_yield_daily_ttm": 0.126,
        "dividends_per_share": 35.0,
        "dividend_payout_ratio_fy": 0.96,
        "total_debt_to_equity_mrq": 1.4,
        "current_ratio_mrq": 0.9,
        "beta": 0.98,
        "high_price_last_52_weeks": 305.0,
        "low_price_last_52_weeks": 231.0,
        "one_year_annual_revenue_growth_rate": 0.06,
        "three_year_annual_revenue_growth_rate": 0.05,
        "five_year_annual_revenue_growth_rate": 0.06,
        "number_of_employees": 67_000,
        "free_float": 0.29,
    },
}

DEMO_ACCOUNTS = [
    {
        "account_id": "sbx-1001",
        "name": "Песочница · брокерский",
        "type": "Брокерский",
        "status": "открыт",
        "opened_date": "2025-02-11T09:00:00+00:00",
    },
    {
        "account_id": "sbx-1002",
        "name": "Песочница · ИИС",
        "type": "ИИС",
        "status": "открыт",
        "opened_date": "2025-06-03T09:15:00+00:00",
    },
]


def _seed(text: str) -> int:
    return zlib.crc32(text.encode("utf-8"))


class DemoTInvestClient(TInvestClient):
    """Клиент с синтетическими данными — интерфейс как у настоящего."""

    def __init__(
        self,
        watchlist: Sequence[str] = ("SBER", "GAZP", "LKOH", "YDEX", "OZON"),
        default_account_id: str = "sbx-1001",
        retries: int = 1,
        cache_ttl: int = 0,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            token="demo-token",
            sandbox=True,
            retries=retries,
            cache_ttl=cache_ttl,
            default_account_id=default_account_id,
            watchlist=tuple(watchlist),
            **kwargs,
        )
        self.demo = True

    # ------------------------------------------------------------- источники
    def list_accounts(self) -> dict[str, Any]:
        return {
            "accounts": [dict(item) for item in DEMO_ACCOUNTS],
            "count": len(DEMO_ACCOUNTS),
            "contour": "demo",
            "note": DEMO_NOTE,
        }

    def get_portfolio(
        self, account_id: str | None = None, include_zero_positions: bool = False
    ) -> dict[str, Any]:
        acc_id = account_id or self.default_account_id or DEMO_ACCOUNTS[0]["account_id"]
        positions: list[dict[str, Any]] = []
        for ticker, (quantity, avg_price) in DEMO_POSITIONS.items():
            if quantity == 0 and not include_zero_positions:
                continue
            meta = DEMO_INSTRUMENTS[ticker]
            price = DEMO_PRICES[ticker]
            value = round(quantity * price, 2)
            invested = round(quantity * avg_price, 2)
            expected_yield = round(value - invested, 2)
            positions.append(
                {
                    "ticker": ticker,
                    "figi": meta["figi"],
                    "instrument_uid": meta["uid"],
                    "instrument_type": "фонд" if meta["instrument_type"] == "etf" else "акция",
                    "quantity": float(quantity),
                    "average_price": avg_price,
                    "current_price": price,
                    "currency": "RUB",
                    "value": value,
                    "invested": invested,
                    "expected_yield": expected_yield,
                    "expected_yield_pct": round(expected_yield / invested * 100, 4) if invested else None,
                    "daily_yield": round(-expected_yield * 0.01, 2),
                    "share_in_portfolio_pct": None,
                    "blocked": False,
                }
            )
        positions_value = sum(p["value"] for p in positions)
        total_value = round(positions_value + DEMO_CASH, 2)
        for position in positions:
            position["share_in_portfolio_pct"] = round(position["value"] / total_value * 100, 2)
        positions.sort(key=lambda p: p["value"], reverse=True)
        total_expected_yield = round(sum(p["expected_yield"] for p in positions), 2)
        total_invested = sum(p["invested"] for p in positions)
        return {
            "account_id": acc_id,
            "contour": "demo",
            "total_value": total_value,
            "positions_value": round(positions_value, 2),
            "cash_value": DEMO_CASH,
            "currencies_positions_count": 1,
            "positions_count": len(positions),
            "expected_yield": total_expected_yield,
            "expected_yield_pct": round(total_expected_yield / total_invested * 100, 4),
            "daily_yield": DEMO_DAILY_YIELD,
            "positions": positions,
            "note": DEMO_NOTE,
        }

    def get_operations(
        self,
        account_id: str | None = None,
        days: int = 30,
        operation_types: Sequence[str] | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        acc_id = account_id or self.default_account_id or DEMO_ACCOUNTS[0]["account_id"]
        date_from, date_to = self._period(days)
        blueprint = [
            (32, "OPERATION_TYPE_INPUT", "Пополнение счёта", 50_000.0, None),
            (30, "OPERATION_TYPE_BUY", "Покупка SBER", -53_080.0, "SBER"),
            (30, "OPERATION_TYPE_BROKER_FEE", "Комиссия брокера", -120.0, "SBER"),
            (20, "OPERATION_TYPE_BUY", "Покупка OZON", -59_250.0, "OZON"),
            (15, "OPERATION_TYPE_COUPON", "Купон по облигации", 340.0, None),
            (11, "OPERATION_TYPE_INPUT", "Пополнение счёта", 30_000.0, None),
            (10, "OPERATION_TYPE_SELL", "Продажа GAZP", 12_840.0, "GAZP"),
            (8, "OPERATION_TYPE_DIVIDEND", "Дивиденды SBER", 11_800.0, "SBER"),
            (8, "OPERATION_TYPE_DIVIDEND_TAX", "Налог с дивидендов", -1_534.0, "SBER"),
            (5, "OPERATION_TYPE_OUTPUT", "Вывод средств", -20_000.0, None),
        ]
        wanted = {t.strip().upper() for t in operation_types} if operation_types else None
        operations: list[dict[str, Any]] = []
        for offset, type_name, label, payment, ticker in blueprint:
            moment = date_to - timedelta(days=offset)
            if moment < date_from:
                continue
            if wanted and type_name not in wanted:
                continue
            operations.append(
                {
                    "date": moment.isoformat(),
                    "type": type_name,
                    "type_label": label,
                    "name": label,
                    "description": DEMO_NOTE,
                    "payment": payment,
                    "currency": "RUB",
                    "quantity": None,
                    "price": None,
                    "commission": None,
                    "ticker": ticker or "",
                    "figi": DEMO_INSTRUMENTS[ticker]["figi"] if ticker else "",
                    "instrument_uid": DEMO_INSTRUMENTS[ticker]["uid"] if ticker else "",
                    "state": "OPERATION_STATE_EXECUTED",
                }
            )
        operations.sort(key=lambda o: o["date"], reverse=True)
        return {
            "account_id": acc_id,
            "period": {"days": days, "from": date_from.isoformat(), "to": date_to.isoformat()},
            "count": len(operations),
            "returned": min(len(operations), limit),
            "truncated": len(operations) > limit,
            "operations": operations[:limit],
        }

    # --------------------------------------------------------- рыночные данные
    def _candles_raw(
        self, *, figi: str, date_from: datetime, date_to: datetime, interval: str = "day"
    ) -> list[Any]:
        ticker = self._ticker_by_figi(figi)
        if ticker is None:
            return []
        price = DEMO_PRICES[ticker]
        total_return = DEMO_RETURN_30D.get(ticker, 0.0)
        span_days = max(1, int((date_to - date_from).total_seconds() // 86400))
        points = 5 if interval in {"1min", "5min"} else (12 if interval == "hour" else min(span_days, 90))
        if interval in {"week", "month"}:
            points = max(3, points // 5)
        start_price = price / (1 + total_return / 100)
        rnd = random.Random(_seed(f"{ticker}:{interval}:{span_days}"))
        candles: list[Any] = []
        step = (date_to - date_from) / max(points - 1, 1)
        for index in range(points):
            progress = index / max(points - 1, 1)
            base = start_price + (price - start_price) * progress
            noise = base * rnd.uniform(-0.012, 0.012) * (1 - abs(progress - 0.5))
            close = round(base + noise, 4)
            open_ = round(close * (1 + rnd.uniform(-0.008, 0.008)), 4)
            high = round(max(open_, close) * (1 + rnd.uniform(0, 0.01)), 4)
            low = round(min(open_, close) * (1 - rnd.uniform(0, 0.01)), 4)
            moment = date_from + step * index
            if index == points - 1:
                close = price
                high = max(high, price)
                low = min(low, price)
            candles.append(
                SimpleNamespace(
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=rnd.randint(100_000, 4_000_000),
                    time=moment,
                    is_complete=True,
                )
            )
        return candles

    def get_last_prices(self, instruments: Sequence[str]) -> dict[str, Any]:
        prices: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        now = self.clock()
        for query in instruments:
            try:
                info = self._resolve_instrument(query)
            except Exception as exc:  # noqa: BLE001
                errors.append({"query": str(query), "error": str(exc)})
                continue
            ticker = info["ticker"]
            prices.append(
                {
                    "ticker": ticker,
                    "name": info.get("name"),
                    "figi": info["figi"],
                    "price": DEMO_PRICES.get(ticker),
                    "currency": "RUB",
                    "time": now.isoformat(),
                }
            )
        prices.sort(key=lambda p: p["ticker"])
        return {"prices": prices, "count": len(prices), "errors": errors or None, "note": DEMO_NOTE}

    def _resolve_instrument(self, query: str) -> dict[str, Any]:
        raw = (query or "").strip()
        if not raw:
            raise ValueError("Пустой идентификатор инструмента.")
        key = raw.upper()
        if key not in DEMO_INSTRUMENTS:
            for ticker, meta in DEMO_INSTRUMENTS.items():
                if raw.lower() in meta["name"].lower() or raw == meta["figi"] or raw == meta["uid"]:
                    key = ticker
                    break
            else:
                raise ValueError(
                    f"В демо-режиме доступны бумаги: {', '.join(sorted(DEMO_INSTRUMENTS))}. "
                    f"«{raw}» не найдена."
                )
        meta = DEMO_INSTRUMENTS[key]
        return {
            "ticker": key,
            "name": meta["name"],
            "figi": meta["figi"],
            "uid": meta["uid"],
            "asset_uid": meta["asset_uid"],
            "class_code": meta["class_code"],
            "isin": meta["isin"],
            "currency": "RUB",
            "lot": meta["lot"],
            "instrument_type": meta["instrument_type"],
            "exchange": "MOEX",
            "sector": meta["sector"],
        }

    def search_instrument(self, query: str, limit: int = 5) -> dict[str, Any]:
        query_lower = (query or "").strip().lower()
        results = []
        for ticker, meta in DEMO_INSTRUMENTS.items():
            if query_lower and query_lower not in ticker.lower() and query_lower not in meta["name"].lower():
                continue
            results.append(
                {
                    "ticker": ticker,
                    "name": meta["name"],
                    "figi": meta["figi"],
                    "uid": meta["uid"],
                    "class_code": meta["class_code"],
                    "instrument_type": meta["instrument_type"],
                    "isin": meta["isin"],
                    "lot": meta["lot"],
                    "tradable": True,
                }
            )
        return {"query": query, "results": results[:limit], "count": len(results), "note": DEMO_NOTE}

    def get_instrument_info(self, instrument: str, with_price: bool = True) -> dict[str, Any]:
        info = dict(self._resolve_instrument(instrument))
        info["sector"] = DEMO_INSTRUMENTS[info["ticker"]]["sector"]
        if with_price:
            info["last_price"] = DEMO_PRICES.get(info["ticker"])
            info["price_time"] = self.clock().isoformat()
        info["note"] = DEMO_NOTE
        return info

    def _fetch_fundamentals(self, asset_uids: Sequence[str]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for ticker, meta in DEMO_INSTRUMENTS.items():
            asset_uid = meta["asset_uid"]
            if not asset_uid or asset_uid not in asset_uids:
                continue
            values = DEMO_FUNDAMENTALS.get(ticker, {})
            raw = SimpleNamespace(
                asset_uid=asset_uid,
                currency="RUB",
                **{field: values.get(field, 0.0) for field in _fundamental_field_names()},
            )
            result.append({"asset_uid": asset_uid, "raw": raw})
        return result

    def get_watchlist(self, with_prices: bool = True) -> dict[str, Any]:
        result = super().get_watchlist(with_prices=with_prices)
        result["note"] = DEMO_NOTE
        return result

    @staticmethod
    def _ticker_by_figi(figi: str) -> str | None:
        for ticker, meta in DEMO_INSTRUMENTS.items():
            if meta["figi"] == figi:
                return ticker
        return None


def _fundamental_field_names() -> tuple[str, ...]:
    from .tinvest_client import FUNDAMENTAL_FIELDS

    return tuple(api_field for api_field, _, _ in FUNDAMENTAL_FIELDS)


def build_demo_client(
    watchlist: Sequence[str] | None = None, default_account_id: str = "sbx-1001"
) -> DemoTInvestClient:
    """Готовый демо-клиент с вотчлистом по умолчанию."""
    return DemoTInvestClient(
        watchlist=watchlist or ("SBER", "GAZP", "LKOH", "YDEX", "OZON"),
        default_account_id=default_account_id,
        clock=lambda: datetime.now(tz=timezone.utc),
    )


__all__ = [
    "DEMO_ACCOUNTS",
    "DEMO_FUNDAMENTALS",
    "DEMO_INSTRUMENTS",
    "DEMO_NOTE",
    "DEMO_POSITIONS",
    "DEMO_PRICES",
    "DemoTInvestClient",
    "build_demo_client",
]

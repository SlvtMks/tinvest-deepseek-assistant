"""Общие фикстуры: объекты, похожие на ответы T-Invest SDK, без сети и gRPC."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from tinvest_ai.config import Settings


def q(units: int = 0, nano: int = 0) -> SimpleNamespace:
    """Аналог ``Quotation``/``MoneyValue`` из SDK."""
    return SimpleNamespace(units=units, nano=nano)


def mv(units: int = 0, nano: int = 0, currency: str = "rub") -> SimpleNamespace:
    return SimpleNamespace(units=units, nano=nano, currency=currency)


def enum(name: str) -> SimpleNamespace:
    """An enum-like объект: у настоящих SDK-энумов есть .name."""
    return SimpleNamespace(name=name, value=0)


def _fundamental(asset_uid: str, **fields: Any) -> SimpleNamespace:
    """Фундаменталка с нулями по всем полям, которые не заданы явно."""
    from tinvest_ai.tinvest_client import FUNDAMENTAL_FIELDS

    values = {name: 0.0 for name, _, _ in FUNDAMENTAL_FIELDS}
    values.update(fields)
    return SimpleNamespace(asset_uid=asset_uid, currency="rub", **values)


def now() -> datetime:
    return datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)


def candle(day: int, open_: float, close: float, volume: int = 1000) -> SimpleNamespace:
    base = now() - timedelta(days=30 - day)
    return SimpleNamespace(
        open=q(int(open_), int(round((open_ % 1) * 1e9))),
        high=q(int(max(open_, close) + 1)),
        low=q(int(min(open_, close) - 1)),
        close=q(int(close), int(round((close % 1) * 1e9))),
        volume=volume,
        time=base,
        is_complete=True,
    )


class FakeServices:
    """Минимальный двойник ``Client``: users / operations / market_data / instruments."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.accounts_response = SimpleNamespace(
            accounts=[
                SimpleNamespace(
                    id="acc-1",
                    name="Брокерский",
                    type=enum("ACCOUNT_TYPE_TINKOFF"),
                    status=enum("ACCOUNT_STATUS_OPEN"),
                    opened_date=now(),
                ),
                SimpleNamespace(
                    id="acc-2",
                    name="ИИС",
                    type=enum("ACCOUNT_TYPE_TINKOFF_IIS"),
                    status=enum("ACCOUNT_STATUS_OPEN"),
                    opened_date=now(),
                ),
            ]
        )
        self.portfolio_response = SimpleNamespace(
            total_amount_portfolio=mv(500_000, 0),
            expected_yield=mv(45_000, 0),
            daily_yield=mv(-1200, 0),
            total_amount_currencies=[mv(100_000, 0)],
            positions=[
                SimpleNamespace(
                    figi="FIGI_SBER",
                    instrument_type="share",
                    quantity=q(100),
                    average_position_price=mv(250, 0),
                    current_price=mv(300, 0),
                    expected_yield=q(5000),
                    daily_yield=q(-100),
                    ticker="SBER",
                    class_code="TQBR",
                    instrument_uid="uid-sber",
                    position_uid="pos-sber",
                    blocked=False,
                ),
                SimpleNamespace(
                    figi="FIGI_GAZP",
                    instrument_type="share",
                    quantity=q(200),
                    average_position_price=mv(150, 0),
                    current_price=mv(120, 0),
                    expected_yield=q(-6000),
                    daily_yield=q(-50),
                    ticker="GAZP",
                    class_code="TQBR",
                    instrument_uid="uid-gazp",
                    position_uid="pos-gazp",
                    blocked=False,
                ),
                SimpleNamespace(
                    figi="",
                    instrument_type="currency",
                    quantity=q(100_000),
                    average_position_price=mv(1, 0),
                    current_price=mv(1, 0),
                    expected_yield=q(0),
                    daily_yield=q(0),
                    ticker="RUB",
                    class_code="",
                    instrument_uid="",
                    position_uid="",
                    blocked=False,
                ),
                SimpleNamespace(
                    figi="FIGI_ZERO",
                    instrument_type="share",
                    quantity=q(0),
                    average_position_price=mv(10, 0),
                    current_price=mv(10, 0),
                    expected_yield=q(0),
                    daily_yield=q(0),
                    ticker="ZERO",
                    class_code="TQBR",
                    instrument_uid="uid-zero",
                    position_uid="pos-zero",
                    blocked=False,
                ),
            ],
        )
        self.operations_response = SimpleNamespace(
            operations=[
                SimpleNamespace(
                    id="op-1",
                    type=enum("OPERATION_TYPE_INPUT"),
                    state=enum("OPERATION_STATE_EXECUTED"),
                    name="Пополнение",
                    description="",
                    payment=mv(50_000, 0),
                    price=mv(0, 0),
                    commission=mv(0, 0),
                    quantity=0,
                    figi="",
                    instrument_uid="",
                    ticker="",
                    date=now() - timedelta(days=5),
                ),
                SimpleNamespace(
                    id="op-2",
                    type=enum("OPERATION_TYPE_OUTPUT"),
                    state=enum("OPERATION_STATE_EXECUTED"),
                    name="Вывод",
                    description="",
                    payment=mv(-20_000, 0),
                    price=mv(0, 0),
                    commission=mv(0, 0),
                    quantity=0,
                    figi="",
                    instrument_uid="",
                    ticker="",
                    date=now() - timedelta(days=3),
                ),
                SimpleNamespace(
                    id="op-3",
                    type=enum("OPERATION_TYPE_DIVIDEND"),
                    state=enum("OPERATION_STATE_EXECUTED"),
                    name="Дивиденды SBER",
                    description="",
                    payment=mv(1_500, 0),
                    price=mv(0, 0),
                    commission=mv(0, 0),
                    quantity=0,
                    figi="FIGI_SBER",
                    instrument_uid="uid-sber",
                    ticker="SBER",
                    date=now() - timedelta(days=2),
                ),
                SimpleNamespace(
                    id="op-4",
                    type=enum("OPERATION_TYPE_BROKER_FEE"),
                    state=enum("OPERATION_STATE_EXECUTED"),
                    name="Комиссия",
                    description="",
                    payment=mv(-45, 0),
                    price=mv(0, 0),
                    commission=mv(0, 0),
                    quantity=0,
                    figi="FIGI_SBER",
                    instrument_uid="uid-sber",
                    ticker="SBER",
                    date=now() - timedelta(days=1),
                ),
                SimpleNamespace(
                    id="op-5",
                    type=enum("OPERATION_TYPE_BUY"),
                    state=enum("OPERATION_STATE_EXECUTED"),
                    name="Покупка SBER",
                    description="",
                    payment=mv(-30_000, 0),
                    price=mv(300, 0),
                    commission=mv(-3, 0),
                    quantity=100,
                    figi="FIGI_SBER",
                    instrument_uid="uid-sber",
                    ticker="SBER",
                    date=now(),
                ),
            ]
        )
        self.candles_by_figi: dict[str, list[SimpleNamespace]] = {
            "FIGI_SBER": [candle(day, open_=100 + day, close=101 + day) for day in range(1, 31)],
            "FIGI_GAZP": [candle(day, open_=200 - day, close=199 - day) for day in range(1, 31)],
        }
        self.last_prices_response = SimpleNamespace(
            last_prices=[
                SimpleNamespace(figi="FIGI_SBER", price=q(318, 750_000_000), time=now()),
                SimpleNamespace(figi="FIGI_GAZP", price=q(121, 350_000_000), time=now()),
            ]
        )
        self.find_instrument_response = SimpleNamespace(
            instruments=[
                SimpleNamespace(
                    figi="FIGI_SBER",
                    ticker="SBER",
                    class_code="TQBR",
                    isin="RU0009029540",
                    instrument_kind=enum("INSTRUMENT_TYPE_SHARE"),
                    instrument_type="share",
                    name="Сбербанк",
                    uid="uid-sber",
                    position_uid="pos-sber",
                    lot=10,
                    api_trade_available_flag=True,
                )
            ]
        )
        self.instruments_by_uid = {
            "uid-sber": SimpleNamespace(
                figi="FIGI_SBER",
                ticker="SBER",
                class_code="TQBR",
                isin="RU0009029540",
                lot=10,
                currency="rub",
                name="Сбербанк",
                exchange="MOEX",
                instrument_type="share",
                uid="uid-sber",
                position_uid="pos-sber",
                asset_uid="asset-sber",
            ),
            "uid-gazp": SimpleNamespace(
                figi="FIGI_GAZP",
                ticker="GAZP",
                class_code="TQBR",
                isin="RU0007661625",
                lot=10,
                currency="rub",
                name="Газпром",
                exchange="MOEX",
                instrument_type="share",
                uid="uid-gazp",
                position_uid="pos-gazp",
                asset_uid="asset-gazp",
            ),
        }
        self.instrument_response = SimpleNamespace(instrument=self.instruments_by_uid["uid-sber"])
        self.fundamentals_by_uid = {
            "asset-sber": _fundamental(
                "asset-sber",
                market_capitalization=5_760_000_000_000.0,
                pe_ratio_ttm=4.1,
                price_to_book_ttm=1.0,
                price_to_sales_ttm=1.6,
                ev_to_ebitda_mrq=3.4,
                roe=0.241,
                roa=0.031,
                roic=0.18,
                net_margin_mrq=0.38,
                eps_ttm=77.7,
                dividend_yield_daily_ttm=0.105,
                dividends_per_share=33.3,
                total_debt_to_equity_mrq=0.0,
                beta=1.05,
                high_price_last_52_weeks=340.0,
                low_price_last_52_weeks=245.0,
                number_of_employees=210_000,
            ),
            "asset-gazp": _fundamental(
                "asset-gazp",
                market_capitalization=2_870_000_000_000.0,
                pe_ratio_ttm=3.2,
                price_to_book_ttm=0.4,
                roe=0.121,
                eps_ttm=37.9,
                dividend_yield_daily_ttm=0.0,
            ),
        }
        self.fundamentals_response = SimpleNamespace(fundamentals=[self.fundamentals_by_uid["asset-sber"]])
        self.users = SimpleNamespace(get_accounts=self._record("users.get_accounts"))
        self.operations = SimpleNamespace(
            get_portfolio=self._record("operations.get_portfolio"),
            get_operations=self._record("operations.get_operations"),
        )
        self.market_data = SimpleNamespace(
            get_candles=self._record("market_data.get_candles", self._candles),
            get_last_prices=self._record("market_data.get_last_prices", self._last_prices),
        )
        self.instruments = SimpleNamespace(
            find_instrument=self._record("instruments.find_instrument", self._find),
            get_instrument_by=self._record("instruments.get_instrument_by", self._instrument),
            get_asset_fundamentals=self._record("instruments.get_asset_fundamentals", self._fundamentals),
        )

    # ------------------------------------------------------------------ helpers
    def _record(self, name: str, result_factory: Any = None) -> Any:
        def caller(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, kwargs))
            if result_factory is not None:
                return result_factory(*args, **kwargs)
            default = {
                "users.get_accounts": self.accounts_response,
                "operations.get_portfolio": self.portfolio_response,
                "operations.get_operations": self.operations_response,
            }[name]
            return default

        return caller

    def _candles(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(candles=self.candles_by_figi.get(kwargs.get("figi", ""), []))

    def _last_prices(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        figi = kwargs.get("figi") or []
        prices = [p for p in self.last_prices_response.last_prices if p.figi in figi]
        return SimpleNamespace(last_prices=prices)

    def _find(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        query = str(kwargs.get("query", "")).upper()
        mapping = {
            "SBER": "uid-sber",
            "СБЕРБАНК": "uid-sber",
            "GAZP": "uid-gazp",
            "ГАЗПРОМ": "uid-gazp",
        }
        uid = mapping.get(query)
        if uid is None:
            raise RuntimeError("not found")
        instrument = self.instruments_by_uid[uid]
        return SimpleNamespace(
            instruments=[
                SimpleNamespace(
                    figi=instrument.figi,
                    ticker=instrument.ticker,
                    class_code=instrument.class_code,
                    isin=instrument.isin,
                    instrument_kind=enum("INSTRUMENT_TYPE_SHARE"),
                    instrument_type=instrument.instrument_type,
                    name=instrument.name,
                    uid=instrument.uid,
                    position_uid=instrument.position_uid,
                    lot=instrument.lot,
                    api_trade_available_flag=True,
                )
            ]
        )

    def _instrument(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        instrument_id = str(kwargs.get("id") or "")
        mapping = {"uid-sber": "uid-sber", "SBER": "uid-sber", "uid-gazp": "uid-gazp", "GAZP": "uid-gazp"}
        uid = mapping.get(instrument_id)
        if uid is None:
            raise RuntimeError(f"instrument {instrument_id} not found")
        return SimpleNamespace(instrument=self.instruments_by_uid[uid])

    def _fundamentals(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        if args:
            request = args[0]
            assets = list(getattr(request, "assets", None) or [])
            return SimpleNamespace(
                fundamentals=[
                    self.fundamentals_by_uid[uid] for uid in assets if uid in self.fundamentals_by_uid
                ]
            )
        asset_uid = kwargs.get("asset_uid")
        if asset_uid in self.fundamentals_by_uid:
            return SimpleNamespace(fundamentals=[self.fundamentals_by_uid[asset_uid]])
        return SimpleNamespace(fundamentals=[])

    def count(self, name: str) -> int:
        return sum(1 for call_name, _ in self.calls if call_name == name)


@pytest.fixture()
def fake_services() -> FakeServices:
    return FakeServices()


@pytest.fixture()
def settings() -> Settings:
    return Settings.from_env(
        env={
            "TINVEST_TOKEN": "t.test-token-value-1234567890",
            "DEEPSEEK_API_KEY": "sk-test-key-0987654321",
            "WATCHLIST": "SBER, GAZP ,sber",
            "TINVEST_SANDBOX": "true",
        },
        load_dotenv_file=False,
    )


@pytest.fixture()
def demo_settings() -> Settings:
    return Settings.from_env(env={"DEMO_MODE": "true", "WATCHLIST": "SBER,GAZP,LKOH"}, load_dotenv_file=False)

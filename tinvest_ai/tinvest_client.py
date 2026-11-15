"""Тонкая обёртка над официальным Python SDK Т-Инвестиций (только чтение).

Задачи слоя:

* держать одно gRPC-соединение на всё время работы приложения;
* превращать ``Quotation``/``MoneyValue``/enum-ы SDK в обычный JSON
  (dict/list/str/float) — именно это увидит LLM в ответах инструментов;
* ретраить шумные ошибки и кэшировать рыночные данные;
* не давать модели «сырой» API: каждый метод отдаёт уже осмысленный срез.

Никаких торговых методов здесь нет и быть не должно: используется read-only
токен и исключительно Get-методы.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from . import sdk
from .cache import TTLCache
from .formatting import (
    money_currency,
    normalize_percent,
    percent,
    pick_positive,
    to_float,
    to_quantity,
)
from .retry import retry_call

LOGGER = logging.getLogger(__name__)

ACCOUNT_TYPE_LABELS = {
    "ACCOUNT_TYPE_TINKOFF": "Брокерский",
    "ACCOUNT_TYPE_TINKOFF_IIS": "ИИС",
    "ACCOUNT_TYPE_INVEST_BOX": "Инвесткопилка",
    "ACCOUNT_TYPE_INVEST_FUND": "ПИФ",
    "ACCOUNT_TYPE_DEBIT": "Дебетовый счёт",
    "ACCOUNT_TYPE_SAVING": "Сберегательный счёт",
    "ACCOUNT_TYPE_DFA": "ЦФА",
    "ACCOUNT_TYPE_UNSPECIFIED": "Неизвестный тип",
}

ACCOUNT_STATUS_LABELS = {
    "ACCOUNT_STATUS_NEW": "новый",
    "ACCOUNT_STATUS_OPEN": "открыт",
    "ACCOUNT_STATUS_CLOSED": "закрыт",
}

INSTRUMENT_TYPE_LABELS = {
    "share": "акция",
    "bond": "облигация",
    "etf": "фонд",
    "currency": "валюта",
    "futures": "фьючерс",
    "option": "опцион",
    "sp": "структурная нота",
    "clearing_certificate": "клиринговая справка",
    "index": "индекс",
    "commodity": "товар",
    "dfa": "ЦФА",
}

OPERATION_TYPE_LABELS = {
    "OPERATION_TYPE_INPUT": "Пополнение",
    "OPERATION_TYPE_INPUT_SWIFT": "Пополнение (SWIFT)",
    "OPERATION_TYPE_INPUT_ACQUIRING": "Пополнение (эквайринг)",
    "OPERATION_TYPE_INP_MULTI": "Пополнение",
    "OPERATION_TYPE_INPUT_SECURITIES": "Завод бумаг",
    "OPERATION_TYPE_OUTPUT": "Вывод",
    "OPERATION_TYPE_OUTPUT_SWIFT": "Вывод (SWIFT)",
    "OPERATION_TYPE_OUTPUT_ACQUIRING": "Вывод (эквайринг)",
    "OPERATION_TYPE_OUT_MULTI": "Вывод",
    "OPERATION_TYPE_OUTPUT_SECURITIES": "Вывод бумаг",
    "OPERATION_TYPE_BUY": "Покупка",
    "OPERATION_TYPE_BUY_CARD": "Покупка с карты",
    "OPERATION_TYPE_BUY_MARGIN": "Покупка в маржу",
    "OPERATION_TYPE_SELL": "Продажа",
    "OPERATION_TYPE_SELL_CARD": "Продажа на карту",
    "OPERATION_TYPE_SELL_MARGIN": "Продажа из маржи",
    "OPERATION_TYPE_DELIVERY_BUY": "Поставка (покупка)",
    "OPERATION_TYPE_DELIVERY_SELL": "Поставка (продажа)",
    "OPERATION_TYPE_DIVIDEND": "Дивиденды",
    "OPERATION_TYPE_DIVIDEND_TRANSFER": "Дивиденды",
    "OPERATION_TYPE_DIV_EXT": "Дивиденды",
    "OPERATION_TYPE_COUPON": "Купон",
    "OPERATION_TYPE_BOND_REPAYMENT": "Погашение облигации",
    "OPERATION_TYPE_BOND_REPAYMENT_FULL": "Полное погашение облигации",
    "OPERATION_TYPE_TAX": "Налог",
    "OPERATION_TYPE_TAX_PROGRESSIVE": "Налог",
    "OPERATION_TYPE_TAX_CORRECTION": "Корректировка налога",
    "OPERATION_TYPE_TAX_CORRECTION_COUPON": "Корректировка налога по купону",
    "OPERATION_TYPE_DIVIDEND_TAX": "Налог с дивидендов",
    "OPERATION_TYPE_DIVIDEND_TAX_PROGRESSIVE": "Налог с дивидендов",
    "OPERATION_TYPE_BOND_TAX": "Налог с облигаций",
    "OPERATION_TYPE_BOND_TAX_PROGRESSIVE": "Налог с облигаций",
    "OPERATION_TYPE_BENEFIT_TAX": "Налог с материальной выгоды",
    "OPERATION_TYPE_BROKER_FEE": "Комиссия брокера",
    "OPERATION_TYPE_SERVICE_FEE": "Комиссия за обслуживание",
    "OPERATION_TYPE_MARGIN_FEE": "Комиссия за маржу",
    "OPERATION_TYPE_SUCCESS_FEE": "Комиссия за результат",
    "OPERATION_TYPE_TRACK_MFEE": "Комиссия фонда (management)",
    "OPERATION_TYPE_TRACK_PFEE": "Комиссия фонда (performance)",
    "OPERATION_TYPE_OTHER_FEE": "Прочая комиссия",
    "OPERATION_TYPE_ADVICE_FEE": "Комиссия за управление",
    "OPERATION_TYPE_CASH_FEE": "Комиссия за перевод",
    "OPERATION_TYPE_OUT_FEE": "Комиссия за вывод",
    "OPERATION_TYPE_OUT_STAMP_DUTY": "Госпошлина",
    "OPERATION_TYPE_OUTPUT_PENALTY": "Штраф",
    "OPERATION_TYPE_OVERNIGHT": "Овернайт",
    "OPERATION_TYPE_ACCRUING_VARMARGIN": "Начисление вариационной маржи",
    "OPERATION_TYPE_WRITING_OFF_VARMARGIN": "Списание вариационной маржи",
    "OPERATION_TYPE_TRANS_IIS_BS": "Перевод ИИС ↔ БС",
    "OPERATION_TYPE_TRANS_BS_BS": "Перевод между счетами",
    "OPERATION_TYPE_DFA_REDEMPTION": "Погашение ЦФА",
    "OPERATION_TYPE_PRIMARY_ORDER": "Первичное размещение",
    "OPERATION_TYPE_OTHER": "Прочее",
}

_DEPOSIT_TYPES = frozenset(
    {
        "OPERATION_TYPE_INPUT",
        "OPERATION_TYPE_INPUT_SWIFT",
        "OPERATION_TYPE_INPUT_ACQUIRING",
        "OPERATION_TYPE_INP_MULTI",
        "OPERATION_TYPE_INPUT_SECURITIES",
    }
)
_WITHDRAW_TYPES = frozenset(
    {
        "OPERATION_TYPE_OUTPUT",
        "OPERATION_TYPE_OUTPUT_SWIFT",
        "OPERATION_TYPE_OUTPUT_ACQUIRING",
        "OPERATION_TYPE_OUT_MULTI",
        "OPERATION_TYPE_OUTPUT_SECURITIES",
    }
)
_TRANSFER_TYPES = frozenset({"OPERATION_TYPE_TRANS_IIS_BS", "OPERATION_TYPE_TRANS_BS_BS"})
_DIVIDEND_TYPES = frozenset(
    {"OPERATION_TYPE_DIVIDEND", "OPERATION_TYPE_DIVIDEND_TRANSFER", "OPERATION_TYPE_DIV_EXT"}
)
_COUPON_TYPES = frozenset({"OPERATION_TYPE_COUPON"})
_TRADE_TYPES = frozenset(
    {
        "OPERATION_TYPE_BUY",
        "OPERATION_TYPE_BUY_CARD",
        "OPERATION_TYPE_BUY_MARGIN",
        "OPERATION_TYPE_SELL",
        "OPERATION_TYPE_SELL_CARD",
        "OPERATION_TYPE_SELL_MARGIN",
        "OPERATION_TYPE_DELIVERY_BUY",
        "OPERATION_TYPE_DELIVERY_SELL",
    }
)

CANDLE_INTERVALS = {
    "1min": ("CANDLE_INTERVAL_1_MIN", timedelta(days=1), "1 минута"),
    "5min": ("CANDLE_INTERVAL_5_MIN", timedelta(days=1), "5 минут"),
    "hour": ("CANDLE_INTERVAL_HOUR", timedelta(weeks=1), "1 час"),
    "day": ("CANDLE_INTERVAL_DAY", timedelta(days=365), "1 день"),
    "week": ("CANDLE_INTERVAL_WEEK", timedelta(days=365 * 2), "1 неделя"),
    "month": ("CANDLE_INTERVAL_MONTH", timedelta(days=365 * 3), "1 месяц"),
}

# Поля фундаментальных показателей: (поле API, подпись, тип)
FUNDAMENTAL_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("market_capitalization", "Рыночная капитализация", "money"),
    ("pe_ratio_ttm", "P/E (TTM)", "ratio"),
    ("price_to_book_ttm", "P/B (TTM)", "ratio"),
    ("price_to_sales_ttm", "P/S (TTM)", "ratio"),
    ("ev_to_ebitda_mrq", "EV/EBITDA", "ratio"),
    ("total_enterprise_value_mrq", "EV", "money"),
    ("roe", "ROE", "percent"),
    ("roa", "ROA", "percent"),
    ("roic", "ROIC", "percent"),
    ("net_margin_mrq", "Чистая маржа", "percent"),
    ("net_interest_margin_mrq", "Процентная маржа", "percent"),
    ("eps_ttm", "EPS (TTM)", "number"),
    ("diluted_eps_ttm", "Разводнённый EPS", "number"),
    ("revenue_ttm", "Выручка (TTM)", "money"),
    ("ebitda_ttm", "EBITDA (TTM)", "money"),
    ("net_income_ttm", "Чистая прибыль (TTM)", "money"),
    ("free_cash_flow_ttm", "Свободный денежный поток (TTM)", "money"),
    ("dividend_yield_daily_ttm", "Дивидендная доходность (TTM)", "percent"),
    ("dividends_per_share", "Дивиденды на акцию", "number"),
    ("dividend_payout_ratio_fy", "Payout ratio", "percent"),
    ("five_years_average_dividend_yield", "Средняя див. доходность за 5 лет", "percent"),
    ("total_debt_to_equity_mrq", "Долг / Капитал", "ratio"),
    ("net_debt_to_ebitda", "Чистый долг / EBITDA", "ratio"),
    ("current_ratio_mrq", "Коэффициент текущей ликвидности", "ratio"),
    ("beta", "Бета", "number"),
    ("high_price_last_52_weeks", "Максимум за 52 недели", "number"),
    ("low_price_last_52_weeks", "Минимум за 52 недели", "number"),
    ("average_daily_volume_last_10_days", "Средний объём за 10 дней", "number"),
    ("one_year_annual_revenue_growth_rate", "Рост выручки за год", "percent"),
    ("three_year_annual_revenue_growth_rate", "Среднегодовой рост выручки за 3 года", "percent"),
    ("five_year_annual_revenue_growth_rate", "Среднегодовой рост выручки за 5 лет", "percent"),
    ("five_year_annual_dividend_growth_rate", "Среднегодовой рост дивидендов за 5 лет", "percent"),
    ("number_of_employees", "Сотрудников", "number"),
    ("free_float", "Free float", "number"),
)


class TInvestError(RuntimeError):
    """Ошибка уровня приложения (понятный текст для пользователя/модели)."""


@dataclass
class _Account:
    id: str
    name: str
    type: str
    status: str
    opened_date: str | None


@dataclass
class TInvestClient:
    """Read-only клиент T-Invest API.

    :param token: read-only токен (или токен песочницы).
    :param sandbox: ``True`` — работать через контур песочницы.
    :param retries: число попыток на один вызов API.
    :param cache_ttl: TTL кэша рыночных данных в секундах (0 — выключить).
    """

    token: str
    sandbox: bool = True
    retries: int = 3
    cache_ttl: int = 60
    default_account_id: str = ""
    watchlist: tuple[str, ...] = ()
    max_performance_positions: int = 20
    logger: logging.Logger = field(default=LOGGER, repr=False)
    client_factory: Callable[..., Any] | None = field(default=None, repr=False)
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(tz=timezone.utc), repr=False)

    _client: Any = field(default=None, init=False, repr=False)
    _stack: contextlib.ExitStack | None = field(default=None, init=False, repr=False)
    _cache: TTLCache = field(default=None, init=False, repr=False)  # type: ignore[assignment]
    _instruments: dict[str, dict[str, Any]] = field(default_factory=dict, init=False, repr=False)
    _accounts: list[_Account] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self._cache = TTLCache(ttl=self.cache_ttl)

    # ------------------------------------------------------------- соединение
    def _services(self) -> Any:
        """Ленивая инициализация SDK-клиента (одно соединение на процесс)."""
        if self._client is not None:
            return self._client
        sdk.require_sdk()
        if not self.token:
            raise TInvestError("Не задан TINVEST_TOKEN — нечего отправлять в API.")
        target = sdk.INVEST_GRPC_API_SANDBOX if self.sandbox else sdk.INVEST_GRPC_API
        factory = self.client_factory or sdk.Client
        self._stack = contextlib.ExitStack()
        try:
            client = factory(self.token, target=target)
        except TypeError:  # старые версии SDK без target
            client = factory(self.token)
        self._client = self._stack.enter_context(client)
        self.logger.info(
            "Подключение к T-Invest API установлено (контур: %s)",
            "sandbox" if self.sandbox else "prod",
        )
        return self._client

    def close(self) -> None:
        if self._stack is not None:
            self._stack.close()
            self._stack = None
            self._client = None

    def __enter__(self) -> TInvestClient:
        self._services()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def _call(self, func: Callable[..., Any], *args: Any, operation: str = "", **kwargs: Any) -> Any:
        return retry_call(
            func,
            *args,
            attempts=self.retries,
            logger=self.logger,
            operation=operation,
            **kwargs,
        )

    # --------------------------------------------------------------- accounts
    def list_accounts(self) -> dict[str, Any]:
        """Список доступных счетов."""
        response = self._call(self._services().users.get_accounts, operation="get_accounts")
        accounts: list[_Account] = []
        for item in response.accounts:
            accounts.append(
                _Account(
                    id=str(getattr(item, "id", "")),
                    name=str(getattr(item, "name", "") or ""),
                    type=str(getattr(getattr(item, "type", ""), "name", item.type)),
                    status=str(getattr(getattr(item, "status", ""), "name", item.status)),
                    opened_date=_iso(getattr(item, "opened_date", None)),
                )
            )
        self._accounts = accounts
        return {
            "accounts": [
                {
                    "account_id": acc.id,
                    "name": acc.name,
                    "type": ACCOUNT_TYPE_LABELS.get(acc.type, acc.type),
                    "status": ACCOUNT_STATUS_LABELS.get(acc.status, acc.status),
                    "opened_date": acc.opened_date,
                }
                for acc in accounts
            ],
            "count": len(accounts),
            "contour": "sandbox" if self.sandbox else "prod",
        }

    def _resolve_account(self, account_id: str | None) -> str:
        if account_id:
            return account_id
        if self.default_account_id:
            return self.default_account_id
        if not self._accounts:
            self._accounts = [
                _Account(
                    id=a["account_id"],
                    name=a["name"],
                    type="",
                    status="",
                    opened_date=None,
                )
                for a in self.list_accounts()["accounts"]
            ]
        if not self._accounts:
            raise TInvestError(
                "В этом контуре нет доступных счетов. "
                "Для песочницы создайте счёт в приложении Т-Инвестиций (раздел «Песочница»)."
            )
        return self._accounts[0].id

    def _account_hint(self, account_id: str) -> str:
        if len(self._accounts) > 1:
            return (
                f"Использован счёт {account_id} (первый из {len(self._accounts)}). "
                "Если у пользователя несколько счетов — уточните, какой нужен."
            )
        return ""

    # -------------------------------------------------------------- portfolio
    def get_portfolio(
        self, account_id: str | None = None, include_zero_positions: bool = False
    ) -> dict[str, Any]:
        """Текущий портфель: позиции, стоимость, доходность."""
        services = self._services()
        acc_id = self._resolve_account(account_id)
        response = self._call(services.operations.get_portfolio, account_id=acc_id, operation="get_portfolio")

        total_portfolio = to_float(getattr(response, "total_amount_portfolio", None), 2) or 0.0
        positions: list[dict[str, Any]] = []
        cash_total = 0.0
        currencies_count = len(getattr(response, "total_amount_currencies", None) or [])

        for position in getattr(response, "positions", []) or []:
            quantity = to_quantity(getattr(position, "quantity", None)) or 0.0
            if quantity == 0 and not include_zero_positions:
                continue
            raw_type = str(getattr(position, "instrument_type", "") or "").lower()
            price = to_float(getattr(position, "current_price", None), 6)
            avg_price = to_float(getattr(position, "average_position_price", None), 6)
            currency = (
                money_currency(getattr(position, "current_price", None))
                or money_currency(getattr(position, "average_position_price", None))
                or ""
            )
            value = round(quantity * price, 2) if price is not None else None
            invested = round(quantity * avg_price, 2) if avg_price is not None else None
            expected_yield = to_float(getattr(position, "expected_yield", None), 2)
            daily_yield = to_float(getattr(position, "daily_yield", None), 2)

            if raw_type == "currency":
                cash_total += value or 0.0
                continue

            positions.append(
                {
                    "ticker": str(getattr(position, "ticker", "") or ""),
                    "figi": str(getattr(position, "figi", "") or ""),
                    "instrument_uid": str(getattr(position, "instrument_uid", "") or ""),
                    "instrument_type": INSTRUMENT_TYPE_LABELS.get(raw_type, raw_type or "неизвестно"),
                    "quantity": quantity,
                    "average_price": avg_price,
                    "current_price": price,
                    "currency": currency.upper(),
                    "value": value,
                    "invested": invested,
                    "expected_yield": expected_yield,
                    "expected_yield_pct": percent(expected_yield, invested),
                    "daily_yield": daily_yield,
                    "share_in_portfolio_pct": percent(value, total_portfolio),
                    "blocked": bool(getattr(position, "blocked", False)),
                }
            )

        positions.sort(key=lambda p: p.get("share_in_portfolio_pct") or 0, reverse=True)
        total_expected_yield = to_float(getattr(response, "expected_yield", None), 2)
        total_invested = sum(p["invested"] or 0 for p in positions)

        result = {
            "account_id": acc_id,
            "contour": "sandbox" if self.sandbox else "prod",
            "total_value": round(total_portfolio, 2),
            "positions_value": round(sum(p["value"] or 0 for p in positions), 2),
            "cash_value": round(cash_total, 2),
            "currencies_positions_count": currencies_count,
            "positions_count": len(positions),
            "expected_yield": total_expected_yield,
            "expected_yield_pct": percent(total_expected_yield, total_invested),
            "daily_yield": to_float(getattr(response, "daily_yield", None), 2),
            "positions": positions,
        }
        hint = self._account_hint(acc_id)
        if hint:
            result["note"] = hint
        if not positions:
            result["note"] = (
                result.get("note", "")
                + " Портфель пуст (в песочнице позиции не появятся сами — их нужно купить внутри песочницы)."
            ).strip()
        return result

    # ------------------------------------------------------------- operations
    def get_operations(
        self,
        account_id: str | None = None,
        days: int = 30,
        operation_types: Sequence[str] | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Операции за период (пополнения, сделки, дивиденды, налоги…)."""
        services = self._services()
        acc_id = self._resolve_account(account_id)
        date_from, date_to = self._period(days)
        response = self._call(
            services.operations.get_operations,
            account_id=acc_id,
            from_=date_from,
            to=date_to,
            operation="get_operations",
        )

        wanted = {t.strip().upper() for t in operation_types} if operation_types else None
        items: list[dict[str, Any]] = []
        for op in getattr(response, "operations", []) or []:
            type_name = _enum_name(getattr(op, "type", ""))
            if wanted and type_name not in wanted:
                continue
            items.append(
                {
                    "date": _iso(getattr(op, "date", None)),
                    "type": type_name,
                    "type_label": OPERATION_TYPE_LABELS.get(type_name, type_name),
                    "name": str(getattr(op, "name", "") or ""),
                    "description": str(getattr(op, "description", "") or ""),
                    "payment": to_float(getattr(op, "payment", None), 2),
                    "currency": (money_currency(getattr(op, "payment", None)) or "").upper(),
                    "quantity": to_quantity(getattr(op, "quantity", None)),
                    "price": to_float(getattr(op, "price", None), 6),
                    "commission": to_float(getattr(op, "commission", None), 2),
                    "ticker": str(getattr(op, "ticker", "") or ""),
                    "figi": str(getattr(op, "figi", "") or ""),
                    "instrument_uid": str(getattr(op, "instrument_uid", "") or ""),
                    "state": _enum_name(getattr(op, "state", "")),
                }
            )

        items.sort(key=lambda x: x["date"] or "", reverse=True)
        total = len(items)
        truncated = total > limit
        return {
            "account_id": acc_id,
            "period": {"days": days, "from": date_from.isoformat(), "to": date_to.isoformat()},
            "count": total,
            "returned": min(total, limit),
            "truncated": truncated,
            "operations": items[:limit],
        }

    def get_cash_flows(self, account_id: str | None = None, days: int = 30) -> dict[str, Any]:
        """Агрегированные денежные потоки за период: пополнения, выводы, дивиденды и т.д."""
        data = self.get_operations(account_id=account_id, days=days, limit=10_000)
        totals: dict[str, dict[str, float]] = {}
        counters: dict[str, dict[str, int]] = {}

        def bucket(currency: str) -> dict[str, float]:
            return totals.setdefault(
                currency,
                {
                    "deposits": 0.0,
                    "withdrawals": 0.0,
                    "transfers": 0.0,
                    "dividends": 0.0,
                    "coupons": 0.0,
                    "taxes": 0.0,
                    "fees": 0.0,
                    "trades_net": 0.0,
                },
            )

        for op in data["operations"]:
            currency = op["currency"] or "RUB"
            type_name = op["type"]
            amount = abs(op["payment"] or 0.0)
            if type_name in _DEPOSIT_TYPES:
                bucket(currency)["deposits"] += amount
            elif type_name in _WITHDRAW_TYPES:
                bucket(currency)["withdrawals"] += amount
            elif type_name in _TRANSFER_TYPES:
                bucket(currency)["transfers"] += op["payment"] or 0.0
            elif type_name in _DIVIDEND_TYPES:
                bucket(currency)["dividends"] += amount
            elif type_name in _COUPON_TYPES:
                bucket(currency)["coupons"] += amount
            elif "TAX" in type_name:
                bucket(currency)["taxes"] += amount
            elif "FEE" in type_name or type_name.endswith("_PENALTY") or "STAMP_DUTY" in type_name:
                bucket(currency)["fees"] += amount
            elif type_name in _TRADE_TYPES:
                bucket(currency)["trades_net"] += op["payment"] or 0.0
            counter = counters.setdefault(currency, {})
            counter[type_name] = counter.get(type_name, 0) + 1

        result_totals: dict[str, dict[str, float]] = {}
        for currency, values in totals.items():
            rounded = {key: round(value, 2) for key, value in values.items()}
            rounded["net_deposits"] = round(values["deposits"] - values["withdrawals"], 2)
            result_totals[currency] = rounded

        primary = max(counters, key=lambda currency: sum(counters[currency].values())) if counters else "RUB"
        return {
            "account_id": data["account_id"],
            "period": data["period"],
            "operations_count": data["count"],
            "totals": result_totals,
            "primary_currency": primary,
            "by_type": counters.get(primary, {}),
            "note": (
                "deposits — пополнения счёта, withdrawals — выводы, net_deposits = deposits - withdrawals. "
                "trades_net — чистое движение денег по сделкам (покупки отрицательные)."
            ),
        }

    # ------------------------------------------------------------ performance
    def get_performance(self, account_id: str | None = None, days: int = 30) -> dict[str, Any]:
        """Доходность каждой бумаги портфеля за период (по дневным свечам)."""
        portfolio = self.get_portfolio(account_id=account_id)
        date_from, date_to = self._period(days)
        items: list[dict[str, Any]] = []
        skipped: list[str] = []

        eligible = [p for p in portfolio["positions"] if (p.get("value") or 0) > 0 and p.get("figi")]
        positions = eligible[: self.max_performance_positions]

        total_value = sum((p.get("value") or 0) for p in positions) or 0.0
        weighted_sum = 0.0
        for position in positions:
            try:
                candles = self._candles_raw(
                    figi=position["figi"], date_from=date_from, date_to=date_to, interval="day"
                )
            except Exception as exc:  # noqa: BLE001 - одна бумага не должна валить весь отчёт
                self.logger.warning("Нет свечей для %s: %s", position["ticker"], exc)
                skipped.append(position["ticker"])
                continue
            if len(candles) < 2:
                skipped.append(position["ticker"])
                continue
            first_open = to_float(candles[0].open)
            last_close = to_float(candles[-1].close)
            if not first_open or last_close is None:
                skipped.append(position["ticker"])
                continue
            change_pct = round((last_close - first_open) / first_open * 100, 2)
            quantity = position.get("quantity") or 0.0
            pnl = round((last_close - first_open) * quantity, 2)
            weight = (position.get("value") or 0) / total_value * 100 if total_value else None
            if weight:
                weighted_sum += change_pct * weight / 100
            items.append(
                {
                    "ticker": position["ticker"],
                    "instrument_type": position["instrument_type"],
                    "quantity": quantity,
                    "price_start": round(first_open, 4),
                    "price_end": round(last_close, 4),
                    "return_pct": change_pct,
                    "pnl": pnl,
                    "currency": position.get("currency"),
                    "portfolio_weight_pct": round(weight, 2) if weight is not None else None,
                    "candles_used": len(candles),
                }
            )

        items.sort(key=lambda x: x["return_pct"], reverse=True)
        best = items[0] if items else None
        worst = items[-1] if items else None
        return {
            "account_id": portfolio["account_id"],
            "period": {"days": days, "from": date_from.isoformat(), "to": date_to.isoformat()},
            "currency": "RUB",
            "items": items,
            "best": best,
            "worst": worst,
            "portfolio_weighted_return_pct": round(weighted_sum, 2) if items else None,
            "skipped_instruments": skipped,
            "method": (
                "Изменение цены бумаги за период: (последняя цена закрытия − первая цена открытия) / первая цена × 100%. "
                "Дивиденды и купоны не включены — только ценовая доходность."
            ),
        }

    # ---------------------------------------------------------------- market
    def _candles_raw(
        self, *, figi: str, date_from: datetime, date_to: datetime, interval: str = "day"
    ) -> list[Any]:
        cache_key = ("candles", figi, interval, date_from.date().isoformat(), date_to.date().isoformat())
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        interval_name, _, _ = CANDLE_INTERVALS.get(interval, CANDLE_INTERVALS["day"])
        interval_enum = sdk.enum_member("CandleInterval", interval_name)
        response = self._call(
            self._services().market_data.get_candles,
            figi=figi,
            from_=date_from,
            to=date_to,
            interval=interval_enum,
            operation="get_candles",
        )
        candles = list(getattr(response, "candles", []) or [])
        candles.sort(key=lambda c: getattr(c, "time", None) or datetime.min)
        self._cache.set(cache_key, candles)
        return candles

    def get_candles(
        self, instrument: str, days: int = 30, interval: str = "day", limit: int = 60
    ) -> dict[str, Any]:
        """Свечи по бумаге + краткая сводка."""
        interval = interval if interval in CANDLE_INTERVALS else "day"
        _, max_span, interval_label = CANDLE_INTERVALS[interval]
        notes: list[str] = []
        if timedelta(days=days) > max_span:
            capped = max(1, int(max_span.total_seconds() // 86400))
            notes.append(
                f"API отдаёт интервал «{interval_label}» максимум за {capped} дн. — период урезан с {days} до {capped}."
            )
            days = capped

        info = self._resolve_instrument(instrument)
        date_from, date_to = self._period(days)
        candles = self._candles_raw(
            figi=info["figi"], date_from=date_from, date_to=date_to, interval=interval
        )
        if not candles:
            return {
                "ticker": info["ticker"],
                "name": info.get("name"),
                "error": "no_candles",
                "message": "API не вернул свечи за этот период (возможно, торги не проводились).",
            }
        rows = [
            {
                "time": _iso(getattr(candle, "time", None)),
                "open": to_float(getattr(candle, "open", None), 4),
                "high": to_float(getattr(candle, "high", None), 4),
                "low": to_float(getattr(candle, "low", None), 4),
                "close": to_float(getattr(candle, "close", None), 4),
                "volume": to_float(getattr(candle, "volume", None), 0),
            }
            for candle in candles
        ]
        first_open = rows[0]["open"]
        last_close = rows[-1]["close"]
        summary = {
            "first_time": rows[0]["time"],
            "last_time": rows[-1]["time"],
            "first_open": first_open,
            "last_close": last_close,
            "change_pct": round((last_close - first_open) / first_open * 100, 2) if first_open else None,
            "min_close": min((r["close"] for r in rows if r["close"] is not None), default=None),
            "max_close": max((r["close"] for r in rows if r["close"] is not None), default=None),
            "candles_total": len(rows),
        }
        truncated = len(rows) > limit
        return {
            "ticker": info["ticker"],
            "name": info.get("name"),
            "figi": info["figi"],
            "currency": info.get("currency"),
            "interval": interval,
            "period": {"days": days, "from": date_from.isoformat(), "to": date_to.isoformat()},
            "summary": summary,
            "candles": rows[-limit:],
            "truncated": truncated,
            "notes": notes,
        }

    def get_last_prices(self, instruments: Sequence[str]) -> dict[str, Any]:
        """Последние цены по списку бумаг (один запрос на все)."""
        if not instruments:
            return {"error": "empty_request", "message": "Не передан список инструментов."}
        resolved: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for query in instruments[:50]:
            try:
                resolved.append(self._resolve_instrument(query))
            except Exception as exc:  # noqa: BLE001
                errors.append({"query": query, "error": str(exc)})
        if not resolved:
            return {
                "error": "not_found",
                "message": "Не удалось распознать ни одну бумагу.",
                "prices": [],
                "count": 0,
                "errors": errors,
            }

        figi_map = {item["figi"]: item for item in resolved if item.get("figi")}
        response = self._call(
            self._services().market_data.get_last_prices,
            figi=list(figi_map),
            operation="get_last_prices",
        )
        prices: list[dict[str, Any]] = []
        for price in getattr(response, "last_prices", []) or []:
            figi = str(getattr(price, "figi", "") or "")
            info = figi_map.get(figi, {})
            prices.append(
                {
                    "ticker": info.get("ticker"),
                    "name": info.get("name"),
                    "figi": figi,
                    "price": to_float(getattr(price, "price", None), 6),
                    "currency": info.get("currency"),
                    "time": _iso(getattr(price, "time", None)),
                }
            )
        prices.sort(key=lambda p: p["ticker"] or "")
        return {"prices": prices, "count": len(prices), "errors": errors or None}

    # ----------------------------------------------------------- instruments
    def search_instrument(self, query: str, limit: int = 5) -> dict[str, Any]:
        """Поиск бумаги по тикеру или названию."""
        query = (query or "").strip()
        if not query:
            return {"error": "empty_query", "message": "Пустой поисковый запрос."}
        try:
            response = self._call(
                self._services().instruments.find_instrument,
                query=query,
                operation="find_instrument",
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("find_instrument недоступен (%s), пробуем точный тикер", exc)
            try:
                info = self._instrument_by_ticker(query)
                return {"query": query, "results": [info], "count": 1, "source": "ticker_lookup"}
            except Exception as exc2:  # noqa: BLE001
                return {"error": "not_found", "message": f"Инструмент не найден: {exc2}"}

        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in getattr(response, "instruments", []) or []:
            figi = str(getattr(item, "figi", "") or "")
            if figi in seen:
                continue
            seen.add(figi)
            results.append(
                {
                    "ticker": str(getattr(item, "ticker", "") or ""),
                    "name": str(getattr(item, "name", "") or ""),
                    "figi": figi,
                    "uid": str(getattr(item, "uid", "") or ""),
                    "class_code": str(getattr(item, "class_code", "") or ""),
                    "instrument_type": str(
                        getattr(getattr(item, "instrument_kind", ""), "name", "")
                        or getattr(item, "instrument_type", "")
                        or ""
                    ),
                    "isin": str(getattr(item, "isin", "") or ""),
                    "lot": int(getattr(item, "lot", 0) or 0),
                    "tradable": bool(getattr(item, "api_trade_available_flag", False)),
                }
            )
        # Совпадения по тикеру — выше, торгуемые — выше.
        upper = query.upper()
        results.sort(key=lambda r: (r["ticker"].upper() != upper, not r["tradable"], r["ticker"]))
        return {"query": query, "results": results[:limit], "count": len(results)}

    def get_instrument_info(self, instrument: str, with_price: bool = True) -> dict[str, Any]:
        """Карточка бумаги: тикер, название, лот, валюта, тип, цена."""
        info = self._resolve_instrument(instrument)
        result = dict(info)
        if with_price:
            try:
                price_data = self.get_last_prices([info["figi"] or info["ticker"]])
                if price_data.get("prices"):
                    result["last_price"] = price_data["prices"][0]["price"]
                    result["price_time"] = price_data["prices"][0]["time"]
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("Не удалось получить цену %s: %s", info.get("ticker"), exc)
                result["price_error"] = str(exc)
        return result

    def _resolve_instrument(self, query: str) -> dict[str, Any]:
        """Приводит тикер/название/uid к полной карточке инструмента."""
        raw = (query or "").strip()
        if not raw:
            raise TInvestError("Пустой идентификатор инструмента.")
        key = raw.upper()
        if key in self._instruments:
            return self._instruments[key]

        info: dict[str, Any] | None = None
        if _looks_like_uid(raw):
            info = self._instrument_by_uid(raw)

        if info is None:
            try:
                found = self._call(
                    self._services().instruments.find_instrument,
                    query=raw,
                    operation="find_instrument",
                )
                candidates = list(getattr(found, "instruments", []) or [])
                best = self._pick_best_candidate(candidates, key)
                if best is not None:
                    uid = str(getattr(best, "uid", "") or "")
                    if uid:
                        info = self._instrument_by_uid(uid)
                    if info is None:
                        info = {
                            "ticker": str(getattr(best, "ticker", "") or key),
                            "name": str(getattr(best, "name", "") or ""),
                            "figi": str(getattr(best, "figi", "") or ""),
                            "uid": uid,
                            "asset_uid": "",
                            "class_code": str(getattr(best, "class_code", "") or ""),
                            "isin": str(getattr(best, "isin", "") or ""),
                            "currency": "",
                            "lot": int(getattr(best, "lot", 0) or 0),
                            "instrument_type": str(getattr(getattr(best, "instrument_kind", ""), "name", "")),
                            "exchange": "",
                        }
            except Exception as exc:  # noqa: BLE001
                self.logger.debug("Поиск %s через find_instrument не удался: %s", raw, exc)

        if info is None:
            info = self._instrument_by_ticker(raw)

        self._instruments[key] = info
        self._instruments[info["ticker"].upper()] = info
        if info.get("figi"):
            self._instruments[info["figi"]] = info
        if info.get("uid"):
            self._instruments[info["uid"]] = info
        return info

    @staticmethod
    def _pick_best_candidate(candidates: Iterable[Any], query_upper: str) -> Any | None:
        candidates = list(candidates or [])
        if not candidates:
            return None
        exact = [c for c in candidates if str(getattr(c, "ticker", "")).upper() == query_upper]
        pool = exact or candidates
        tradable = [c for c in pool if getattr(c, "api_trade_available_flag", False)]
        pool = tradable or pool
        return pool[0]

    def _instrument_by_uid(self, uid: str) -> dict[str, Any] | None:
        if not uid:
            return None
        id_type = sdk.enum_member("InstrumentIdType", "INSTRUMENT_ID_TYPE_UID")
        try:
            response = self._call(
                self._services().instruments.get_instrument_by,
                id_type=id_type,
                id=uid,
                operation="get_instrument_by",
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("get_instrument_by(uid=%s) не удался: %s", uid, exc)
            return None
        return self._instrument_to_dict(getattr(response, "instrument", None))

    def _instrument_by_ticker(self, ticker: str) -> dict[str, Any]:
        id_type = sdk.enum_member("InstrumentIdType", "INSTRUMENT_ID_TYPE_TICKER")
        last_error: Exception | None = None
        for class_code in ("TQBR", "TQTF", "TQCB", "TQIR", "SPBXM", "SPBEX", "CETS"):
            try:
                response = self._call(
                    self._services().instruments.get_instrument_by,
                    id_type=id_type,
                    class_code=class_code,
                    id=ticker.upper(),
                    operation="get_instrument_by",
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                continue
            info = self._instrument_to_dict(getattr(response, "instrument", None))
            if info and info.get("ticker"):
                return info
        raise TInvestError(
            f"Не удалось найти инструмент «{ticker}». "
            f"Попробуйте полный тикер (например SBER) или проверьте раскладку. "
            f"({type(last_error).__name__ if last_error else 'нет данных'})"
        )

    @staticmethod
    def _instrument_to_dict(instrument: Any) -> dict[str, Any] | None:
        if instrument is None:
            return None
        ticker = str(getattr(instrument, "ticker", "") or "")
        if not ticker:
            return None
        kind = getattr(instrument, "instrument_type", "")
        if not isinstance(kind, str):
            kind = _enum_name(kind)
        return {
            "ticker": ticker,
            "name": str(getattr(instrument, "name", "") or ""),
            "figi": str(getattr(instrument, "figi", "") or ""),
            "uid": str(getattr(instrument, "uid", "") or ""),
            "asset_uid": str(getattr(instrument, "asset_uid", "") or ""),
            "class_code": str(getattr(instrument, "class_code", "") or ""),
            "isin": str(getattr(instrument, "isin", "") or ""),
            "currency": str(getattr(instrument, "currency", "") or "").upper(),
            "lot": int(getattr(instrument, "lot", 0) or 0),
            "instrument_type": kind.lower().replace("instrument_type_", "") if kind else "",
            "exchange": str(getattr(instrument, "exchange", "") or ""),
        }

    # ----------------------------------------------------------- fundamentals
    def get_fundamentals(self, instruments: Sequence[str]) -> dict[str, Any]:
        """Фундаментальные показатели (GetAssetFundamentals) по списку бумаг."""
        if not instruments:
            return {"error": "empty_request", "message": "Не передан список инструментов."}
        if len(instruments) > 100:
            return {"error": "too_many", "message": "За один запрос можно не более 100 активов."}

        cards: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for query in instruments:
            try:
                cards.append(self._resolve_instrument(query))
            except Exception as exc:  # noqa: BLE001
                errors.append({"query": str(query), "error": str(exc)})

        uid_to_card = {c["asset_uid"]: c for c in cards if c.get("asset_uid")}
        no_asset_uid = [c for c in cards if not c.get("asset_uid")]

        fundamentals: list[dict[str, Any]] = []
        if uid_to_card:
            for item in self._fetch_fundamentals(list(uid_to_card)):
                card = uid_to_card.get(item["asset_uid"])
                if card is None:
                    continue
                fundamentals.append({**self._fundamental_to_dict(item, card)})

        for card in no_asset_uid:
            errors.append(
                {
                    "query": card.get("ticker", ""),
                    "error": "У инструмента нет asset_uid — фундаментальные показатели недоступны "
                    "(обычно так бывает у валют, фьючерсов и некоторых ETF).",
                }
            )

        return {
            "fundamentals": fundamentals,
            "count": len(fundamentals),
            "errors": errors or None,
            "note": (
                "Проценты отдаются дважды: «*» — как в API (коэффициент), «*_pct» — в процентах. "
                "Для мультипликаторов ноль означает «нет данных» и заменён на null."
            ),
        }

    def _fetch_fundamentals(self, asset_uids: Sequence[str]) -> list[dict[str, Any]]:
        services = self._services()
        request_cls = sdk.GetAssetFundamentalsRequest
        items: list[Any] = []
        used_new_api = True
        if request_cls is not None:
            try:
                request = request_cls(assets=list(asset_uids))
            except TypeError:
                used_new_api = False
            else:
                response = self._call(
                    services.instruments.get_asset_fundamentals,
                    request,
                    operation="get_asset_fundamentals",
                )
                items = list(getattr(response, "fundamentals", []) or [])
        if request_cls is None or not used_new_api:
            # legacy-подпись SDK: get_asset_fundamentals(asset_uid=...)
            for uid in asset_uids:
                try:
                    response = self._call(
                        services.instruments.get_asset_fundamentals,
                        asset_uid=uid,
                        operation="get_asset_fundamentals",
                    )
                except TypeError as exc:  # pragma: no cover - совсем старый SDK
                    self.logger.warning("get_asset_fundamentals недоступен: %s", exc)
                    continue
                items.extend(list(getattr(response, "fundamentals", []) or []))

        result: list[dict[str, Any]] = []
        for item in items:
            result.append(
                {
                    "asset_uid": str(getattr(item, "asset_uid", "") or ""),
                    "raw": item,
                }
            )
        return result

    @staticmethod
    def _fundamental_to_dict(item: dict[str, Any], card: dict[str, Any]) -> dict[str, Any]:
        raw = item["raw"]
        values: dict[str, Any] = {}
        for api_field, _label, kind in FUNDAMENTAL_FIELDS:
            value = getattr(raw, api_field, None)
            if kind == "percent":
                values[api_field] = pick_positive(value)
                values[f"{api_field}_pct"] = normalize_percent(value) if value else None
            elif kind == "ratio":
                values[api_field] = pick_positive(value)
            else:
                values[api_field] = pick_positive(value)
        currency = str(getattr(raw, "currency", "") or card.get("currency") or "").upper()
        labels = {api_field: label for api_field, label, _ in FUNDAMENTAL_FIELDS}
        return {
            "ticker": card.get("ticker"),
            "name": card.get("name"),
            "asset_uid": item["asset_uid"],
            "currency": currency,
            **values,
            "fields": labels,
        }

    # --------------------------------------------------------------- watchlist
    def get_watchlist(self, with_prices: bool = True) -> dict[str, Any]:
        """Вотчлист из конфигурации (WATCHLIST) + последние цены."""
        tickers = list(self.watchlist)
        if not tickers:
            return {
                "tickers": [],
                "note": "Вотчлист пуст: задайте WATCHLIST=SBER,GAZP,LKOH в .env.",
            }
        result: dict[str, Any] = {"tickers": tickers, "count": len(tickers)}
        if with_prices:
            try:
                result.update(self.get_last_prices(tickers))
            except Exception as exc:  # noqa: BLE001
                result["prices_error"] = str(exc)
        return result

    # --------------------------------------------------------------- helpers
    def _period(self, days: int) -> tuple[datetime, datetime]:
        days = max(0, min(int(days), 3650))
        date_to = self.clock()
        if date_to.tzinfo is None:  # pragma: no cover
            date_to = date_to.replace(tzinfo=timezone.utc)
        return date_to - timedelta(days=days), date_to

    def stats(self) -> dict[str, Any]:  # pragma: no cover - диагностика
        return {
            "contour": "sandbox" if self.sandbox else "prod",
            "cache": self._cache.stats(),
            "instruments_cached": len(self._instruments),
            "accounts_loaded": len(self._accounts),
        }


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _enum_name(value: Any) -> str:
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name
    text = str(value or "")
    return text


def _looks_like_uid(value: str) -> bool:
    return len(value) == 36 and value.count("-") == 4 and not value.isdigit()


__all__ = [
    "ACCOUNT_TYPE_LABELS",
    "CANDLE_INTERVALS",
    "FUNDAMENTAL_FIELDS",
    "INSTRUMENT_TYPE_LABELS",
    "OPERATION_TYPE_LABELS",
    "TInvestClient",
    "TInvestError",
]

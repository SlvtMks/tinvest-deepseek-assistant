"""Офлайн-ассистент для демо-режима (без ключа DeepSeek).

Это не LLM, а набор детерминированных сценариев поверх тех же инструментов.
Нужен ровно для одного: показать работающий продукт (и прогнать приёмочные
сценарии) там, где нет ключей — например, в публичном демо на Railway.

Интерфейс совпадает с :class:`~tinvest_ai.agent.DeepSeekAgent`, поэтому UI и
CLI работают с ним одинаково.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Sequence
from typing import Any

from .config import Settings
from .formatting import fmt_compact, fmt_money, fmt_number, fmt_pct_abs
from .tools import ToolCallResult, ToolExecutor

LOGGER = logging.getLogger(__name__)

_STOPWORDS = {
    "ПОКАЖИ",
    "КАКАЯ",
    "КАКИЕ",
    "КАКОЙ",
    "БУМАГА",
    "БУМАГИ",
    "ПОРТФЕЛЬ",
    "МЕСЯЦ",
    "МЕСЯЦА",
    "ЗА",
    "ЧТО",
    "ТАМ",
    "СКОЛЬКО",
    "ПОПОЛНИЛ",
    "ВОТЧЛИСТА",
    "ВОТЧЛИСТ",
    "НЕДООЦЕНЕНЫ",
    "ДОХОДНОСТЬ",
    "ЛУЧШУЮ",
    "СЕЙЧАС",
    "МОИ",
    "МОЕГО",
    "ЕСТЬ",
    "МНЕ",
    "ПРО",
}


def _chunk(text: str, size: int = 24) -> list[str]:
    return [text[index : index + size] for index in range(0, len(text), size)] or [""]


class OfflineAgent:
    """Сценарный ассистент без обращения к LLM."""

    def __init__(
        self,
        settings: Settings,
        executor: ToolExecutor,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.settings = settings
        self.executor = executor
        self.logger = logger or LOGGER
        self.model = "offline-demo"
        self._history: list[dict[str, str]] = []

    # ------------------------------------------------------------------ API
    def reset(self) -> None:
        self._history.clear()

    @property
    def history(self) -> list[dict[str, str]]:
        return list(self._history)

    def ask(
        self,
        question: str,
        *,
        stream: bool | None = None,
        on_token: Callable[[str], None] | None = None,
        on_tool_start: Callable[[str, str], None] | None = None,
        on_tool_end: Callable[[ToolCallResult], None] | None = None,
    ) -> OfflineAnswer:
        started = time.perf_counter()
        question = (question or "").strip()
        lower = question.lower()
        records: list[ToolCallResult] = []

        def call(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
            args = arguments or {}
            if on_tool_start is not None:
                on_tool_start(name, str(args))
            result = self.executor.execute(name, args)
            records.append(result)
            if on_tool_end is not None:
                on_tool_end(result)
            payload = result.result if isinstance(result.result, dict) else {}
            return payload

        if not question:
            text = "Задайте вопрос про портфель — например: «покажи портфель»."
        elif any(word in lower for word in ("портфель", "позиции", "актив", "portfolio")):
            text = self._portfolio(call)
        elif any(word in lower for word in ("доходност", "лучш", "прибыльн", "perfomance", "вырос")):
            text = self._performance(call, lower)
        elif any(word in lower for word in ("пополн", "вывод", "внес", "кэшфлоу", "cash", "поток")):
            text = self._cash_flows(call, lower)
        elif any(word in lower for word in ("вотчлист", "watchlist", "недооцен", "p/e", "pe ", "roe")):
            text = self._watchlist(call)
        elif any(word in lower for word in ("счет", "счёт", "account")):
            text = self._accounts(call)
        elif any(word in lower for word in ("свеч", "график", "динамик", "котиров")):
            text = self._candles(call, question)
        elif self._extract_ticker(question):
            text = self._about_ticker(call, question)
        else:
            text = self._help()

        if on_token is not None:
            for piece in _chunk(text):
                on_token(piece)

        self._history.append({"role": "user", "content": question})
        self._history.append({"role": "assistant", "content": text})
        return OfflineAnswer(
            text=text,
            tool_calls=records,
            model=self.model,
            duration_s=time.perf_counter() - started,
        )

    # ------------------------------------------------------------- сценарии
    def _accounts(self, call: Callable[..., dict[str, Any]]) -> str:
        data = call("list_accounts")
        accounts = data.get("accounts", [])
        if not accounts:
            return "Счетов не найдено. Проверьте токен и контур (песочница/прод)."
        lines = ["**Ваши счета**", "", "| Счёт | Название | Тип | Статус |", "|---|---|---|---|"]
        for account in accounts:
            lines.append(
                f"| `{account['account_id']}` | {account['name']} | {account['type']} | {account['status']} |"
            )
        return "\n".join(lines)

    def _portfolio(self, call: Callable[..., dict[str, Any]]) -> str:
        data = call("get_portfolio")
        positions = data.get("positions", [])
        if not positions:
            return "Портфель пуст: позиций нет, только денежные остатки."
        lines = [
            "**Портфель**",
            "",
            "| Тикер | Кол-во | Ср. цена | Текущая | Стоимость | Доля | P/L |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        for position in positions:
            pnl = position.get("expected_yield") or 0
            pnl_pct = position.get("expected_yield_pct")
            lines.append(
                "| **{ticker}** | {qty} | {avg} | {price} | {value} | {share} | {pnl} ({pct}) |".format(
                    ticker=position["ticker"],
                    qty=fmt_number(position["quantity"], 0),
                    avg=fmt_money(position["average_price"], position["currency"]),
                    price=fmt_money(position["current_price"], position["currency"]),
                    value=fmt_money(position["value"], position["currency"]),
                    share=fmt_pct_abs(position["share_in_portfolio_pct"], 1),
                    pnl=fmt_money(pnl, position["currency"]),
                    pct=fmt_pct_abs(pnl_pct, 1) if pnl_pct is not None else "—",
                )
            )
        lines += [
            "",
            f"**Итого по портфелю:** {fmt_money(data['total_value'], 'RUB')} "
            f"(бумаги {fmt_money(data['positions_value'], 'RUB')} + деньги {fmt_money(data['cash_value'], 'RUB')})",
            f"**Текущий результат:** {fmt_money(data['expected_yield'], 'RUB')} "
            f"({fmt_pct_abs(data.get('expected_yield_pct'), 2)})",
        ]
        if data.get("account_id"):
            lines.append(f"\n_Счёт: `{data['account_id']}`_")
        return "\n".join(lines)

    def _performance(self, call: Callable[..., dict[str, Any]], lower: str) -> str:
        days = 30
        if "недел" in lower:
            days = 7
        elif "квартал" in lower or "3 месяц" in lower:
            days = 90
        elif "год" in lower:
            days = 365
        data = call("get_performance", {"days": days})
        items = data.get("items", [])
        if not items:
            return "Не удалось рассчитать доходность: нет дневных свечей по бумагам портфеля."
        lines = [
            f"**Доходность бумаг за {days} дн.** (ценовая, без дивидендов)",
            "",
            "| Тикер | Цена на начало | Цена сейчас | Изменение | Вклад, ₽ | Вес |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for item in items:
            lines.append(
                "| **{ticker}** | {start} | {end} | {change} | {pnl} | {weight} |".format(
                    ticker=item["ticker"],
                    start=fmt_money(item["price_start"], item.get("currency") or "RUB"),
                    end=fmt_money(item["price_end"], item.get("currency") or "RUB"),
                    change=fmt_pct_abs(item["return_pct"], 2),
                    pnl=fmt_money(item["pnl"], item.get("currency") or "RUB"),
                    weight=fmt_pct_abs(item.get("portfolio_weight_pct"), 1),
                )
            )
        best, worst = data.get("best"), data.get("worst")
        lines.append("")
        if best:
            lines.append(
                f"**Лучший результат:** {best['ticker']} — {fmt_pct_abs(best['return_pct'], 2)} "
                f"({fmt_money(best['pnl'], 'RUB')} при текущей позиции)"
            )
        if worst and worst is not best:
            lines.append(f"**Худший результат:** {worst['ticker']} — {fmt_pct_abs(worst['return_pct'], 2)}")
        if data.get("portfolio_weighted_return_pct") is not None:
            lines.append(
                f"**Взвешенная доходность портфеля:** {fmt_pct_abs(data['portfolio_weighted_return_pct'], 2)}"
            )
        lines.append("\n_Это ценовая динамика без дивидендов и купонов; не ИИР._")
        return "\n".join(lines)

    def _cash_flows(self, call: Callable[..., dict[str, Any]], lower: str) -> str:
        days = 30
        if "недел" in lower:
            days = 7
        elif "год" in lower:
            days = 365
        elif "квартал" in lower:
            days = 90
        data = call("get_cash_flows", {"days": days})
        totals = data.get("totals", {})
        if not totals:
            return f"За последние {days} дн. операций не найдено."
        currency = data.get("primary_currency", "RUB")
        values = totals.get(currency, {})
        lines = [
            f"**Денежные потоки за {days} дн.** (операций: {data.get('operations_count', 0)})",
            "",
            f"- Пополнения: **{fmt_money(values.get('deposits', 0), currency)}**",
            f"- Выводы: **{fmt_money(values.get('withdrawals', 0), currency)}**",
            f"- Чистое пополнение (пополнения − выводы): **{fmt_money(values.get('net_deposits', 0), currency)}**",
        ]
        if values.get("dividends"):
            lines.append(f"- Дивиденды: {fmt_money(values['dividends'], currency)}")
        if values.get("coupons"):
            lines.append(f"- Купоны: {fmt_money(values['coupons'], currency)}")
        if values.get("taxes"):
            lines.append(f"- Налоги: {fmt_money(values['taxes'], currency)}")
        if values.get("fees"):
            lines.append(f"- Комиссии: {fmt_money(values['fees'], currency)}")
        if len(totals) > 1:
            others = ", ".join(code for code in totals if code != currency)
            lines.append(f"\n_Также есть операции в валютах: {others} — уточните, если нужна разбивка._")
        return "\n".join(lines)

    def _watchlist(self, call: Callable[..., dict[str, Any]]) -> str:
        watchlist = call("get_watchlist", {"with_prices": False})
        tickers: Sequence[str] = watchlist.get("tickers") or []
        if not tickers:
            return "Вотчлист пуст. Добавьте тикеры в переменную WATCHLIST в .env, например `WATCHLIST=SBER,GAZP,LKOH`."
        data = call("get_fundamentals", {"instruments": list(tickers)})
        fundamentals = data.get("fundamentals", [])
        if not fundamentals:
            return "Не удалось получить фундаментальные показатели по вотчлисту."
        rows = sorted(
            fundamentals,
            key=lambda item: (item.get("pe_ratio_ttm") is None, item.get("pe_ratio_ttm") or 0),
        )
        lines = [
            "**Вотчлист: мультипликаторы**",
            "",
            "| Тикер | P/E | P/B | ROE | Див. доходность | Капитализация |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for item in rows:
            div_yield = item.get("dividend_yield_daily_ttm_pct")
            lines.append(
                "| **{ticker}** | {pe} | {pb} | {roe} | {div} | {cap} |".format(
                    ticker=item["ticker"],
                    pe=fmt_number(item.get("pe_ratio_ttm"), 1),
                    pb=fmt_number(item.get("price_to_book_ttm"), 2),
                    roe=fmt_pct_abs(item.get("roe_pct"), 1),
                    div=fmt_pct_abs(div_yield, 1) if div_yield else "—",
                    cap=fmt_compact(item.get("market_capitalization"), item.get("currency") or "RUB"),
                )
            )

        candidates = [
            item
            for item in fundamentals
            if (item.get("pe_ratio_ttm") or 0) > 0
            and (item.get("roe_pct") or 0) > 0
            and (item.get("roe_pct") or 0) >= 12
        ]
        candidates.sort(key=lambda item: item.get("pe_ratio_ttm") or 0)
        lines.append("")
        if candidates:
            top = candidates[:3]
            names = ", ".join(
                f"{item['ticker']} (P/E {fmt_number(item['pe_ratio_ttm'], 1)}, ROE {fmt_pct_abs(item['roe_pct'], 1)})"
                for item in top
            )
            lines.append("**Потенциально недооценённые** (низкий P/E при ROE выше 12 %): " + names)
        negative = [item["ticker"] for item in fundamentals if (item.get("pe_ratio_ttm") or 0) <= 0]
        if negative:
            lines.append(
                "\n_Без осмысленного P/E (убыток или нет данных): "
                + ", ".join(negative)
                + " — их нельзя сравнивать по этому мультипликатору._"
            )
        lines.append(
            "\n_Логика: низкий P/E при высоком ROE — признак недооценки, но это лишь скрининг, не ИИР._"
        )
        return "\n".join(lines)

    def _candles(self, call: Callable[..., dict[str, Any]], question: str) -> str:
        ticker = self._extract_ticker(question) or "SBER"
        data = call("get_candles", {"instrument": ticker, "days": 30})
        if data.get("error"):
            return f"Не удалось получить свечи: {data.get('message')}"
        summary = data.get("summary", {})
        return (
            f"**{data['ticker']} · {data.get('name') or ''}**\n\n"
            f"- Первая цена периода: {fmt_money(summary.get('first_open'), data.get('currency') or 'RUB')}\n"
            f"- Последняя цена: {fmt_money(summary.get('last_close'), data.get('currency') or 'RUB')}\n"
            f"- Изменение: {fmt_pct_abs(summary.get('change_pct'), 2)}\n"
            f"- Диапазон закрытий: {fmt_number(summary.get('min_close'), 2)} — {fmt_number(summary.get('max_close'), 2)}\n"
            f"- Свечей в выборке: {summary.get('candles_total')}"
        )

    def _about_ticker(self, call: Callable[..., dict[str, Any]], question: str) -> str:
        ticker = self._extract_ticker(question) or question
        info = call("get_instrument_info", {"instrument": ticker})
        if info.get("error"):
            return (
                f"Не нашёл бумагу «{ticker}». Уточните тикер — в демо-режиме доступны: "
                "SBER, GAZP, LKOH, YDEX, OZON, TMOS, GMKN, MTSS."
            )
        ticker = info.get("ticker", ticker)
        fundamentals = call("get_fundamentals", {"instruments": [ticker]}).get("fundamentals") or []
        candles = call("get_candles", {"instrument": ticker, "days": 30})
        lines = [
            f"**{ticker} — {info.get('name', '')}**",
            "",
            f"- Последняя цена: **{fmt_money(info.get('last_price'), info.get('currency') or 'RUB')}**",
            f"- Изменение за 30 дн.: {fmt_pct_abs((candles.get('summary') or {}).get('change_pct'), 2)}",
            f"- Лот: {info.get('lot')}, биржа: {info.get('exchange') or 'MOEX'}, "
            f"сектор: {info.get('sector') or '—'}",
        ]
        if fundamentals:
            item = fundamentals[0]
            lines += [
                "",
                "| Показатель | Значение |",
                "|---|---:|",
                f"| P/E (TTM) | {fmt_number(item.get('pe_ratio_ttm'), 1)} |",
                f"| P/B (TTM) | {fmt_number(item.get('price_to_book_ttm'), 2)} |",
                f"| ROE | {fmt_pct_abs(item.get('roe_pct'), 1)} |",
                f"| EPS (TTM) | {fmt_number(item.get('eps_ttm'), 2)} |",
                f"| Дивидендная доходность | {fmt_pct_abs(item.get('dividend_yield_daily_ttm_pct'), 1)} |",
                f"| Капитализация | {fmt_compact(item.get('market_capitalization'), item.get('currency') or 'RUB')} |",
                f"| Долг / Капитал | {fmt_number(item.get('total_debt_to_equity_mrq'), 2)} |",
            ]
        lines.append("\n_Фундаментальные данные — T-Invest GetAssetFundamentals; не ИИР._")
        return "\n".join(lines)

    def _help(self) -> str:
        return (
            "Я демо-ассистент по портфелю Т-Инвестиций (работаю без ключа DeepSeek, "
            "по сценариям). Попробуйте:\n\n"
            "1. «Покажи портфель»\n"
            "2. «Какая бумага дала лучшую доходность за месяц»\n"
            "3. «Что там по SBER»\n"
            "4. «Сколько я пополнил за месяц»\n"
            "5. «Какие бумаги из моего вотчлиста недооценены по P/E и ROE»\n\n"
            "С ключом `DEEPSEEK_API_KEY` те же вопросы обрабатывает DeepSeek через "
            "Function Calling — свободным текстом и с любыми уточнениями."
        )

    @staticmethod
    def _extract_ticker(question: str) -> str | None:
        for token in re.findall(r"[A-Za-z][A-Za-z0-9\-\.]{1,9}", question):
            if token.upper() not in _STOPWORDS and len(token) >= 3:
                return token.upper()
        return None


class OfflineAnswer:
    """Ответ офлайн-ассистента (совместим по полям с ``Answer``)."""

    def __init__(
        self,
        text: str,
        tool_calls: list[ToolCallResult],
        model: str,
        duration_s: float,
        usage: dict[str, Any] | None = None,
    ) -> None:
        self.text = text
        self.tool_calls = tool_calls
        self.model = model
        self.duration_s = duration_s
        self.usage = usage or {}
        self.rounds = 1

    @property
    def tools_used(self) -> list[str]:
        return [record.name for record in self.tool_calls]


__all__ = ["OfflineAgent", "OfflineAnswer"]

"""Инструменты (Function Calling) для DeepSeek.

Здесь два слоя:

* ``TOOLS`` — JSON-схемы в формате OpenAI/DeepSeek ``tools``. Их читает модель,
  чтобы понять, что она может запросить у T-Invest API;
* :class:`ToolExecutor` — исполняет выбранный моделью инструмент, валидирует
  аргументы и превращает любое исключение в аккуратный JSON с ``error``,
  чтобы модель могла объяснить проблему пользователю, а не падать.

Инструменты только читают данные — торговых операций здесь нет.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

LOGGER = logging.getLogger(__name__)


class ToolArgumentError(ValueError):
    """Некорректные аргументы вызова инструмента."""


def _schema(
    name: str, description: str, properties: dict[str, Any], required: list[str] | None = None
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
                "additionalProperties": False,
            },
        },
    }


_ACCOUNT_ID = {
    "type": "string",
    "description": "Идентификатор счёта из list_accounts. Если не указан — берётся счёт по умолчанию.",
}

_INSTRUMENTS = {
    "type": "array",
    "items": {"type": "string"},
    "description": "Список тикеров (SBER, GAZP), FIGI или instrument_uid. До 20 штук.",
    "minItems": 1,
    "maxItems": 20,
}

TOOLS: list[dict[str, Any]] = [
    _schema(
        "list_accounts",
        "Список счетов пользователя в T-Invest (id, название, тип, статус, дата открытия). "
        "Вызывай первым, если неизвестен account_id.",
        {},
    ),
    _schema(
        "get_portfolio",
        "Текущий портфель: позиции (тикер, количество, средняя и текущая цена, стоимость, "
        "доля в портфеле, текущая прибыль/убыток в деньгах и процентах), денежные остатки "
        "и общая стоимость портфеля. Используй для вопроса «покажи портфель».",
        {
            "account_id": _ACCOUNT_ID,
            "include_zero_positions": {
                "type": "boolean",
                "description": "Показывать позиции с нулевым количеством (по умолчанию false).",
            },
        },
    ),
    _schema(
        "get_operations",
        "История операций по счёту за период: пополнения, выводы, покупки, продажи, "
        "дивиденды, купоны, налоги, комиссии. Для подсчёта сумм используй get_cash_flows.",
        {
            "account_id": _ACCOUNT_ID,
            "days": {
                "type": "integer",
                "description": "Глубина истории в днях (1–3650). 30 = последний месяц.",
                "minimum": 1,
                "maximum": 3650,
                "default": 30,
            },
            "operation_types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Фильтр по типам API, например ['OPERATION_TYPE_INPUT'] — только пополнения, "
                "['OPERATION_TYPE_DIVIDEND','OPERATION_TYPE_COUPON'] — дивиденды и купоны.",
            },
            "limit": {
                "type": "integer",
                "description": "Сколько последних операций вернуть (по умолчанию 100).",
                "default": 100,
            },
        },
    ),
    _schema(
        "get_cash_flows",
        "Агрегированные денежные потоки за период: сумма пополнений, выводов, дивидендов, "
        "купонов, налогов и комиссий (по валютам). Используй для вопроса «сколько я пополнил за месяц».",
        {
            "account_id": _ACCOUNT_ID,
            "days": {
                "type": "integer",
                "description": "Период в днях (30 = последний месяц).",
                "minimum": 1,
                "maximum": 3650,
                "default": 30,
            },
        },
    ),
    _schema(
        "get_performance",
        "Доходность каждой бумаги портфеля за период по дневным свечам: цена на начало и конец "
        "периода, изменение в процентах, вклад в рублях, вес в портфеле, лучшая и худшая бумага. "
        "Используй для вопроса «какая бумага дала лучшую доходность за месяц».",
        {
            "account_id": _ACCOUNT_ID,
            "days": {
                "type": "integer",
                "description": "Период в днях: 7 = неделя, 30 = месяц, 90 = квартал, 365 = год.",
                "minimum": 1,
                "maximum": 3650,
                "default": 30,
            },
        },
    ),
    _schema(
        "search_instrument",
        "Поиск бумаги по тикеру или названию компании (например 'SBER', 'Сбербанк', 'Газпром'). "
        "Возвращает ticker, name, figi, uid, class_code, тип, лот, ISIN.",
        {
            "query": {"type": "string", "description": "Тикер или название бумаги."},
            "limit": {
                "type": "integer",
                "description": "Максимум результатов (по умолчанию 5).",
                "default": 5,
            },
        },
        ["query"],
    ),
    _schema(
        "get_instrument_info",
        "Карточка бумаги: полное название, тикер, лот, валюта, тип, биржа, ISIN, asset_uid и последняя цена. "
        "Используй для вопроса «что там по SBER» вместе с get_fundamentals и get_candles.",
        {
            "instrument": {"type": "string", "description": "Тикер, FIGI или uid бумаги."},
            "with_price": {
                "type": "boolean",
                "description": "Добавить последнюю цену (по умолчанию true).",
                "default": True,
            },
        },
        ["instrument"],
    ),
    _schema(
        "get_fundamentals",
        "Фундаментальные показатели бумаг через GetAssetFundamentals: P/E, P/B, P/S, EV/EBITDA, ROE, ROA, "
        "ROIC, EPS, чистая маржа, выручка, чистая прибыль, EBITDA, капитализация, дивидендная доходность "
        "и payout ratio, долг/капитал, бета, максимум/минимум за 52 недели, темпы роста. "
        "Используй для сравнения бумаг по мультипликаторам и для поиска недооценённых бумаг.",
        {"instruments": _INSTRUMENTS},
        ["instruments"],
    ),
    _schema(
        "get_candles",
        "Свечи по бумаге за период + сводка (первая/последняя цена, изменение в %, минимум, максимум). "
        "Для анализа динамики цены и расчёта доходности за произвольный период.",
        {
            "instrument": {"type": "string", "description": "Тикер, FIGI или uid."},
            "days": {
                "type": "integer",
                "description": "Период в днях (по умолчанию 30).",
                "minimum": 1,
                "maximum": 3650,
                "default": 30,
            },
            "interval": {
                "type": "string",
                "enum": ["1min", "5min", "hour", "day", "week", "month"],
                "description": "Интервал свечей. По умолчанию day. Учти лимиты API: 1min — 1 день, hour — неделя, day — год.",
                "default": "day",
            },
        },
        ["instrument"],
    ),
    _schema(
        "get_last_prices",
        "Последние цены по списку бумаг одним запросом.",
        {"instruments": _INSTRUMENTS},
        ["instruments"],
    ),
    _schema(
        "get_watchlist",
        "Вотчлист пользователя из настроек (переменная WATCHLIST) и последние цены по этим бумагам. "
        "Для вопроса «какие бумаги из моего вотчлиста недооценены» вызови это, затем get_fundamentals "
        "по полученным тикерам и сам интерпретируй P/E и ROE.",
        {
            "with_prices": {
                "type": "boolean",
                "description": "Добавить последние цены (по умолчанию true).",
                "default": True,
            }
        },
    ),
]

TOOLS_BY_NAME: dict[str, dict[str, Any]] = {tool["function"]["name"]: tool for tool in TOOLS}

# Ожидаемые типы аргументов — для мягкой нормализации того, что прислала модель.
_ARG_TYPES: dict[str, dict[str, type]] = {
    "get_portfolio": {"account_id": str, "include_zero_positions": bool},
    "get_operations": {"account_id": str, "days": int, "operation_types": list, "limit": int},
    "get_cash_flows": {"account_id": str, "days": int},
    "get_performance": {"account_id": str, "days": int},
    "search_instrument": {"query": str, "limit": int},
    "get_instrument_info": {"instrument": str, "with_price": bool},
    "get_fundamentals": {"instruments": list},
    "get_candles": {"instrument": str, "days": int, "interval": str},
    "get_last_prices": {"instruments": list},
    "get_watchlist": {"with_prices": bool},
}


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on", "да"}
    return bool(value)


def _coerce_int(value: Any) -> int:
    if isinstance(value, bool):
        raise ToolArgumentError("ожидалось целое число, получено булево")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(float(value.strip()))
        except ValueError as exc:
            raise ToolArgumentError(f"ожидалось целое число, получено {value!r}") from exc
    raise ToolArgumentError(f"ожидалось целое число, получено {value!r}")


def _coerce_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [chunk.strip() for chunk in value.split(",") if chunk.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    raise ToolArgumentError(f"ожидался список строк, получено {value!r}")


def normalize_arguments(name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Приводит аргументы модели к ожидаемым типам и отбрасывает лишние ключи."""
    expected = _ARG_TYPES.get(name, {})
    normalized: dict[str, Any] = {}
    for key, value in (arguments or {}).items():
        if key not in expected or value is None:
            continue
        target = expected[key]
        if target is bool:
            normalized[key] = _coerce_bool(value)
        elif target is int:
            normalized[key] = _coerce_int(value)
        elif target is list:
            normalized[key] = _coerce_list(value)
        else:
            normalized[key] = str(value).strip()
    if "account_id" in normalized and not normalized["account_id"]:
        normalized.pop("account_id")
    return normalized


@dataclass
class ToolCallResult:
    """Результат одного вызова инструмента."""

    name: str
    arguments: dict[str, Any]
    result: Any
    duration_s: float
    ok: bool = True
    error: str | None = None

    def to_payload(self) -> str:
        """JSON-строка, которая уходит модели в сообщении role=tool."""
        return json.dumps(self.result, ensure_ascii=False, default=str)


# Имя инструмента модели -> метод клиента с той же сигнатурой.
_HANDLERS: dict[str, str] = {
    name: name
    for name in (
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
    )
}


@dataclass
class ToolExecutor:
    """Исполняет вызовы инструментов поверх клиента T-Invest."""

    client: Any
    logger: logging.Logger = field(default=LOGGER, repr=False)
    history: list[ToolCallResult] = field(default_factory=list, repr=False)

    def available_tools(self) -> list[str]:
        return sorted(_HANDLERS)

    def execute(self, name: str, arguments: Mapping[str, Any] | None = None) -> ToolCallResult:
        started = time.perf_counter()
        arguments = dict(arguments or {})
        handler_name = _HANDLERS.get(name)
        if handler_name is None:
            error = f"Неизвестный инструмент «{name}». Доступны: {', '.join(self.available_tools())}"
            self.logger.error(error)
            return ToolCallResult(
                name=name,
                arguments=arguments,
                result={"error": "unknown_tool", "message": error},
                duration_s=time.perf_counter() - started,
                ok=False,
                error=error,
            )

        try:
            normalized = normalize_arguments(name, arguments)
            handler: Callable[..., Any] = getattr(self.client, handler_name)
            result = handler(**normalized)
            ok = True
            error = None
        except ToolArgumentError as exc:
            error = f"Некорректные аргументы {name}: {exc}"
            result = {"error": "invalid_arguments", "message": str(exc)}
            ok = False
        except Exception as exc:  # noqa: BLE001 - модель должна увидеть причину, а не traceback
            error = f"{type(exc).__name__}: {exc}"
            result = {
                "error": "tool_failed",
                "message": str(exc) or type(exc).__name__,
                "hint": "Проверь корректность тикера/счёта или попробуй другой инструмент.",
            }
            ok = False
            self.logger.exception("Инструмент %s завершился ошибкой", name)
        duration = time.perf_counter() - started
        if ok:
            self.logger.info("tool=%s args=%s ok за %.2f c", name, _safe_args(normalized), duration)
        record = ToolCallResult(
            name=name,
            arguments=arguments,
            result=result,
            duration_s=duration,
            ok=ok,
            error=error,
        )
        self.history.append(record)
        return record

    def execute_json(self, name: str, raw_arguments: str | None) -> ToolCallResult:
        """Разбирает JSON-аргументы из ``tool_calls`` и выполняет инструмент."""
        try:
            arguments = json.loads(raw_arguments) if raw_arguments and raw_arguments.strip() else {}
        except json.JSONDecodeError as exc:
            error = f"Не удалось разобрать JSON-аргументы: {exc}"
            self.logger.error("%s (%s)", error, raw_arguments)
            return ToolCallResult(
                name=name,
                arguments={},
                result={"error": "invalid_json", "message": error},
                duration_s=0.0,
                ok=False,
                error=error,
            )
        if not isinstance(arguments, dict):
            return ToolCallResult(
                name=name,
                arguments={},
                result={"error": "invalid_json", "message": "Ожидался JSON-объект."},
                duration_s=0.0,
                ok=False,
                error="Ожидался JSON-объект.",
            )
        return self.execute(name, arguments)

    def clear_history(self) -> None:
        self.history.clear()


def _safe_args(arguments: Mapping[str, Any], limit: int = 200) -> str:
    text = json.dumps(dict(arguments), ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "…"


def summarize_result(result: ToolCallResult) -> str:
    """Короткая строка для UI: «ok · 5 позиций · 0.42 c»."""
    if not result.ok:
        return f"ошибка · {result.error or 'неизвестно'}"
    payload = result.result if isinstance(result.result, dict) else {}
    parts: list[str] = []
    if isinstance(payload.get("count"), int):
        parts.append(f"записей: {payload['count']}")
    if isinstance(payload.get("positions_count"), int):
        parts.append(f"позиций: {payload['positions_count']}")
    for key in ("prices", "fundamentals", "items", "operations", "accounts", "results", "tickers"):
        value = payload.get(key)
        if isinstance(value, list):
            parts.append(f"{key}: {len(value)}")
            break
    if payload.get("error"):
        parts.append(str(payload["error"]))
    summary = ", ".join(parts) if parts else "ok"
    return f"{summary} · {result.duration_s:.2f} c"


__all__ = [
    "TOOLS",
    "TOOLS_BY_NAME",
    "ToolArgumentError",
    "ToolCallResult",
    "ToolExecutor",
    "normalize_arguments",
    "summarize_result",
]

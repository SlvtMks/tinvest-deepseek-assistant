"""Приведение типов SDK к обычным числам и человекочитаемое форматирование.

T-Invest API отдаёт деньги и количества как ``Quotation``/``MoneyValue``
(units + nano), а фундаментальные показатели — как float. Модели нужен
плоский JSON с числами, поэтому всё нормализуется здесь.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

# Валюты, в которых торгуются бумаги на Мосбирже + служебные.
CURRENCY_SIGNS = {
    "rub": "₽",
    "usd": "$",
    "eur": "€",
    "gbp": "£",
    "hkd": "HK$",
    "cny": "¥",
    "chf": "₣",
    "try": "₺",
    "kzt": "₸",
    "byr": "Br",
    "amd": "֏",
    "kgs": "с",
    "uzs": "so'm",
    "tjs": "смн",
    "azn": "₼",
    "mnt": "₮",
    "gel": "₾",
    "srg": "ƒ",
    "ply": "zł",
    "ils": "₪",
    "jpy": "¥",
    "inr": "₹",
    "krw": "₩",
    "vnd": "₫",
    "byn": "Br",
    "uah": "₴",
    "rsd": "дин",
    "bgn": "лв",
    "ron": "lei",
    "czk": "Kč",
    "huf": "Ft",
    "sek": "kr",
    "nok": "kr",
    "dkk": "kr",
    "aud": "A$",
    "cad": "C$",
    "nzd": "NZ$",
    "sgd": "S$",
    "zar": "R",
    "brl": "R$",
    "mxn": "MX$",
    "ars": "AR$",
    "clp": "CLP$",
    "pen": "S/",
    "cop": "COL$",
    "idr": "Rp",
    "myr": "RM",
    "php": "₱",
    "thb": "฿",
    "aed": "AED",
    "sar": "SAR",
    "egp": "E£",
    "ngn": "₦",
    "kes": "KSh",
    "twd": "NT$",
}


def to_decimal(value: Any) -> Decimal | None:
    """Универсально приводит значение к ``Decimal``.

    Понимает ``Quotation``/``MoneyValue`` (units/nano), int, float, str,
    Decimal и вложенные словари вида ``{"units": 1, "nano": 500000000}``.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return Decimal(int(value))
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):  # NaN/inf
            return None
        return Decimal(str(value))
    if isinstance(value, str):
        text = value.strip().replace(",", ".")
        if not text:
            return None
        try:
            return Decimal(text)
        except InvalidOperation:
            return None
    if isinstance(value, dict):
        if "units" in value or "nano" in value:
            return _from_units_nano(value.get("units", 0), value.get("nano", 0))
        return None
    units = getattr(value, "units", None)
    nano = getattr(value, "nano", None)
    if units is not None or nano is not None:
        return _from_units_nano(units or 0, nano or 0)
    # Денежные суммы вида {"currency": ..., "units": ..., "nano": ...} уже покрыты выше.
    return None


def _from_units_nano(units: Any, nano: Any) -> Decimal:
    try:
        return Decimal(int(units)) + Decimal(int(nano)) / Decimal(10**9)
    except (TypeError, ValueError, InvalidOperation):  # pragma: no cover
        return Decimal(0)


def to_float(value: Any, ndigits: int | None = None) -> float | None:
    dec = to_decimal(value)
    if dec is None:
        return None
    result = float(dec)
    return round(result, ndigits) if ndigits is not None else result


def to_quantity(value: Any, ndigits: int = 6) -> float | None:
    """Количество бумаг: у дробных активов бывает до 6 знаков."""
    return to_float(value, ndigits)


def money_currency(value: Any) -> str | None:
    """Достаёт код валюты из ``MoneyValue``/словаря (в нижнем регистре)."""
    if value is None:
        return None
    currency = getattr(value, "currency", None)
    if currency is None and isinstance(value, dict):
        currency = value.get("currency")
    if not currency:
        return None
    return str(currency).strip().lower()


def percent(part: Any, whole: Any) -> float | None:
    """Доля в процентах, безопасная к нулевому/пустому знаменателю."""
    p = to_decimal(part)
    w = to_decimal(whole)
    if p is None or w is None or w == 0:
        return None
    return round(float(p / w * 100), 4)


def normalize_percent(value: Any) -> float | None:
    """Приводит "процентный" показатель к процентам.

    T-Invest API отдаёт рентабельности/доходности как коэффициент (0.24 = 24%),
    но в отдельных полях и версиях SDK встречаются уже умноженные значения.
    Эвристика: |value| <= 1.5 трактуем как коэффициент.
    """
    dec = to_decimal(value)
    if dec is None:
        return None
    if dec == 0:
        return 0.0
    if abs(dec) <= Decimal("1.5"):
        return round(float(dec * 100), 4)
    return round(float(dec), 4)


def pick_positive(value: Any) -> float | None:
    """0 в фундаментале означает "нет данных" — отдаём None, чтобы модель не врала."""
    dec = to_decimal(value)
    if dec is None or dec == 0:
        return None
    return round(float(dec), 6)


def currency_sign(currency: str | None) -> str:
    if not currency:
        return ""
    return CURRENCY_SIGNS.get(currency.strip().lower(), currency.upper())


def fmt_money(value: Any, currency: str | None = None, digits: int = 2) -> str:
    """``1234.5`` -> ``1 234,50 ₽``."""
    dec = to_decimal(value)
    if dec is None:
        return "—"
    quant = Decimal(1).scaleb(-digits)
    dec = dec.quantize(quant)
    integer_part = f"{int(dec):,}".replace(",", " ")
    if digits > 0:
        frac = f"{dec:.{digits}f}".split(".")[1]
        text = f"{integer_part},{frac}"
    else:
        text = integer_part
    sign = currency_sign(currency)
    return f"{text} {sign}".strip()


def fmt_number(value: Any, digits: int = 2) -> str:
    dec = to_decimal(value)
    if dec is None:
        return "—"
    text = f"{float(dec):,.{digits}f}".replace(",", " ")
    return text.replace(".", ",")


def fmt_pct(value: Any, digits: int = 2) -> str:
    dec = to_decimal(value)
    if dec is None:
        return "—"
    return f"{float(dec):+.{digits}f} %".replace(".", ",")


def fmt_pct_abs(value: Any, digits: int = 2) -> str:
    dec = to_decimal(value)
    if dec is None:
        return "—"
    return f"{float(dec):.{digits}f} %".replace(".", ",")


def fmt_compact(value: Any, currency: str | None = None) -> str:
    """Компактный формат больших чисел: 1,23 трлн / 45,6 млрд / 789 млн."""
    dec = to_decimal(value)
    if dec is None:
        return "—"
    sign = currency_sign(currency)
    abs_value = abs(dec)
    for threshold, suffix in (
        (Decimal(10**12), "трлн"),
        (Decimal(10**9), "млрд"),
        (Decimal(10**6), "млн"),
        (Decimal(10**3), "тыс."),
    ):
        if abs_value >= threshold:
            scaled = dec / threshold
            text = f"{float(scaled):,.2f}".replace(",", " ").replace(".", ",")
            return f"{text} {suffix}{(' ' + sign) if sign else ''}"
    text = f"{float(dec):,.2f}".replace(",", " ").replace(".", ",")
    return f"{text}{(' ' + sign) if sign else ''}"


__all__ = [
    "CURRENCY_SIGNS",
    "currency_sign",
    "fmt_compact",
    "fmt_money",
    "fmt_number",
    "fmt_pct",
    "fmt_pct_abs",
    "money_currency",
    "normalize_percent",
    "percent",
    "pick_positive",
    "to_decimal",
    "to_float",
    "to_quantity",
]

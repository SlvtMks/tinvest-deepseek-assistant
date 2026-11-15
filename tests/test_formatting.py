"""Нормализация значений SDK и форматирование."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from tinvest_ai.formatting import (
    fmt_compact,
    fmt_money,
    fmt_number,
    fmt_pct,
    money_currency,
    normalize_percent,
    percent,
    pick_positive,
    to_decimal,
    to_float,
    to_quantity,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        (0, Decimal(0)),
        (5, Decimal(5)),
        (1.5, Decimal("1.5")),
        ("12.34", Decimal("12.34")),
        ("12,34", Decimal("12.34")),
        ("", None),
        ("abc", None),
        (Decimal("7.1"), Decimal("7.1")),
        ({"units": 3, "nano": 500_000_000}, Decimal("3.5")),
        (SimpleNamespace(units=2, nano=250_000_000), Decimal("2.25")),
        (SimpleNamespace(units=0, nano=0, currency="rub"), Decimal(0)),
    ],
)
def test_to_decimal(value, expected) -> None:
    assert to_decimal(value) == expected


def test_to_decimal_rejects_nan() -> None:
    assert to_decimal(float("nan")) is None
    assert to_decimal(float("inf")) is None


def test_to_float_and_quantity() -> None:
    assert to_float(SimpleNamespace(units=10, nano=500_000_000), 2) == 10.5
    assert to_quantity(SimpleNamespace(units=0, nano=500_000_000)) == 0.5


def test_money_currency() -> None:
    assert money_currency(SimpleNamespace(currency="rub")) == "rub"
    assert money_currency({"currency": "USD"}) == "usd"
    assert money_currency(None) is None


@pytest.mark.parametrize(
    "part,whole,expected",
    [(50, 200, 25.0), (1, 3, 33.3333), (5, 0, None), (None, 10, None)],
)
def test_percent(part, whole, expected) -> None:
    assert percent(part, whole) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (0.241, 24.1),
        (0.0, 0.0),
        (24.0, 24.0),
        (None, None),
        (-0.21, -21.0),
    ],
)
def test_normalize_percent(value, expected) -> None:
    assert normalize_percent(value) == expected


def test_pick_positive_treats_zero_as_missing() -> None:
    assert pick_positive(0) is None
    assert pick_positive(0.0) is None
    assert pick_positive(4.1) == 4.1
    assert pick_positive(-58.0) == -58.0


def test_fmt_money() -> None:
    assert fmt_money(1234567.891, "rub") == "1 234 567,89 ₽"
    assert fmt_money(1234.5, "usd", digits=1) == "1 234,5 $"
    assert fmt_money(None) == "—"
    assert fmt_money(0, "rub") == "0,00 ₽"


def test_fmt_number_and_pct() -> None:
    assert fmt_number(1234.567, 2) == "1 234,57"
    assert fmt_pct(4.321, 2) == "+4,32 %"
    assert fmt_pct(-4.321, 1) == "-4,3 %"


def test_fmt_compact() -> None:
    assert fmt_compact(5_760_000_000_000, "rub") == "5,76 трлн ₽"
    assert fmt_compact(537_000_000_000, "rub") == "537,00 млрд ₽"
    assert fmt_compact(1_500_000) == "1,50 млн"
    assert fmt_compact(None) == "—"

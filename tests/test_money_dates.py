from datetime import date

import pytest

from bot.services.dates import parse_offsets, parse_past_date, relative_days
from bot.services.money import amount_example, format_amount, parse_amount, split_price

TODAY = date(2026, 10, 16)


@pytest.mark.parametrize(
    ("text", "currency", "expected"),
    [
        ("22.24", "USD", 2224),
        ("22,24", "USD", 2224),
        ("22", "USD", 2200),
        ("1 234,5", "RUB", 123450),
        ("990", "JPY", 990),
    ],
)
def test_parse_amount_valid(text, currency, expected):
    assert parse_amount(text, currency) == expected


@pytest.mark.parametrize(
    ("text", "currency"),
    [("0", "USD"), ("-5", "USD"), ("22.245", "USD"), ("9.5", "JPY"), ("abc", "USD"), ("", "USD")],
)
def test_parse_amount_invalid(text, currency):
    assert parse_amount(text, currency) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("22.24", ("22.24", None)),
        ("22.24 usd", ("22.24", "USD")),
        ("$22.24", ("22.24", "USD")),
        ("1 990 ₽", ("1 990", "RUB")),
        ("22.24 dollars", None),
        ("$22 €", None),
    ],
)
def test_split_price(text, expected):
    assert split_price(text) == expected


def test_amount_example_matches_currency():
    assert amount_example("RUB") == "2000"
    assert amount_example("USD") == "9.99"
    assert amount_example("VND") == "1000"  # zero-decimal currency without a preset
    assert amount_example("PLN") == "9.99"
    assert amount_example(None) == "9.99"
    # Every example must be accepted by the parser for its own currency
    for code in ("USD", "EUR", "GBP", "RUB", "UAH", "KZT", "JPY", "VND", "PLN"):
        assert parse_amount(amount_example(code), code) is not None


def test_format_amount():
    assert format_amount(2224, "USD") == "22.24 USD"
    assert format_amount(123450, "RUB") == "1 234.50 RUB"
    assert format_amount(990, "JPY") == "990 JPY"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("16.10", date(2026, 10, 16)),
        ("1.10", date(2026, 10, 1)),
        ("16.12", date(2025, 12, 16)),  # no year and in the future -> last year
        ("16.10.2025", date(2025, 10, 16)),
        ("16/10/25", date(2025, 10, 16)),
    ],
)
def test_parse_past_date_valid(text, expected):
    assert parse_past_date(text, TODAY) == expected


@pytest.mark.parametrize("text", ["31.02", "17.10.2026", "yesterday", "16.13", ""])
def test_parse_past_date_invalid(text):
    assert parse_past_date(text, TODAY) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [("3, 0", [3, 0]), ("0 7 3", [7, 3, 0]), ("1,1", [1]), ("-", []), ("abc", None),
     ("", None), ("61", None), ("1 2 3 4 5 6", None)],
)
def test_parse_offsets(text, expected):
    assert parse_offsets(text) == expected


def test_relative_days():
    assert relative_days(TODAY, TODAY) == "сегодня"
    assert relative_days(date(2026, 10, 17), TODAY) == "завтра"
    assert relative_days(date(2026, 10, 21), TODAY) == "через 5 дней"
    assert relative_days(date(2026, 11, 7), TODAY) == "через 22 дня"

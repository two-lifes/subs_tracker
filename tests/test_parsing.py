import pytest

from bot.services.money import parse_currency
from bot.services.tz import parse_timezone


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Europe/Moscow", "Europe/Moscow"),
        ("  Europe/Berlin ", "Europe/Berlin"),
        ("UTC", "UTC"),
        ("UTC+3", "Etc/GMT-3"),
        ("utc-5", "Etc/GMT+5"),
        ("+3", "Etc/GMT-3"),
        ("GMT+0", "UTC"),
    ],
)
def test_parse_timezone_valid(text, expected):
    assert parse_timezone(text) == expected


@pytest.mark.parametrize("text", ["", "Mars/Olympus", "UTC+15", "../etc/passwd", "hello"])
def test_parse_timezone_invalid(text):
    assert parse_timezone(text) is None


def test_parse_currency():
    assert parse_currency(" usd ") == "USD"
    assert parse_currency("US") is None
    assert parse_currency("12$") is None

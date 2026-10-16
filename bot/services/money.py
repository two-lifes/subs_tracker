import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

COMMON_CURRENCIES = ["USD", "EUR", "RUB", "UAH", "KZT", "GBP"]

# ISO 4217 currencies without a minor unit; everything else is assumed to have 2 decimals
_ZERO_DECIMAL = {"JPY", "KRW", "VND", "CLP", "ISK", "UGX", "PYG", "XAF", "XOF"}

_CODE_RE = re.compile(r"^[A-Z]{3}$")
_MAX_MAJOR = Decimal(10) ** 9


def parse_currency(text: str) -> str | None:
    code = text.strip().upper()
    return code if _CODE_RE.match(code) else None


_SYMBOLS = {"$": "USD", "€": "EUR", "₽": "RUB", "₴": "UAH", "₸": "KZT", "£": "GBP"}
_PRICE_RE = re.compile(r"^([^\d\s]{1,3})?\s*([\d\s ',.]+?)\s*([^\d\s]{1,3})?$")


def split_price(text: str) -> tuple[str, str | None] | None:
    """'22.24 usd' / '$22.24' / '22.24' -> (number text, currency or None)."""
    match = _PRICE_RE.match(text.strip())
    if not match:
        return None
    prefix, number, suffix = match.groups()
    if prefix and suffix:
        return None
    marker = prefix or suffix
    if marker is None:
        return number, None
    currency = _SYMBOLS.get(marker) or parse_currency(marker)
    return (number, currency) if currency else None


# Typical subscription prices, so input hints look natural for each currency
_EXAMPLES = {
    "USD": "9.99",
    "EUR": "9.99",
    "GBP": "9.99",
    "RUB": "2000",
    "UAH": "200",
    "KZT": "3000",
    "JPY": "1000",
}


def amount_example(currency: str | None) -> str:
    if currency in _EXAMPLES:
        return _EXAMPLES[currency]
    return "1000" if currency and exponent(currency) == 0 else "9.99"


def exponent(currency: str) -> int:
    return 0 if currency in _ZERO_DECIMAL else 2


def parse_amount(text: str, currency: str) -> int | None:
    """'22.24', '22,24', '1 234,5' -> minor units. None if invalid, non-positive or too precise."""
    cleaned = re.sub(r"[\s ']", "", text).replace(",", ".")
    if not re.fullmatch(r"\d+(\.\d+)?", cleaned):
        return None
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    exp = exponent(currency)
    minor = value.scaleb(exp)
    if value <= 0 or value >= _MAX_MAJOR or minor != minor.to_integral_value():
        return None
    return int(minor)


def format_amount(minor: int | Decimal, currency: str) -> str:
    exp = exponent(currency)
    value = Decimal(minor).scaleb(-exp).quantize(Decimal(1).scaleb(-exp), ROUND_HALF_UP)
    text = f"{value:,.{exp}f}".replace(",", " ")
    return f"{text} {currency}"

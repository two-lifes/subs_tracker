import re
from datetime import date, time

_DATE_RE = re.compile(r"^(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2}|\d{4}))?$")


def parse_past_date(text: str, today: date) -> date | None:
    """Parses DD.MM[.YY|.YYYY] as a date that is not in the future.

    Without a year, picks the most recent such date (16.12 in October means last December).
    """
    match = _DATE_RE.match(text.strip())
    if not match:
        return None
    day, month, year_text = int(match.group(1)), int(match.group(2)), match.group(3)
    try:
        if year_text is None:
            parsed = date(today.year, month, day)
            if parsed > today:
                parsed = date(today.year - 1, month, day)
        else:
            year = int(year_text)
            parsed = date(year + 2000 if year < 100 else year, month, day)
    except ValueError:
        return None
    if parsed > today:
        return None
    return parsed


_TIME_RE = re.compile(r"^(\d{1,2})[:.](\d{2})$")


def parse_time(text: str) -> time | None:
    match = _TIME_RE.match(text.strip())
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


MAX_OFFSET_DAYS = 60


def parse_offsets(text: str) -> list[int] | None:
    """'3, 0' -> [3, 0]; '-' -> [] (no reminders). None if invalid."""
    text = text.strip()
    if text == "-":
        return []
    parts = [p for p in re.split(r"[\s,;]+", text) if p]
    if not parts or len(parts) > 5 or not all(p.isdigit() for p in parts):
        return None
    values = sorted({int(p) for p in parts}, reverse=True)
    if values[0] > MAX_OFFSET_DAYS:
        return None
    return values


def days_word(n: int) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return "день"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "дня"
    return "дней"


def relative_days(target: date, today: date) -> str:
    delta = (target - today).days
    if delta == 0:
        return "сегодня"
    if delta == 1:
        return "завтра"
    if delta > 0:
        return f"через {delta} {days_word(delta)}"
    return f"{-delta} {days_word(delta)} назад"

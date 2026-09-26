from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal


DAILY_OPERATING_HOURS = Decimal("8.00")


def easter_sunday(year: int) -> date:
    """Return Gregorian Easter Sunday using the Meeus/Jones/Butcher algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    length = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * length) // 451
    month = (h + length - 7 * m + 114) // 31
    day = (h + length - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def standard_kenya_holidays(year: int) -> list[tuple[date, str]]:
    easter = easter_sunday(year)
    holidays = [
        (date(year, 1, 1), "New Year's Day"),
        (easter - timedelta(days=2), "Good Friday"),
        (easter + timedelta(days=1), "Easter Monday"),
        (date(year, 5, 1), "Labour Day"),
        (date(year, 6, 1), "Madaraka Day"),
        (date(year, 10, 10), "Mazingira Day"),
        (date(year, 10, 20), "Mashujaa Day"),
        (date(year, 12, 12), "Jamhuri Day"),
        (date(year, 12, 25), "Christmas Day"),
        (date(year, 12, 26), "Boxing Day"),
    ]
    return sorted(holidays)


def operating_hours(
    start_date: date,
    end_date: date,
    holiday_dates: set[date],
) -> Decimal:
    eligible_days = 0
    current = start_date
    while current <= end_date:
        if current.weekday() < 5 and current not in holiday_dates:
            eligible_days += 1
        current += timedelta(days=1)
    return DAILY_OPERATING_HOURS * eligible_days

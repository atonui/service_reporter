from datetime import date
from decimal import Decimal

from backend.app.services.business_calendar import operating_hours, standard_kenya_holidays


def test_operating_hours_exclude_weekends_holidays_and_lunch() -> None:
    assert operating_hours(
        date(2026, 4, 1),
        date(2026, 4, 7),
        {date(2026, 4, 3), date(2026, 4, 6)},
    ) == Decimal("24.00")


def test_standard_kenya_calendar_contains_movable_easter_dates() -> None:
    holidays = dict(standard_kenya_holidays(2026))
    assert holidays[date(2026, 4, 3)] == "Good Friday"
    assert holidays[date(2026, 4, 6)] == "Easter Monday"

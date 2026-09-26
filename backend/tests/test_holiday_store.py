from datetime import date

from backend.app.schemas.holiday import HolidayInput
from backend.app.services.holiday_store import (
    create_holiday,
    delete_holiday,
    holiday_dates_for_range,
    list_holidays,
)


def test_holiday_calendar_seeds_and_accepts_gazetted_dates(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SERVICE_INTELLIGENCE_DB_PATH", str(tmp_path / "holidays.db"))
    seeded = list_holidays(2026)
    assert any(item.name == "Madaraka Day" for item in seeded)

    added = create_holiday(
        HolidayInput(holiday_date="2026-05-27", name="Eid al-Adha")
    )
    dates = holiday_dates_for_range(date(2026, 5, 1), date(2026, 5, 31))
    assert date(2026, 5, 27) in dates

    assert delete_holiday(added.id) is True
    assert date(2026, 5, 27) not in holiday_dates_for_range(
        date(2026, 5, 1), date(2026, 5, 31)
    )

from datetime import date
from decimal import Decimal

from backend.app.schemas.report_profile import ReportProfileInput
from backend.app.services.business_calendar import operating_hours
from backend.app.services.report_profile_store import (
    create_report_profile,
    delete_report_profile,
    get_report_profile,
    list_report_profiles,
    update_report_profile,
)


def test_standard_profile_is_seeded(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SERVICE_INTELLIGENCE_DB_PATH", str(tmp_path / "profiles.db"))

    profile = get_report_profile()

    assert profile is not None
    assert profile.name == "Kenya standard"
    assert profile.working_days == [0, 1, 2, 3, 4]
    assert profile.daily_hours == Decimal("8.00")
    assert profile.is_default is True


def test_profile_crud_and_default_protection(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SERVICE_INTELLIGENCE_DB_PATH", str(tmp_path / "profiles.db"))
    standard = get_report_profile()
    created = create_report_profile(
        ReportProfileInput(
            name="Six-day service",
            working_days=[0, 1, 2, 3, 4, 5],
            start_time="08:00",
            end_time="17:00",
            break_minutes=60,
            is_default=True,
        )
    )

    assert created.is_default is True
    assert get_report_profile().id == created.id
    assert next(item for item in list_report_profiles() if item.id == standard.id).is_default is False
    updated = update_report_profile(
        created.id,
        ReportProfileInput(
            name="Six-day service",
            working_days=[0, 1, 2, 3, 4, 5],
            start_time="08:00",
            end_time="16:00",
            break_minutes=30,
            is_default=True,
        ),
    )
    assert updated is not None
    assert updated.daily_hours == Decimal("7.50")
    try:
        delete_report_profile(created.id)
    except ValueError as exc:
        assert "default" in str(exc)
    else:
        raise AssertionError("default profile should not be removable")


def test_operating_hours_accept_custom_weekdays_and_daily_hours() -> None:
    assert operating_hours(
        date(2026, 9, 21),
        date(2026, 9, 27),
        set(),
        {0, 1, 2, 3, 4, 5},
        Decimal("7.50"),
    ) == Decimal("45.00")


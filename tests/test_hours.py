"""Tests for the OSM opening-hours parser."""

from gtm_engine.enrichment.hours import parse_opening_hours


def test_none_input():
    assert parse_opening_hours(None) is None
    assert parse_opening_hours("") is None


def test_24_7():
    result = parse_opening_hours("24/7")
    assert result is not None
    assert result.is_24_7
    assert result.days_open == 7
    assert len(result.weekly_schedule) == 7


def test_weekdays_only():
    result = parse_opening_hours("Mo-Fr 09:00-18:00")
    assert result is not None
    assert result.days_open == 5
    assert "Mo" in result.weekly_schedule
    assert "Fr" in result.weekly_schedule
    assert "Sa" not in result.weekly_schedule
    assert result.weekly_schedule["Mo"] == ["09:00-18:00"]


def test_weekdays_plus_saturday():
    result = parse_opening_hours("Mo-Fr 09:00-18:00; Sa 10:00-14:00")
    assert result is not None
    assert result.days_open == 6
    assert result.weekly_schedule["Sa"] == ["10:00-14:00"]


def test_full_week():
    result = parse_opening_hours("Mo-Su 08:00-22:00")
    assert result is not None
    assert result.days_open == 7


def test_sunday_off():
    result = parse_opening_hours("Mo-Su 09:00-17:00; Su off")
    assert result is not None
    assert result.days_open == 6
    assert "Su" not in result.weekly_schedule


def test_single_day():
    result = parse_opening_hours("Fr 18:00-23:00")
    assert result is not None
    assert result.days_open == 1
    assert result.weekly_schedule["Fr"] == ["18:00-23:00"]


def test_raw_preserved():
    raw = "Mo-Fr 09:00-18:00; Sa 10:00-14:00"
    result = parse_opening_hours(raw)
    assert result.raw == raw

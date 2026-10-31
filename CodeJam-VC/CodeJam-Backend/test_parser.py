"""Run with: python -m pytest test_parser.py"""
from datetime import datetime

import pytest

from main import MAX_EVENTS, json_to_google_event, parse_schedule

# Wednesday 28 October 2026, 10:00
REF = datetime(2026, 10, 28, 10, 0)


def parse(text, ref=REF):
    events, warnings = parse_schedule(text, ref)
    return [e.to_dict() for e in events], warnings


def only(text, ref=REF):
    events, _ = parse(text, ref)
    assert len(events) == 1, events
    return events[0]


def test_basic_event_with_date_time_and_location():
    e = only("Dinner with Alex tomorrow at 7 pm at the Keg")
    assert e["activity"] == "Dinner with Alex"
    assert e["location"] == "Keg"
    assert e["start"] == "2026-10-29T19:00"
    assert e["end"] == "2026-10-29T20:00"


def test_whisper_style_punctuation():
    e = only("Dentist appointment on November 24th at 2:30 p.m.")
    assert e["activity"] == "Dentist appointment"
    assert e["start"] == "2026-11-24T14:30"


def test_day_number_is_not_mistaken_for_an_hour():
    e = only("Team sync on 24 November")
    assert e["start"] == "2026-11-24T09:00"
    assert e["time_inferred"] is True


def test_relative_days_are_not_times():
    e = only("Call mom in 2 days at 6 pm")
    assert e["start"] == "2026-10-30T18:00"
    assert e["activity"] == "Call mom"


def test_relative_hours():
    e = only("Remind me to stretch in 30 minutes")
    assert e["activity"] == "Stretch"
    assert e["start"] == "2026-10-28T10:30"


def test_range_with_shared_meridiem():
    e = only("Study session from 2 to 4 pm in the library")
    assert e["start"] == "2026-10-28T14:00"
    assert e["end"] == "2026-10-28T16:00"
    assert e["location"] == "library"


def test_range_crossing_noon():
    e = only("Workshop tomorrow from 11 to 1 pm")
    assert e["start"] == "2026-10-29T11:00"
    assert e["end"] == "2026-10-29T13:00"


def test_range_crossing_midnight():
    e = only("Party friday from 10 pm to 2 am")
    assert e["start"] == "2026-10-30T22:00"
    assert e["end"] == "2026-10-31T02:00"


def test_between_and_is_a_range_not_two_events():
    e = only("Office hours between 3 and 5 tomorrow")
    assert e["start"] == "2026-10-29T15:00"
    assert e["end"] == "2026-10-29T17:00"


def test_duration():
    e = only("Gym tomorrow at 7 am for an hour and a half")
    assert e["start"] == "2026-10-29T07:00"
    assert e["end"] == "2026-10-29T08:30"


def test_spoken_times():
    assert only("Lunch with Sam tomorrow at noon")["start"] == "2026-10-29T12:00"
    assert only("Call Jo tomorrow at half past seven in the evening")["start"] == "2026-10-29T19:30"
    assert only("Standup tomorrow at quarter to nine")["start"] == "2026-10-29T08:45"
    assert only("Meet Kim tomorrow at three o'clock")["start"] == "2026-10-29T15:00"


def test_ambiguous_hour_assumes_afternoon_and_warns():
    e = only("Coffee tomorrow at 3")
    assert e["start"] == "2026-10-29T15:00"
    assert any("Assumed 3:00 PM" in w for w in e["warnings"])


def test_morning_context():
    assert only("Run tomorrow morning at 6")["start"] == "2026-10-29T06:00"


def test_time_already_passed_today_moves_to_tomorrow():
    e = only("Gym at 8 am")
    assert e["start"] == "2026-10-29T08:00"
    assert any("tomorrow" in w for w in e["warnings"])


def test_multiple_events_in_one_sentence():
    events, _ = parse("Gym tomorrow at 7 am and lunch with Sam at noon, then study for 2 hours")
    assert [e["activity"] for e in events] == ["Gym", "Lunch with Sam", "Study"]
    assert [e["start"] for e in events] == ["2026-10-29T07:00", "2026-10-29T12:00", "2026-10-29T13:00"]
    assert events[2]["end"] == "2026-10-29T15:00"


def test_and_inside_an_activity_does_not_split():
    e = only("Dinner with Alex and Sam tomorrow at 7")
    assert e["activity"] == "Dinner with Alex and Sam"


def test_same_activity_several_times():
    events, _ = parse("Meetings tomorrow at 2 pm and 4 pm")
    assert [e["activity"] for e in events] == ["Meetings", "Meetings"]
    assert [e["start"] for e in events] == ["2026-10-29T14:00", "2026-10-29T16:00"]


def test_weekdays():
    assert only("Soccer on friday at 5 pm")["start"] == "2026-10-30T17:00"
    # Same weekday as today means next week
    assert only("Review on wednesday at 5 pm")["start"] == "2026-11-04T17:00"
    assert only("Review next monday at 5 pm")["start"] == "2026-11-02T17:00"


def test_next_weekday_in_same_week_skips_a_week():
    assert only("Brunch next friday at 11 am")["start"] == "2026-11-06T11:00"


def test_past_month_day_rolls_to_next_year():
    assert only("Birthday party on March 3 at 6 pm")["start"] == "2027-03-03T18:00"


def test_invalid_date_is_reported():
    e = only("Exam on February 30 at 9 am")
    assert any("isn't a real date" in w for w in e["warnings"])


def test_invalid_time_is_reported():
    e = only("Shift tomorrow at 25:00")
    assert e["time_inferred"] is True
    assert any("isn't a valid time" in w for w in e["warnings"])


def test_numbers_that_are_not_times():
    e = only("Meeting in room 204 tomorrow at 3 pm")
    assert e["location"] == "room 204"
    assert e["start"] == "2026-10-29T15:00"
    e = only("Dinner for 2 people tomorrow at 8 pm")
    assert e["start"] == "2026-10-29T20:00"


def test_calendar_instruction_is_not_a_location():
    e = only("Can you add a haircut on Friday at 4 to my calendar please")
    assert e["activity"] == "Haircut"
    assert e["location"] is None
    assert e["start"] == "2026-10-30T16:00"


def test_recurring_is_flagged():
    e = only("Yoga every monday at 6 pm")
    assert e["start"] == "2026-11-02T18:00"
    assert any("Repeating" in w for w in e["warnings"])


def test_empty_and_noise():
    assert parse("") == ([], ["I didn't catch anything — try saying it again."])
    events, warnings = parse("   ")
    assert events == [] and warnings


def test_time_without_activity():
    e = only("tomorrow at 5 pm")
    assert e["activity"] == "New event"
    assert e["warnings"]


def test_event_limit():
    text = ", ".join(f"task{i} tomorrow at {i % 12 + 1} pm" for i in range(15))
    events, warnings = parse(text)
    assert len(events) == MAX_EVENTS
    assert any("up to" in w for w in warnings)


def test_duplicates_are_dropped():
    events, _ = parse("Gym tomorrow at 7 am. Gym tomorrow at 7 am.")
    assert len(events) == 1


def test_month_end_relative_months():
    e = only("Dentist in 1 month at 9 am", ref=datetime(2026, 1, 31, 8, 0))
    assert e["start"] == "2026-02-28T09:00"


def test_google_event_conversion():
    body = json_to_google_event({"activity": " Gym ", "location": "",
                                 "start": "2026-10-29T07:00", "end": "2026-10-29T08:00"}, "Europe/Paris")
    assert body["summary"] == "Gym"
    assert "location" not in body
    assert body["start"] == {"dateTime": "2026-10-29T07:00:00", "timeZone": "Europe/Paris"}


def test_google_event_conversion_legacy_fields():
    e = only("Gym tomorrow at 7 am")
    body = json_to_google_event(e)
    assert body["start"]["dateTime"] == "2026-10-29T07:00:00"


@pytest.mark.parametrize("data", [
    {"activity": "x", "start": "2026-10-29T08:00", "end": "2026-10-29T07:00"},
    {"activity": "  ", "start": "2026-10-29T07:00", "end": "2026-10-29T08:00"},
])
def test_google_event_conversion_rejects_bad_events(data):
    with pytest.raises(ValueError):
        json_to_google_event(data)

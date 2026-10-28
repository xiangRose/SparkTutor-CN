"""Server-owned dates, persistence, streaks, and idempotent daily check-in."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import sqlite3

import pytest

from sparktutor.state.checkins import CheckInStore
from sparktutor.state.data_management import backup_learning_database, clear_learning_records


def moment(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def test_reading_status_does_not_sign_in_and_record_survives_restart(tmp_path):
    path = tmp_path / "progress.db"
    store = CheckInStore(path)
    now = moment("2026-10-27T01:00:00")
    assert store.status(now=now)["totalDays"] == 0
    assert store.check_in(now=now)["recorded"] is True
    restored = CheckInStore(path).status(now=now)
    assert restored["checkedInToday"] is True
    assert restored["checkedDates"] == ["2026-10-27"]
    assert restored["currentStreak"] == 1


def test_beijing_midnight_defines_a_new_day(tmp_path):
    store = CheckInStore(tmp_path / "progress.db")
    before = store.check_in(now=moment("2026-10-26T15:59:59"))
    after = store.check_in(now=moment("2026-10-26T16:00:00"))
    assert before["today"] == "2026-10-26"
    assert after["today"] == "2026-10-27"
    assert after["recorded"] is True
    assert after["currentStreak"] == 2
    assert after["totalDays"] == 2


def test_streak_crosses_month_and_ends_only_after_a_missed_day(tmp_path):
    store = CheckInStore(tmp_path / "progress.db")
    for value in ["2026-10-30T04:00:00", "2026-10-31T04:00:00", "2026-11-01T04:00:00"]:
        store.check_in(now=moment(value))
    today = moment("2026-11-02T04:00:00")
    assert store.status(now=today)["currentStreak"] == 3
    assert store.status(now=today)["checkedInToday"] is False
    previous = store.status("2026-10", now=today)
    assert previous["monthCount"] == 2
    assert previous["checkedDates"] == ["2026-10-30", "2026-10-31"]
    assert previous["totalDays"] == 3
    assert store.status(now=moment("2026-11-03T04:00:00"))["currentStreak"] == 0
    assert store.check_in(now=moment("2026-11-03T04:00:00"))["currentStreak"] == 1


def test_parallel_and_repeated_requests_cannot_add_multiple_days(tmp_path):
    path = tmp_path / "progress.db"
    store = CheckInStore(path)
    now = moment("2026-10-27T04:00:00")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.check_in(now=now), range(16)))
    assert sum(item["recorded"] for item in results) == 1
    assert all(item["totalDays"] == 1 for item in results)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM study_checkins").fetchone()[0] == 1


@pytest.mark.parametrize("month", ["2026-13", "2026-00", "2026-1", "0000-01", "2026-10' OR 1=1", 27, []])
def test_invalid_months_are_rejected(tmp_path, month):
    with pytest.raises(ValueError):
        CheckInStore(tmp_path / "progress.db").status(month)


def test_calendar_supports_leap_year_and_starts_on_monday(tmp_path):
    status = CheckInStore(tmp_path / "progress.db").status("2028-02")
    assert status["daysInMonth"] == 29
    assert status["firstWeekday"] == 1  # Tuesday, with Monday as day zero.


def test_existing_database_backup_includes_habits_and_course_reset_preserves_them(tmp_path):
    path = tmp_path / "progress.db"
    now = moment("2026-10-27T04:00:00")
    CheckInStore(path).check_in(now=now)
    backup = tmp_path / "backup.db"
    backup_learning_database(path, backup)
    assert CheckInStore(backup).status(now=now)["totalDays"] == 1
    clear_learning_records(path)
    assert CheckInStore(path).status(now=now)["checkedInToday"] is True

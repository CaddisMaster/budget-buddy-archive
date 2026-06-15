"""Unit tests for the recurring-transaction date math.

`compute_next_due()` is a pure function (calendar / datetime / dateutil only),
so these tests need no database or running app — they just import the function
and assert the next due date for each frequency and edge case.
"""
from datetime import date

import pytest

from app.blueprints.transactions import compute_next_due


# --- Fixed-interval frequencies ------------------------------------------

@pytest.mark.parametrize("frequency, current, expected", [
    ("weekly",    date(2026, 6, 15), date(2026, 6, 22)),
    ("biweekly",  date(2026, 6, 15), date(2026, 6, 29)),
    ("monthly",   date(2026, 6, 15), date(2026, 7, 15)),
    ("quarterly", date(2026, 6, 15), date(2026, 9, 15)),
    ("annually",  date(2026, 6, 15), date(2027, 6, 15)),
])
def test_fixed_frequency_advances(frequency, current, expected):
    assert compute_next_due(current, frequency) == expected


def test_weekly_crosses_month_boundary():
    assert compute_next_due(date(2026, 6, 29), "weekly") == date(2026, 7, 6)


def test_biweekly_crosses_year_boundary():
    assert compute_next_due(date(2026, 12, 24), "biweekly") == date(2027, 1, 7)


def test_monthly_crosses_year_boundary():
    assert compute_next_due(date(2026, 12, 15), "monthly") == date(2027, 1, 15)


def test_quarterly_crosses_year_boundary():
    assert compute_next_due(date(2026, 11, 15), "quarterly") == date(2027, 2, 15)


# --- Month-end clamping and leap years -----------------------------------

def test_monthly_jan31_clamps_to_feb28():
    # relativedelta snaps a 31st to the last valid day of a shorter month.
    assert compute_next_due(date(2026, 1, 31), "monthly") == date(2026, 2, 28)


def test_monthly_jan31_clamps_to_feb29_leap_year():
    assert compute_next_due(date(2028, 1, 31), "monthly") == date(2028, 2, 29)


def test_annually_leap_day_clamps_to_feb28():
    assert compute_next_due(date(2024, 2, 29), "annually") == date(2025, 2, 28)


def test_quarterly_clamps_short_month():
    # Nov 30 + 3 months -> Feb 30 doesn't exist, clamps to Feb 28.
    assert compute_next_due(date(2026, 11, 30), "quarterly") == date(2027, 2, 28)


# --- Semi-monthly (two pay days that alternate) --------------------------

def test_semimonthly_advances_to_second_day_this_month():
    # On the 15th, the next pay day is the (clamped) 31st of the same month.
    result = compute_next_due(date(2026, 6, 15), "semimonthly",
                              anchor_day=15, second_day=31)
    assert result == date(2026, 6, 30)


def test_semimonthly_rolls_to_first_day_next_month():
    # Past the month's high day -> jump to the low day of the next month.
    result = compute_next_due(date(2026, 6, 30), "semimonthly",
                              anchor_day=15, second_day=31)
    assert result == date(2026, 7, 15)


def test_semimonthly_full_alternation_cycle():
    # 15 -> 30 -> 15 -> 31 -> 15 ... walking the schedule forward.
    cur = date(2026, 6, 15)
    sequence = []
    for _ in range(4):
        cur = compute_next_due(cur, "semimonthly", anchor_day=15, second_day=31)
        sequence.append(cur)
    assert sequence == [
        date(2026, 6, 30),
        date(2026, 7, 15),
        date(2026, 7, 31),
        date(2026, 8, 15),
    ]


def test_semimonthly_second_day_clamps_in_february():
    result = compute_next_due(date(2026, 2, 15), "semimonthly",
                              anchor_day=15, second_day=31)
    assert result == date(2026, 2, 28)


def test_semimonthly_day_order_does_not_matter():
    # anchor/second are sorted internally, so 31/15 == 15/31.
    a = compute_next_due(date(2026, 6, 20), "semimonthly",
                         anchor_day=31, second_day=15)
    b = compute_next_due(date(2026, 6, 20), "semimonthly",
                         anchor_day=15, second_day=31)
    assert a == b == date(2026, 6, 30)


# --- Fallbacks -----------------------------------------------------------

def test_semimonthly_without_second_day_falls_back_to_monthly():
    result = compute_next_due(date(2026, 6, 15), "semimonthly",
                              anchor_day=15, second_day=None)
    assert result == date(2026, 7, 15)


def test_unknown_frequency_falls_back_to_monthly():
    assert compute_next_due(date(2026, 6, 15), "biennially") == date(2026, 7, 15)

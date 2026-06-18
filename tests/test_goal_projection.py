"""Pure unit tests for compute_goal_projection() — no DB, like test_recurring.

The helper takes plain numbers/dates (saved, recent monthly inflow, target date)
and returns progress + two forward-looking projections. A fixed `today` keeps
the date math deterministic.
"""
from datetime import date

from app.blueprints.goals import compute_goal_projection

TODAY = date(2026, 1, 1)


def test_percent_and_remaining_partial():
    p = compute_goal_projection(1000, 250, None, 0, today=TODAY)
    assert p["percent"] == 25.0
    assert p["remaining"] == 750.0
    assert p["complete"] is False


def test_complete_when_saved_meets_target():
    p = compute_goal_projection(100, 120, date(2026, 6, 1), 0, today=TODAY)
    assert p["complete"] is True
    assert p["percent"] == 100.0          # clamped, not 120
    assert p["on_track"] is True


def test_required_per_month_from_target_date():
    # 1200 over exactly 12 months → 100/mo.
    p = compute_goal_projection(1200, 0, date(2027, 1, 1), 0, today=TODAY)
    assert p["required_per_month"] == 100.0


def test_projected_completion_on_track():
    # 1200 to go at 200/mo → done in 6 months (2026-07-01), beats the deadline.
    p = compute_goal_projection(1200, 0, date(2027, 1, 1), 200, today=TODAY)
    assert p["projected_date"] == date(2026, 7, 1)
    assert p["on_track"] is True


def test_projected_completion_behind():
    # 1200 to go at 50/mo → 24 months (2028-01-01), misses the deadline.
    p = compute_goal_projection(1200, 0, date(2027, 1, 1), 50, today=TODAY)
    assert p["projected_date"] == date(2028, 1, 1)
    assert p["on_track"] is False


def test_no_target_date_means_no_pace_or_track():
    p = compute_goal_projection(500, 100, None, 100, today=TODAY)
    assert p["required_per_month"] is None
    assert p["on_track"] is None
    assert p["projected_date"] == date(2026, 5, 1)  # 400 / 100 = 4 months


def test_zero_inflow_with_deadline_is_off_track():
    p = compute_goal_projection(500, 100, date(2026, 6, 1), 0, today=TODAY)
    assert p["projected_date"] is None
    assert p["on_track"] is False


def test_negative_inflow_treated_as_no_progress():
    p = compute_goal_projection(500, 100, date(2026, 6, 1), -50, today=TODAY)
    assert p["projected_date"] is None
    assert p["on_track"] is False

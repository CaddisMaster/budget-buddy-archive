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


# --- payoff framing (v10.9) ---------------------------------------------------
# A payoff goal feeds the SAME function with target = the starting debt and
# saved = balance − baseline = amount paid off. These prove the readings a
# payoff goal relies on, with the math untouched.

def test_payoff_partial_paydown():
    # $3,000 starting debt, $1,200 paid → 40%, $1,800 still owed.
    p = compute_goal_projection(3000, 1200, None, 0, today=TODAY)
    assert p["percent"] == 40.0
    assert p["remaining"] == 1800.0
    assert p["complete"] is False


def test_payoff_charging_more_clamps_percent_and_grows_remaining():
    # New charges outpaced payments: paid = −200 → 0% (clamped), and remaining
    # exceeds the starting debt (the current REAL debt, self-correcting).
    p = compute_goal_projection(3000, -200, None, 0, today=TODAY)
    assert p["percent"] == 0.0
    assert p["remaining"] == 3200.0
    assert p["complete"] is False


def test_payoff_complete_at_or_past_zero():
    # Paid past $0 (balance now positive) → complete, clamped at 100%.
    p = compute_goal_projection(3000, 3050, date(2026, 6, 1), 0, today=TODAY)
    assert p["complete"] is True
    assert p["percent"] == 100.0
    assert p["on_track"] is True


def test_payoff_required_per_month_is_debt_over_months():
    # $2,400 still owed, 12 months to the date → $200/mo to be debt-free on time.
    p = compute_goal_projection(3000, 600, date(2027, 1, 1), 0, today=TODAY)
    assert p["required_per_month"] == 200.0


# --- v10.15 — interest-aware payoff projection (apr param) --------------------

def test_apr_none_output_unchanged():
    # Backward compat: every pre-v10.15 figure identical, the new key is None.
    p = compute_goal_projection(1200, 0, date(2027, 1, 1), 200, today=TODAY)
    q = compute_goal_projection(1200, 0, date(2027, 1, 1), 200, today=TODAY,
                                apr=None)
    assert p == q
    assert p["est_monthly_interest"] is None


def test_est_monthly_interest_figure():
    # $1,200 owed at 24% APR → 1200 × 0.02 = $24/mo.
    p = compute_goal_projection(1200, 0, None, 0, today=TODAY, apr=24)
    assert p["est_monthly_interest"] == 24.0


def test_interest_pushes_projected_date_later():
    # Interest-free: 1200 at 100/mo = 12 months (2027-01-01). At 24% APR the
    # simulation needs 14 (the first payments mostly fight the 2%/mo accrual).
    free = compute_goal_projection(1200, 0, None, 100, today=TODAY)
    apr = compute_goal_projection(1200, 0, None, 100, today=TODAY, apr=24)
    assert free["projected_date"] == date(2027, 1, 1)
    assert apr["projected_date"] == date(2027, 3, 1)


def test_pace_below_interest_never_finishes():
    # $24/mo against $24/mo of interest — the balance never moves.
    p = compute_goal_projection(1200, 0, date(2027, 1, 1), 24, today=TODAY,
                                apr=24)
    assert p["projected_date"] is None
    assert p["on_track"] is False
    assert p["est_monthly_interest"] == 24.0


def test_pace_below_interest_no_target_date():
    p = compute_goal_projection(1200, 0, None, 24, today=TODAY, apr=24)
    assert p["projected_date"] is None
    assert p["on_track"] is None


def test_required_per_month_amortized():
    # 1200 over 12 months at 2%/mo: the amortized payment (113.47) beats the
    # straight-line 100 — paying 100/mo would land short by the interest.
    p = compute_goal_projection(1200, 0, date(2027, 1, 1), 0, today=TODAY,
                                apr=24)
    assert p["required_per_month"] == 113.47


def test_complete_goal_has_no_interest_figure():
    p = compute_goal_projection(3000, 3050, None, 0, today=TODAY, apr=24)
    assert p["complete"] is True
    assert p["est_monthly_interest"] is None


def test_apr_decimal_input_does_not_raise():
    from decimal import Decimal
    p = compute_goal_projection(1200, 0, None, 100, today=TODAY,
                                apr=Decimal("24.00"))
    assert p["est_monthly_interest"] == 24.0


def test_unusable_apr_falls_back_to_interest_free():
    # Zero/negative/NaN apr (stored values could predate the parser) → the
    # interest-free math, not a crash.
    for bad in (0, -5, float("nan")):
        p = compute_goal_projection(1200, 0, None, 100, today=TODAY, apr=bad)
        assert p["est_monthly_interest"] is None
        assert p["projected_date"] == date(2027, 1, 1)


def test_simulation_cap_terminates():
    # A pace a hair above the monthly interest would take ~626 months — past
    # the 600-month horizon, so it reads as "never" (and the loop is bounded).
    p = compute_goal_projection(1200, 0, None, 24.0001, today=TODAY, apr=24)
    assert p["projected_date"] is None

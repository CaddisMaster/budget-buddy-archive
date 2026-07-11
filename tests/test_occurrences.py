"""Pure occurrence-walker tests — _advance_past / upcoming_occurrences
(main.py). The walkers forward-walk compute_next_due; the weekly digest's
upcoming-week enumerator (digests._upcoming_scheduled) depends on their window
semantics: start EXCLUSIVE (anything due today was already materialized by the
due-runners), end INCLUSIVE.

(These originated with the v10.9 Safe to spend feature and outlived its
removal in v10.12.0 — moved here from test_safe_to_spend.py.)
"""
from datetime import date

from app.blueprints.main import _advance_past, upcoming_occurrences


def test_advance_past_walks_stale_next_due():
    # Monthly anchor months in the past walks to the first occurrence after `day`.
    occ = _advance_past(date(2026, 1, 5), "monthly", None, None, date(2026, 3, 10))
    assert occ == date(2026, 4, 5)


def test_advance_past_future_occurrence_untouched():
    occ = _advance_past(date(2026, 3, 20), "monthly", None, None, date(2026, 3, 10))
    assert occ == date(2026, 3, 20)


def test_occurrences_multiple_in_window():
    # Weekly bill, 22-day window → four occurrences.
    occs = upcoming_occurrences(date(2026, 3, 2), "weekly", None, None,
                                date(2026, 3, 1), date(2026, 3, 23))
    assert occs == [date(2026, 3, 2), date(2026, 3, 9),
                    date(2026, 3, 16), date(2026, 3, 23)]


def test_occurrences_window_end_inclusive():
    # A bill due exactly on the window's last day is in.
    occs = upcoming_occurrences(date(2026, 3, 23), "weekly", None, None,
                                date(2026, 3, 1), date(2026, 3, 23))
    assert occs == [date(2026, 3, 23)]
    # One day past the window is out.
    assert upcoming_occurrences(date(2026, 3, 24), "weekly", None, None,
                                date(2026, 3, 1), date(2026, 3, 23)) == []


def test_occurrences_window_start_exclusive():
    # next_due == window_start was already materialized by the due-runners;
    # only the NEXT cycle counts, and monthly pushes it past this window.
    occs = upcoming_occurrences(date(2026, 3, 1), "monthly", None, None,
                                date(2026, 3, 1), date(2026, 3, 15))
    assert occs == []


def test_occurrences_semimonthly_across_month_boundary():
    occs = upcoming_occurrences(date(2026, 3, 15), "semimonthly", 15, 31,
                                date(2026, 3, 1), date(2026, 4, 20))
    assert occs == [date(2026, 3, 15), date(2026, 3, 31), date(2026, 4, 15)]


def test_occurrences_empty_when_beyond_window():
    assert upcoming_occurrences(date(2026, 6, 1), "monthly", None, None,
                                date(2026, 3, 1), date(2026, 3, 31)) == []

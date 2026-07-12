"""v10.12 tests — History running balance connects across pages.

The v2-era defect: _load_history seeded running_balance = 0 and walked only the
LIMIT/OFFSET slice, so every page's balances were disconnected from the ledger
(page 1 included, whenever more than one page existed). The fix seeds the walk
with the signed SUM of every filtered row OLDER than the page slice. With a
month/search filter active the column reads as the running net of the matching
rows, carried across pages (the plan-mode call).

The conftest `users` fixture seeds one today-dated 42.50 expense per user; the
unfiltered tests fold it into their expectations, the month-filtered tests are
outside its month by construction.
"""
import pytest

from app.blueprints.transactions import PER_PAGE, _load_history
from conftest import create_transaction


def _signed(amount, ttype):
    return amount if ttype == "income" else -amount


def _tail_sum(ledger, index):
    """Expected balance of the ledger row at `index` (newest-first list of
    signed amounts): the row itself plus everything older."""
    return sum(ledger[index:])


def _seed_january(users):
    """30 rows on distinct January 2026 dates; returns the full expected
    newest-first signed ledger INCLUDING the fixture's today-dated expense."""
    uid = users["a"]["id"]
    acct = users["a"]["account_id"]
    rows = []
    for i in range(30):
        ttype = "income" if i % 3 == 0 else "expense"
        amount = 10 + i
        create_transaction(uid, acct, amount, f"2026-01-{i + 1:02d}", ttype)
        rows.append(_signed(amount, ttype))
    # Newest-first: fixture row (today) first, then Jan 30 back to Jan 1.
    return [_signed(42.50, "expense")] + list(reversed(rows))


def test_unfiltered_pages_connect(users):
    ledger = _seed_january(users)  # 31 rows -> pages of 25 + 6
    uid = users["a"]["id"]
    page1, total, pages = _load_history(uid, None, "", 1)
    page2, _, _ = _load_history(uid, None, "", 2)
    assert total == len(ledger) and pages == 2
    assert len(page1) == PER_PAGE and len(page2) == len(ledger) - PER_PAGE

    # Page 1's top row now shows the TRUE full net, not the net of 25 rows.
    assert float(page1[0].running_balance) == pytest.approx(_tail_sum(ledger, 0))
    # Continuity: page 1's bottom = page 2's top + the bottom row itself.
    assert float(page1[-1].running_balance) == pytest.approx(
        float(page2[0].running_balance) + _signed(float(page1[-1].amount),
                                                  page1[-1].transaction_type))
    assert float(page1[-1].running_balance) == pytest.approx(_tail_sum(ledger, PER_PAGE - 1))
    assert float(page2[0].running_balance) == pytest.approx(_tail_sum(ledger, PER_PAGE))
    # The oldest row's balance is just its own signed amount.
    assert float(page2[-1].running_balance) == pytest.approx(ledger[-1])


def test_month_filter_carries_within_the_filter(users):
    uid = users["a"]["id"]
    acct = users["a"]["account_id"]
    march = []
    for i in range(28):
        ttype = "income" if i % 4 == 0 else "expense"
        amount = 20 + i
        create_transaction(uid, acct, amount, f"2026-03-{i + 1:02d}", ttype)
        march.append(_signed(amount, ttype))
    ledger = list(reversed(march))  # newest-first within the filter
    # Big out-of-month rows that would blow every assertion if they leaked in.
    for day in ("2026-02-05", "2026-02-15", "2026-04-02"):
        create_transaction(uid, acct, 99999, day, "income")

    page1, total, pages = _load_history(uid, "2026-03", "", 1)
    page2, _, _ = _load_history(uid, "2026-03", "", 2)
    assert total == 28 and pages == 2

    # The filtered net starts from 0 at the oldest MATCHING row...
    assert float(page2[-1].running_balance) == pytest.approx(ledger[-1])
    # ...and carries across the filtered pages.
    assert float(page2[0].running_balance) == pytest.approx(_tail_sum(ledger, PER_PAGE))
    assert float(page1[-1].running_balance) == pytest.approx(
        float(page2[0].running_balance) + _signed(float(page1[-1].amount),
                                                  page1[-1].transaction_type))
    assert float(page1[0].running_balance) == pytest.approx(_tail_sum(ledger, 0))


def test_tbody_swap_carries_the_seed(users, client_a):
    """The HTMX tbody swap (Cancel/refresh path) renders page 2 with the same
    connected balance the full page load computes."""
    ledger = _seed_january(users)
    expected_top = _tail_sum(ledger, PER_PAGE)
    response = client_a.get("/transactions/rows?page=2")
    assert response.status_code == 200
    assert f"${expected_top:.2f}" in response.get_data(as_text=True)

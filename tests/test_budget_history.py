"""v10.9 Budget history — the append-only writer (nothing reads it yet).

The budgets table upserts ONE row per (user, category) in place, so
budget_history is the only record of past amounts — history can't be
backfilled later, which is why the writer ships before any reader. These
tests pin the three write points (set / clear / review-apply) and the
no-op rules that keep the log a log of CHANGES.
"""
from tests.conftest import create_budget, fetch_budget_history


# --- /budgets/set -------------------------------------------------------------

def test_set_records_a_change(client_a, users):
    a = users["a"]
    client_a.post("/budgets/set",
                  data={"category_id": a["category_id"], "amount": "400"})
    history = fetch_budget_history(a["id"], a["category_id"])
    assert [float(h[0]) for h in history] == [400.0]


def test_resaving_same_amount_is_not_recorded(client_a, users):
    a = users["a"]
    for _ in range(2):
        client_a.post("/budgets/set",
                      data={"category_id": a["category_id"], "amount": "400"})
    history = fetch_budget_history(a["id"], a["category_id"])
    assert len(history) == 1  # a no-op save isn't a change


def test_changed_amount_appends(client_a, users):
    a = users["a"]
    client_a.post("/budgets/set",
                  data={"category_id": a["category_id"], "amount": "400"})
    client_a.post("/budgets/set",
                  data={"category_id": a["category_id"], "amount": "450"})
    history = fetch_budget_history(a["id"], a["category_id"])
    assert [float(h[0]) for h in history] == [400.0, 450.0]


def test_history_records_the_rounded_amount(client_a, users):
    # /budgets/set rounds to whole dollars before the upsert; the log must
    # record what was SAVED, not what was typed, or the two drift.
    a = users["a"]
    client_a.post("/budgets/set",
                  data={"category_id": a["category_id"], "amount": "399.60"})
    history = fetch_budget_history(a["id"], a["category_id"])
    assert [float(h[0]) for h in history] == [400.0]


def test_rejected_amount_writes_nothing(client_a, users):
    a = users["a"]
    client_a.post("/budgets/set",
                  data={"category_id": a["category_id"], "amount": "nan"})
    assert fetch_budget_history(a["id"], a["category_id"]) == []


# --- /budgets/clear -----------------------------------------------------------

def test_clear_records_null(client_a, users):
    a = users["a"]
    client_a.post("/budgets/set",
                  data={"category_id": a["category_id"], "amount": "400"})
    client_a.post("/budgets/clear", data={"category_id": a["category_id"]})
    history = fetch_budget_history(a["id"], a["category_id"])
    assert len(history) == 2
    assert float(history[0][0]) == 400.0
    assert history[1][0] is None  # NULL = cleared


def test_clear_without_budget_writes_nothing(client_a, users):
    a = users["a"]
    response = client_a.post("/budgets/clear",
                             data={"category_id": a["category_id"]})
    assert response.status_code == 404  # existing behavior
    assert fetch_budget_history(a["id"], a["category_id"]) == []


# --- /budgets/review/apply ------------------------------------------------------

def test_review_apply_records_a_change(client_a, users, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    a = users["a"]
    create_budget(a["id"], a["category_id"], 300)
    client_a.post("/budgets/review/apply", data={
        "category_id": [str(a["category_id"])],
        f"amount_{a['category_id']}": "450",
    })
    history = fetch_budget_history(a["id"], a["category_id"])
    assert [float(h[0]) for h in history] == [450.0]


def test_review_apply_noop_amount_not_recorded(client_a, users, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    a = users["a"]
    create_budget(a["id"], a["category_id"], 450)
    client_a.post("/budgets/review/apply", data={
        "category_id": [str(a["category_id"])],
        f"amount_{a['category_id']}": "450",
    })
    assert fetch_budget_history(a["id"], a["category_id"]) == []


# --- isolation ------------------------------------------------------------------

def test_history_is_per_user(client_a, users):
    a, b = users["a"], users["b"]
    client_a.post("/budgets/set",
                  data={"category_id": a["category_id"], "amount": "400"})
    assert fetch_budget_history(b["id"], b["category_id"]) == []

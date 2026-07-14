"""v10.15 tests — AI-card read-state collapse.

The four AI narration cards (Insight, Forecast, Money agent, Goal Coach) render
as <details> collapsibles: CLOSED when cached content exists (the client-side
initAiCollapse script opens unseen content — not exercisable here), OPEN when
there's no cached content yet (the Generate button must be reachable without
JS) and on the fragment a generate route returns (just_generated). Each cached
card carries data-generated = the DB row's created_at isoformat, which is what
localStorage read-state keys on — so the generate fragment and the next page
load must render the SAME value (the RETURNING created_at fix).

No real API calls — the usual per-feature seams are monkeypatched.
"""
import re
from datetime import date

import app.ai as ai
from app.ai import _Insight, ParseError
from app.helpers import most_recent_sunday
from tests.conftest import (
    create_agent_run,
    create_goal,
    create_goal_coach,
    create_insight,
    create_transaction,
)

HX = {"HX-Request": "true"}
WEEK = most_recent_sunday(date.today())


def _prev_month():
    t = date.today()
    return (t.year - 1, 12) if t.month == 1 else (t.year, t.month - 1)


def _details_attrs(html, card_id):
    """The attribute string of one card's <details …> opening tag."""
    marker = f'<details id="{card_id}"'
    assert marker in html, f"{card_id} details element missing"
    return html.split(marker, 1)[1].split(">", 1)[0]


def _generated_value(attrs):
    m = re.search(r'data-generated="([^"]+)"', attrs)
    return m.group(1) if m else None


class _Seam:
    def __init__(self, result=None, boom=False):
        self.calls = 0
        self.result = result or _Insight(summary="Seamed narration.", tips=[])
        self.boom = boom

    def __call__(self, *a, **k):
        self.calls += 1
        if self.boom:
            raise ParseError("boom")
        return self.result


# --- cached content renders CLOSED with a read-state key --------------------

def test_cached_insight_card_closed_with_data_generated(client_a, users, monkeypatch):
    a = users["a"]
    year, month = _prev_month()
    create_transaction(a["id"], a["account_id"], 30, date(year, month, 15),
                       transaction_type="expense", category_id=a["category_id"])
    create_insight(a["id"], year, month,
                   {"summary": "Collapse recap text.", "tips": []})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    html = client_a.get("/").get_data(as_text=True)
    attrs = _details_attrs(html, "insight-card")
    assert 'data-ai-key="insight"' in attrs
    assert _generated_value(attrs)                 # read-state key present
    assert " open" not in attrs                    # server renders it closed
    assert "Collapse recap text." in html          # content still in the DOM
    assert "Net $" in html                         # headline figure in the summary


def test_cached_agent_card_headline_counts_findings(client_a, users, monkeypatch):
    create_agent_run(users["a"]["id"], WEEK,
                     {"summary": "Two things to look at.",
                      "findings": [
                          {"title": "F1", "detail": "d", "evidence": "e"},
                          {"title": "F2", "detail": "d", "evidence": "e"}],
                      "tools_used": ["recent_transactions"]})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    html = client_a.get("/").get_data(as_text=True)
    attrs = _details_attrs(html, "agent-card")
    assert 'data-ai-key="agent"' in attrs
    assert _generated_value(attrs)
    assert " open" not in attrs
    assert "2 findings" in html


def test_cached_agent_card_quiet_week_headline(client_a, users, monkeypatch):
    create_agent_run(users["a"]["id"], WEEK,
                     {"summary": "Nothing notable.", "findings": [],
                      "tools_used": ["recent_transactions"]})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    html = client_a.get("/").get_data(as_text=True)
    assert " open" not in _details_attrs(html, "agent-card")
    assert "All quiet" in html


def test_cached_coach_card_closed_on_goals_page(client_a, users, monkeypatch):
    a = users["a"]
    create_goal(a["id"], a["account_id"], 1000, baseline=0)
    t = date.today()
    create_goal_coach(a["id"], t.year, t.month,
                      {"summary": "Coach collapse text.", "tips": []})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    html = client_a.get("/goals").get_data(as_text=True)
    attrs = _details_attrs(html, "goal-coach-card")
    assert 'data-ai-key="coach"' in attrs
    assert _generated_value(attrs)
    assert " open" not in attrs
    assert "Coach collapse text." in html
    assert "on track" in html                      # headline figure


# --- empty state renders OPEN (Generate reachable without JS) ----------------

def test_uncached_cards_render_open_without_key(client_a, users, monkeypatch):
    a = users["a"]
    year, month = _prev_month()
    create_transaction(a["id"], a["account_id"], 30, date(year, month, 15),
                       transaction_type="expense", category_id=a["category_id"])
    create_transaction(a["id"], a["account_id"], 20, date.today(),
                       transaction_type="expense", category_id=a["category_id"])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    html = client_a.get("/").get_data(as_text=True)
    for card, button in (("insight-card", "Generate insight"),
                         ("forecast-card", "Generate forecast"),
                         ("agent-card", "Run now")):
        attrs = _details_attrs(html, card)
        assert " open" in attrs, f"{card} empty state must render open"
        assert _generated_value(attrs) is None, f"{card} has no read-state key yet"
        assert button in html


# --- the generate fragment: open + the DB timestamp --------------------------

def test_generate_fragment_open_and_timestamp_matches_next_load(client_a, users, monkeypatch):
    a = users["a"]
    year, month = _prev_month()
    create_transaction(a["id"], a["account_id"], 30, date(year, month, 15),
                       transaction_type="expense", category_id=a["category_id"])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(ai, "_call_insight_model",
                        _Seam(_Insight(summary="Fresh narration.", tips=[])))

    frag = client_a.post("/insights/generate",
                         data={"year": year, "month": month},
                         headers=HX).get_data(as_text=True)
    assert "<html" not in frag                     # still a fragment
    attrs = _details_attrs(frag, "insight-card")
    assert " open" in attrs                        # just generated → expanded
    generated = _generated_value(attrs)
    assert generated

    # The next dashboard load must render the SAME key (DB created_at, not a
    # route-local datetime.today()) — otherwise every regenerate would read as
    # "new" twice.
    html = client_a.get("/").get_data(as_text=True)
    assert f'data-generated="{generated}"' in html
    assert " open" not in _details_attrs(html, "insight-card")

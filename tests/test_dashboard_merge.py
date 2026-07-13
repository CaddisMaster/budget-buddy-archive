"""v10.9 Dashboard consolidation — /analytics merged into /dashboard.

Covers: the /analytics redirect (bookmarks live, month filter carried), the
moved Ask box, the two ported sections (spending by day of week, year over
year), and the tojson switch — chart data is HTML-escaped into the script
block, where the old json.dumps + |safe let a crafted category name break
out of it.
"""
from datetime import date

from tests.conftest import create_category, create_transaction


# --- the redirect -------------------------------------------------------------

def test_analytics_redirects_to_dashboard(client_a):
    response = client_a.get("/analytics")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")


def test_analytics_redirect_carries_month_filter(client_a):
    response = client_a.get("/analytics?month=2026-05")
    assert response.status_code == 302
    assert "/dashboard" in response.headers["Location"]
    assert "month=2026-05" in response.headers["Location"]


def test_analytics_redirect_for_anonymous_lands_on_login(anon_client, users):
    # The hop itself isn't auth-gated; /dashboard bounces anons to /login.
    response = anon_client.get("/analytics", follow_redirects=True)
    assert response.status_code == 200
    assert response.request.path == "/login"


def test_nav_has_no_analytics_link(client_a):
    response = client_a.get("/dashboard")
    assert b'href="/analytics"' not in response.data


def test_chartjs_is_vendored(client_a):
    # v10.13: Chart.js ships from /static/, not the jsdelivr CDN.
    response = client_a.get("/dashboard")
    assert b"chart.umd.min.js" in response.data
    assert b"cdn.jsdelivr" not in response.data


def test_charts_section_is_collapsible(client_a):
    # v10.13 de-scroll: the chart grid sits inside a <details> (open on
    # desktop, collapsed on mobile via JS); the canvases are still always
    # server-rendered.
    response = client_a.get("/dashboard")
    assert b'id="charts-details"' in response.data
    assert b'id="spendingPie"' in response.data


# --- the moved Ask box ----------------------------------------------------------

def test_ask_box_renders_on_dashboard(client_a, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    response = client_a.get("/dashboard")
    assert response.status_code == 200
    assert b"Ask your finances" in response.data


def test_ask_box_hidden_without_key(client_a, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    response = client_a.get("/dashboard")
    assert response.status_code == 200
    assert b"Ask your finances" not in response.data


# --- ported sections ------------------------------------------------------------

def test_day_of_week_chart_renders(client_a):
    # The seeded 42.50 expense is dated today, so today's day name must appear
    # in the chart payload (TO_CHAR 'Day' is blank-padded; the route strips it).
    response = client_a.get("/dashboard")
    assert response.status_code == 200
    assert b'id="dowBar"' in response.data
    assert date.today().strftime("%A").encode() in response.data


def test_yoy_card_shown_when_month_filtered_with_history(client_a, users):
    a = users["a"]
    today = date.today()
    last_year = today.replace(year=today.year - 1, day=1)
    create_transaction(a["id"], a["account_id"], 100.00, last_year,
                       category_id=a["category_id"])

    response = client_a.get(f"/dashboard?month={today.strftime('%Y-%m')}")
    assert response.status_code == 200
    assert b"Year over year" in response.data
    assert b"Same month last year" in response.data
    assert b"$100.00" in response.data


def test_yoy_absent_in_all_time_view(client_a, users):
    a = users["a"]
    today = date.today()
    create_transaction(a["id"], a["account_id"], 100.00,
                       today.replace(year=today.year - 1, day=1),
                       category_id=a["category_id"])

    response = client_a.get("/dashboard")
    assert response.status_code == 200
    assert b"Year over year" not in response.data


def test_yoy_absent_when_no_prior_year_data(client_a):
    # Month selected, but last year is empty — no card, not a $0.00 card.
    response = client_a.get(f"/dashboard?month={date.today().strftime('%Y-%m')}")
    assert response.status_code == 200
    assert b"Year over year" not in response.data


# --- income-by-category toggle (v10.12) --------------------------------------------

def test_income_toggle_renders_with_categorized_income(client_a, users):
    a = users["a"]
    inc = create_category(a["id"], "Salary", kind="income")
    create_transaction(a["id"], a["account_id"], 2500.00, date.today(),
                       transaction_type="income", category_id=inc)
    response = client_a.get("/dashboard")
    assert response.status_code == 200
    assert b'id="categoryChartToggle"' in response.data
    assert b"Salary" in response.data           # in the income payload
    assert b"2500.0" in response.data


def test_income_toggle_hidden_without_categorized_income(client_a, users):
    a = users["a"]
    # Uncategorized income exists, but the rollup JOINs categories — no payload,
    # no toggle (the fixture's seeded expense keeps the chart grid rendering).
    create_transaction(a["id"], a["account_id"], 900.00, date.today(),
                       transaction_type="income", category_id=None)
    response = client_a.get("/dashboard")
    assert response.status_code == 200
    assert b'id="categoryChartToggle"' not in response.data


def test_income_payload_respects_month_filter(client_a, users):
    # Income in another month -> empty income payload for the filtered view, so
    # the category name is absent and the toggle doesn't render. (The amount
    # itself still legitimately shows in the all-time net-balance trend.)
    a = users["a"]
    inc = create_category(a["id"], "Salary", kind="income")
    create_transaction(a["id"], a["account_id"], 1111.00, date(2025, 3, 15),
                       transaction_type="income", category_id=inc)
    response = client_a.get(f"/dashboard?month={date.today().strftime('%Y-%m')}")
    assert response.status_code == 200
    assert b"Salary" not in response.data
    assert b'id="categoryChartToggle"' not in response.data


def test_income_chart_json_escapes_script_breakout(client_a, users):
    # The income payload goes through |tojson like the other seven.
    a = users["a"]
    evil = "</script><script>alert(2)</script>"
    inc = create_category(a["id"], evil, kind="income")
    create_transaction(a["id"], a["account_id"], 7.00, date.today(),
                       transaction_type="income", category_id=inc)
    response = client_a.get("/dashboard")
    assert response.status_code == 200
    assert b"</script><script>alert(2)</script>" not in response.data
    assert b"\\u003c/script\\u003e" in response.data


# --- tojson hardening -------------------------------------------------------------

def test_chart_json_escapes_script_breakout(client_a, users):
    # A category named to close the script tag must not escape the JSON block.
    a = users["a"]
    evil = "</script><script>alert(1)</script>"
    cat_id = create_category(a["id"], evil)
    create_transaction(a["id"], a["account_id"], 5.00, date.today(),
                       category_id=cat_id)

    response = client_a.get("/dashboard")
    assert response.status_code == 200
    assert b"</script><script>alert(1)</script>" not in response.data
    # The name still made it into the payload — just \u-escaped by |tojson.
    assert b"\\u003c/script\\u003e" in response.data

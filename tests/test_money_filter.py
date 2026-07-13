"""v10.13 |money filter — display formatting with thousands separators.

The filter emits the NUMBER only ("1,234.56"); templates write the $ and any
sign styling around it. AI fact-builders and chart |tojson payloads keep raw
numbers — only display templates route through it.
"""
from datetime import date
from decimal import Decimal

from tests.conftest import create_transaction


def _money(app):
    return app.jinja_env.filters["money"]


# --- the filter itself ----------------------------------------------------------

def test_money_adds_thousands_separators(app):
    assert _money(app)(1234.5) == "1,234.50"
    assert _money(app)(1234.56) == "1,234.56"
    assert _money(app)(1234567.891) == "1,234,567.89"


def test_money_small_amounts_unchanged_shape(app):
    assert _money(app)(42.5) == "42.50"
    assert _money(app)(0) == "0.00"


def test_money_coerces_decimal(app):
    # DB rows carry numeric → Decimal; the filter must not choke on it.
    assert _money(app)(Decimal("9876.54")) == "9,876.54"


def test_money_negative(app):
    assert _money(app)(-1234.5) == "-1,234.50"


# --- rendered end-to-end ----------------------------------------------------------

def test_dashboard_renders_comma_separated_amounts(client_a, users):
    # Seed a ≥$1,000 expense; the hero Expenses figure (2000 + the 42.50 seed)
    # must render with a comma.
    create_transaction(
        users["a"]["id"], users["a"]["account_id"], 2000.00, date.today()
    )
    response = client_a.get("/")
    assert response.status_code == 200
    assert b"2,042.50" in response.data

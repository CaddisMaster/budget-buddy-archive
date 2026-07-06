"""Route smoke + auth tests.

Smoke: every main page returns 200 for a logged-in user (catches template/query
regressions). Auth: protected pages bounce anonymous users to /login, and the
login form accepts good credentials / rejects bad ones.
"""
import pytest

from tests.conftest import USER_A


PROTECTED_PAGES = [
    "/",
    "/dashboard",
    "/transactions",
    "/transactions/new",
    "/scheduled",
    "/categories",
    "/accounts",
    "/budgets",
    "/transfers",
    "/goals",
    "/profile",
]


@pytest.mark.parametrize("path", PROTECTED_PAGES)
def test_logged_in_user_gets_200(client_a, path):
    response = client_a.get(path)
    assert response.status_code == 200


@pytest.mark.parametrize("path", PROTECTED_PAGES)
def test_anonymous_user_redirected_to_login(anon_client, path):
    response = anon_client.get(path)
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_login_with_valid_credentials_redirects(anon_client, users):
    response = anon_client.post(
        "/login",
        data={"username": USER_A, "password": "test-password-123"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/login" not in response.headers["Location"]


def test_login_with_bad_password_stays_on_login(anon_client, users):
    response = anon_client.post(
        "/login",
        data={"username": USER_A, "password": "wrong-password"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Invalid username or password" in response.data


def test_dashboard_shows_hero_summary(client_a):
    """v10.6 Refresh — the dashboard renders the at-a-glance hero (net position +
    income/expenses) for a user with transactions."""
    response = client_a.get("/dashboard")
    assert response.status_code == 200
    assert b"Net position" in response.data
    # USER_A is seeded with a single $42.50 expense, so it surfaces in the hero.
    assert b"42.50" in response.data


def test_logout_redirects_to_login(client_a):
    response = client_a.get("/logout")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]

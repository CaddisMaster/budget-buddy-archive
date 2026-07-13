"""v10.13 PWA shell — manifest, service worker, icons, remember cookie.

The SW is served from a root route (main.service_worker) because a worker's
scope is capped at its URL's directory and installability requires control of
start_url '/'. The manifest lives in /static/ (fine for manifests) with an
explicit scope of '/'. Head tags are mirrored in login.html, which doesn't
extend base.html (the css_v lockstep precedent).
"""
import json


def test_sw_served_at_root_scope(anon_client):
    # Anon on purpose: the browser re-fetches the SW outside any session.
    response = anon_client.get("/sw.js")
    assert response.status_code == 200
    assert "javascript" in response.mimetype
    assert b"bb-static-" in response.data


def test_manifest_parses_with_root_start_url(anon_client):
    response = anon_client.get("/static/manifest.json")
    assert response.status_code == 200
    manifest = json.loads(response.data)
    assert manifest["start_url"] == "/"
    assert manifest["scope"] == "/"
    assert any(i["purpose"] == "maskable" for i in manifest["icons"])


def test_icons_exist(anon_client):
    for path in ("icons/icon.svg", "icons/icon-192.png", "icons/icon-512.png",
                 "icons/icon-maskable-512.png", "icons/apple-touch-icon.png"):
        assert anon_client.get(f"/static/{path}").status_code == 200


def test_pwa_head_tags_on_base_and_login(client_a, anon_client):
    # Both page shells (base.html and the standalone login.html) carry the
    # manifest + theme-color + icons.
    for response in (client_a.get("/"), anon_client.get("/login")):
        assert response.status_code == 200
        assert b'rel="manifest"' in response.data
        assert b'name="theme-color"' in response.data
        assert b'rel="apple-touch-icon"' in response.data


def test_sw_registration_in_base(client_a):
    response = client_a.get("/")
    assert b"serviceWorker" in response.data


def test_login_sets_remember_cookie(anon_client, users):
    # v10.13: login_user(remember=True) — an installed PWA shouldn't re-prompt
    # login every launch. The cookie flags live in __init__.py.
    from tests.conftest import USER_A, PASSWORD
    response = anon_client.post(
        "/login",
        data={"username": USER_A, "password": PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 302
    cookies = response.headers.getlist("Set-Cookie")
    assert any(c.startswith("remember_token=") for c in cookies)

import pytest
from fastapi.testclient import TestClient
from starlette.responses import RedirectResponse

from app.config import Config
from app.main import create_app
from tests.factory import FakeSource

SSO_FIELDS = dict(
    sso_enabled=True,
    oidc_issuer="https://issuer.test",
    oidc_client_id="tsugi",
    oidc_client_secret="secret",
    session_secret="test-session-secret",
    public_base_url=None,
)


class FakeOIDC:
    def __init__(self):
        self.last_redirect_uri = None

    async def authorize_redirect(self, request, redirect_uri):
        self.last_redirect_uri = redirect_uri
        return RedirectResponse("https://issuer.test/authorize?state=xyz", status_code=302)

    async def authorize_access_token(self, request):
        return {"access_token": "at", "userinfo": None}

    async def userinfo(self, token=None):
        return {"sub": "u1", "name": "Marco", "groups": ["admins"]}

    async def load_server_metadata(self):
        return {"end_session_endpoint": "https://issuer.test/logout"}


class FakeOAuth:
    def __init__(self):
        self.oidc = FakeOIDC()


def _sso_app(tmp_path):
    app = create_app(Config(data_dir=tmp_path / "web", **SSO_FIELDS),
                     sources={"fake": FakeSource({})})
    app.state.oauth = FakeOAuth()
    return app


@pytest.fixture
def sso_client(tmp_path):
    with TestClient(_sso_app(tmp_path)) as c:
        yield c


def test_incomplete_config_raises(tmp_path):
    with pytest.raises(RuntimeError):
        create_app(Config(data_dir=tmp_path / "w", sso_enabled=True),
                   sources={"fake": FakeSource({})})


def test_disabled_app_stays_open(tmp_path):
    app = create_app(Config(data_dir=tmp_path / "w"), sources={"fake": FakeSource({})})
    with TestClient(app) as c:
        assert c.get("/").status_code == 200


def test_unauth_browser_redirects(sso_client):
    r = sso_client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/auth/login"


def test_unauth_htmx_gets_hx_redirect(sso_client):
    r = sso_client.get("/", headers={"HX-Request": "true"}, follow_redirects=False)
    assert r.status_code == 401
    assert r.headers["HX-Redirect"] == "/auth/login"


def test_static_is_exempt(sso_client):
    assert sso_client.get("/static/app.css", follow_redirects=False).status_code == 200


def test_login_redirects_to_provider(sso_client):
    r = sso_client.get("/auth/login", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("https://issuer.test/authorize")


def test_callback_authenticates_then_routes_work(sso_client):
    r = sso_client.get("/auth/oidc/callback", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/"
    assert sso_client.get("/").status_code == 200  # session now carries the user


def test_callback_restores_original_destination(sso_client):
    sso_client.get("/library", follow_redirects=False)  # stashes next=/library
    r = sso_client.get("/auth/oidc/callback", follow_redirects=False)
    assert r.headers["location"].endswith("/library")


def test_logout_clears_session(sso_client):
    sso_client.get("/auth/oidc/callback", follow_redirects=False)  # log in
    r = sso_client.get("/auth/logout", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "https://issuer.test/logout"
    assert sso_client.get("/", follow_redirects=False).status_code == 302  # gated again


def test_settings_shows_user_and_signout(sso_client):
    sso_client.get("/auth/oidc/callback", follow_redirects=False)  # log in as Marco
    body = sso_client.get("/settings").text
    assert "Marco" in body
    assert "/auth/logout" in body

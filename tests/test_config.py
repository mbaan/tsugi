from pathlib import Path

from app.config import Config, load_config


def test_defaults_off_when_constructed_directly():
    cfg = Config(data_dir=Path("data"))
    assert cfg.sso_enabled is False
    assert cfg.oidc_issuer is None


def test_load_config_sso_on_by_default(monkeypatch):
    monkeypatch.delenv("DISABLE_SSO", raising=False)
    monkeypatch.setenv("OIDC_ISSUER", "https://sso.example.com")
    monkeypatch.setenv("OIDC_CLIENT_ID", "tsugi")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "shh")
    monkeypatch.setenv("SESSION_SECRET", "sign")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://tsugi.example.com")
    cfg = load_config()
    assert cfg.sso_enabled is True
    assert cfg.oidc_issuer == "https://sso.example.com"
    assert cfg.oidc_client_id == "tsugi"
    assert cfg.oidc_client_secret == "shh"
    assert cfg.session_secret == "sign"
    assert cfg.public_base_url == "https://tsugi.example.com"


def test_disable_sso_env_turns_it_off(monkeypatch):
    monkeypatch.setenv("DISABLE_SSO", "1")
    assert load_config().sso_enabled is False

"""OpenID Connect SSO: client setup, login/callback/logout routes, and a
middleware that gates the app. Vendor-neutral; configured entirely from env."""

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.config import Config

CALLBACK_PATH = "/auth/oidc/callback"
EXEMPT_PREFIXES = ("/auth/", "/static/")
EXEMPT_PATHS = {"/favicon.svg", "/favicon-32.png", "/apple-touch-icon.png"}


def build_oauth(cfg: Config) -> OAuth:
    oauth = OAuth()
    oauth.register(
        name="oidc",
        server_metadata_url=f"{cfg.oidc_issuer.rstrip('/')}/.well-known/openid-configuration",
        client_id=cfg.oidc_client_id,
        client_secret=cfg.oidc_client_secret,
        client_kwargs={"scope": "openid profile groups"},
    )
    return oauth


def current_user(request: Request) -> dict | None:
    if "session" not in request.scope:
        return None
    return request.session.get("user")


def _redirect_uri(request: Request, cfg: Config) -> str:
    if cfg.public_base_url:
        return cfg.public_base_url.rstrip("/") + CALLBACK_PATH
    return str(request.url_for("auth_oidc_callback"))


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        if (path in EXEMPT_PATHS or path.startswith(EXEMPT_PREFIXES)
                or request.session.get("user")):
            return await call_next(request)
        if request.headers.get("HX-Request") == "true":
            return Response(status_code=401, headers={"HX-Redirect": "/auth/login"})
        request.session["next"] = str(request.url)
        return RedirectResponse("/auth/login", status_code=302)


router = APIRouter()


@router.get("/auth/login")
async def auth_login(request: Request):
    oauth = request.app.state.oauth
    return await oauth.oidc.authorize_redirect(
        request, _redirect_uri(request, request.app.state.config))


@router.get(CALLBACK_PATH)
async def auth_oidc_callback(request: Request):
    oauth = request.app.state.oauth
    try:
        token = await oauth.oidc.authorize_access_token(request)
        info = await oauth.oidc.userinfo(token=token)
    except Exception:
        return RedirectResponse("/auth/login", status_code=302)
    request.session["user"] = {
        "sub": info.get("sub"),
        "name": info.get("name") or info.get("preferred_username") or info.get("sub"),
        "groups": info.get("groups") or [],
    }
    return RedirectResponse(request.session.pop("next", "/"), status_code=302)


@router.get("/auth/logout")
async def auth_logout(request: Request):
    request.session.pop("user", None)
    try:
        meta = await request.app.state.oauth.oidc.load_server_metadata()
        end = meta.get("end_session_endpoint")
    except Exception:
        end = None
    return RedirectResponse(end or "/", status_code=302)


def setup_auth(app, cfg: Config) -> None:
    """Validate config (fail closed), then wire session + gate + auth routes."""
    missing = [name for name, val in (
        ("OIDC_ISSUER", cfg.oidc_issuer),
        ("OIDC_CLIENT_ID", cfg.oidc_client_id),
        ("OIDC_CLIENT_SECRET", cfg.oidc_client_secret),
        ("SESSION_SECRET", cfg.session_secret),
    ) if not val]
    if missing:
        raise RuntimeError(
            f"SSO is enabled but missing config: {', '.join(missing)}. "
            f"Set them, or set DISABLE_SSO=1 to run without auth."
        )
    app.state.config = cfg
    app.state.oauth = build_oauth(cfg)
    app.add_middleware(AuthMiddleware)  # added first → inner (runs after session is set)
    app.add_middleware(
        SessionMiddleware,
        secret_key=cfg.session_secret,
        session_cookie="tsugi_session",
        same_site="lax",
        https_only=bool(cfg.public_base_url and cfg.public_base_url.startswith("https")),
        max_age=14 * 24 * 3600,
    )
    app.include_router(router)

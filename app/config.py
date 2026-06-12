import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    data_dir: Path
    sso_enabled: bool = False
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    session_secret: str | None = None
    public_base_url: str | None = None

    @property
    def catalog_path(self) -> Path:
        return self.data_dir / "catalog.sqlite"

    @property
    def archive_path(self) -> Path:
        return self.data_dir / "archive.sqlite"

    @property
    def covers_dir(self) -> Path:
        return self.data_dir / "covers"


def load_config() -> Config:
    return Config(
        data_dir=Path(os.environ.get("TSUGI_DATA", "data")),
        sso_enabled=os.environ.get("DISABLE_SSO") != "1",
        oidc_issuer=os.environ.get("OIDC_ISSUER") or None,
        oidc_client_id=os.environ.get("OIDC_CLIENT_ID") or None,
        oidc_client_secret=os.environ.get("OIDC_CLIENT_SECRET") or None,
        session_secret=os.environ.get("SESSION_SECRET") or None,
        public_base_url=os.environ.get("PUBLIC_BASE_URL") or None,
    )

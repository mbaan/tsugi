import os

os.environ.setdefault("DISABLE_SSO", "1")

import json
from pathlib import Path

import pytest

from app import db

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def catalog(tmp_path):
    conn = db.connect(tmp_path / "catalog.sqlite")
    db.init_catalog(conn)
    yield conn
    conn.close()


@pytest.fixture
def archive_db(tmp_path):
    conn = db.connect(tmp_path / "archive.sqlite")
    db.init_archive(conn)
    yield conn
    conn.close()


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def client(tmp_path):
    from fastapi.testclient import TestClient

    from app.config import Config
    from app.main import create_app
    from tests.factory import FakeSource

    app = create_app(Config(data_dir=tmp_path / "web"), sources={"fake": FakeSource({})})
    with TestClient(app) as c:
        c.app_ref = app
        yield c

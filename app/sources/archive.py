import json
import sqlite3
import zlib
from collections.abc import Iterator


class RawArchive:
    """Append-only store of every raw API response. Never deleted, never updated.

    Do not interleave iter_all() with store() on the same connection: rebuilds
    (the only iter_all consumer) run while no crawl is writing.
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def store(self, source: str, source_key: str, kind: str, payload: dict) -> None:
        blob = zlib.compress(json.dumps(payload, ensure_ascii=False).encode())
        self._conn.execute(
            "INSERT INTO source_raw(source, source_key, kind, payload_zlib) VALUES(?,?,?,?)",
            (source, source_key, kind, blob),
        )
        self._conn.commit()

    def latest(self, source: str, kind: str) -> dict | None:
        row = self._conn.execute(
            "SELECT payload_zlib FROM source_raw WHERE source=? AND kind=?"
            " ORDER BY id DESC LIMIT 1",
            (source, kind),
        ).fetchone()
        return json.loads(zlib.decompress(row[0])) if row else None

    def iter_all(self) -> Iterator[tuple[str, str, str, str, dict]]:
        cur = self._conn.execute(
            "SELECT source, source_key, kind, fetched_at, payload_zlib FROM source_raw ORDER BY id"
        )
        for source, key, kind, fetched_at, blob in cur:
            yield source, key, kind, fetched_at, json.loads(zlib.decompress(blob))

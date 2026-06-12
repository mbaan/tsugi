import re
import sqlite3


def fold_tag_name(name: str) -> str:
    """Matching key for near-duplicate tag names across sources.

    Lowercase, strip punctuation, drop the bare 's' token MU's '/s' suffix
    leaves behind, and fold simple trailing plurals ('ss' endings survive).
    """
    tokens = re.sub(r"[^a-z0-9]+", " ", name.lower()).split()
    folded = [
        t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t
        for t in tokens
        if t != "s"
    ]
    return " ".join(folded)


# Curated cross-source equivalences: (source, source_tag_name) -> canonical name.
# Everything not listed auto-creates a 1:1 canonical tag, so nothing is lost.
CURATED: dict[tuple[str, str], str] = {
    ("mangaupdates", "Dungeon/s"): "Dungeon",
    ("mangaupdates", "Dungeon/s Exploring"): "Dungeon",
    ("anilist", "Dungeon"): "Dungeon",
    ("mangaupdates", "Overpowered Protagonist"): "Overpowered Main Character",
    ("mangaupdates", "Overpowered Male Lead"): "Overpowered Main Character",
    ("mangaupdates", "Cultivation"): "Cultivation",
    ("anilist", "Cultivation"): "Cultivation",
    ("mangaupdates", "Regression"): "Time Regression",
    ("anilist", "Time Manipulation"): "Time Regression",
    ("mangaupdates", "Reincarnation"): "Reincarnation",
    ("anilist", "Reincarnation"): "Reincarnation",
    ("mangaupdates", "Game Elements"): "Game Elements",
    ("anilist", "Video Games"): "Game Elements",
    ("mangaupdates", "Full Color"): "Full Color",
    ("anilist", "Full Color"): "Full Color",
}

# Authoritative kind for canonicals merged from multiple sources: without this,
# tags.kind would depend on crawl arrival order (first creator wins).
CURATED_KINDS: dict[str, str] = {
    "Dungeon": "trope",
    "Overpowered Main Character": "trope",
    "Cultivation": "trope",
    "Time Regression": "trope",
    "Reincarnation": "trope",
    "Game Elements": "trope",
    "Full Color": "tag",
}


def canonical_tag_id(conn: sqlite3.Connection, source: str, name: str, kind: str,
                     category: str | None = None) -> int:
    row = conn.execute(
        "SELECT tag_id FROM tag_aliases WHERE source=? AND source_tag_name=?", (source, name)
    ).fetchone()
    if row:
        return row["tag_id"]

    canonical = CURATED.get((source, name), name)
    row = conn.execute("SELECT id FROM tags WHERE name=?", (canonical,)).fetchone()
    if not row:  # near-duplicate of an existing tag under a different spelling?
        row = conn.execute(
            "SELECT id FROM tags WHERE norm_name=? ORDER BY id LIMIT 1",
            (fold_tag_name(canonical),),
        ).fetchone()
    if row:
        tag_id = row["id"]
    else:
        tag_id = conn.execute(
            "INSERT INTO tags(name, kind, category, norm_name) VALUES(?,?,?,?)",
            (canonical, CURATED_KINDS.get(canonical, kind), category, fold_tag_name(canonical)),
        ).lastrowid
    conn.execute(
        "INSERT OR IGNORE INTO tag_aliases(source, source_tag_name, tag_id) VALUES(?,?,?)",
        (source, name, tag_id),
    )
    conn.commit()
    return tag_id

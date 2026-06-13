import math
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class Scored:
    work_id: int
    title: str
    type: str | None
    year: int | None
    status: str | None
    chapters: int | None
    quality: float
    cover_url: str | None
    cover_color: str | None
    first_seen_at: str | None
    score: float
    why: tuple[str, ...]


def _p95(values: list[int]) -> float:
    values = sorted(v for v in values if v > 0)  # downvoted edges carry no signal
    if not values:
        return 1.0
    # ceil, not int(): with few edges the p95 must land on the larger value,
    # otherwise every edge saturates at strength 1.0
    idx = min(len(values) - 1, math.ceil(0.95 * (len(values) - 1)))
    return float(values[idx]) or 1.0


def rating_affinity(overall: float) -> float:
    # Seed pull from a 1-10 rating (stars x2). Anchors: 4★(8)=+1.0 matches an
    # unrated seed, 3★(6) is neutral, 2★(4)=-0.5 matches a discard, and a
    # perfect 10 pulls extra hard. Linear between anchors, floored at -1.0.
    if overall >= 8:
        return 1.0 + (overall - 8) * 0.25
    if overall >= 6:
        return (overall - 6) * 0.5
    return max(-1.0, (overall - 6) * 0.25)


def recommend(conn: sqlite3.Connection, limit: int = 50, *, sort: str = "match",
              work_type: str | None = None, min_quality: float | None = None,
              with_skipped: bool = False):
    """Top recommendations; with_skipped=True also returns how many works scored
    but sit below the quality tier (so the UI can say what the gate is hiding)."""
    cfg = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
    gate = float(cfg["quality_gate"])
    w_sim = float(cfg["w_similarity"])
    w_tropes = float(cfg["w_tropes"])
    w_quality = float(cfg["w_quality"])
    floor = float(cfg["require_floor"])
    show_adult = cfg.get("show_adult") == "1"
    excl_franchise = cfg.get("exclude_seed_franchise") == "1"
    discard_affinity = float(cfg["discard_affinity"])
    discard_tag_weight = float(cfg["discard_tag_weight"])
    seed_all_read = cfg.get("seed_all_read") == "1"

    affinities: dict[int, float] = {}
    stored_affinities: dict[int, float] = {}
    if seed_all_read:
        # mutex shortcut: every Read item is a seed at normal strength; a rating
        # tunes the pull (5★ harder, ≤2★ becomes an anti-seed). Manual seeds ignored.
        rows = conn.execute(
            "SELECT ul.work_id, rt.overall FROM user_list ul"
            " LEFT JOIN ratings rt ON rt.work_id=ul.work_id WHERE ul.status='read'"
        )
        for r in rows:
            stored_affinities[r["work_id"]] = 1.0
            affinities[r["work_id"]] = (
                rating_affinity(r["overall"]) if r["overall"] is not None else 1.0
            )
    else:
        for r in conn.execute(
            "SELECT s.work_id, s.affinity, rt.overall FROM seeds s"
            " LEFT JOIN ratings rt ON rt.work_id=s.work_id"
        ):
            stored_affinities[r["work_id"]] = r["affinity"]
            affinities[r["work_id"]] = (
                rating_affinity(r["overall"]) if r["overall"] is not None else r["affinity"]
            )
    seed_titles = {}
    for r in conn.execute(
        "SELECT work_id FROM user_list WHERE status='discarded'"
    ).fetchall():
        affinities.setdefault(r["work_id"], discard_affinity)
        # never read while discard_affinity < 0 (derived gate filters it); kept so a
        # positive misconfig degrades to dilution instead of a KeyError
        stored_affinities.setdefault(r["work_id"], discard_affinity)
    if affinities:
        ids = ",".join("?" * len(affinities))
        seed_titles = {r["id"]: r["canonical_title"] for r in conn.execute(
            f"SELECT id, canonical_title FROM works WHERE id IN ({ids})",
            tuple(affinities))}

    p95s = {r["source"]: _p95([v["votes"] for v in conn.execute(
        "SELECT votes FROM similarities WHERE source=?", (r["source"],))])
        for r in conn.execute("SELECT DISTINCT source FROM similarities")}

    # strongest edge per (seed, candidate) across sources and directions
    edges: dict[tuple[int, int], tuple[float, int]] = {}  # -> (strength, raw_votes)
    if affinities:
        ids = ",".join("?" * len(affinities))
        params = tuple(affinities) * 2
        for r in conn.execute(
            f"SELECT from_work_id f, to_work_id t, source, votes FROM similarities"
            f" WHERE from_work_id IN ({ids}) OR to_work_id IN ({ids})", params,
        ):
            seed_id, cand = (r["f"], r["t"]) if r["f"] in affinities else (r["t"], r["f"])
            if cand in affinities:
                continue
            votes = max(0, r["votes"])  # AniList rec ratings go negative when downvoted
            strength = min(1.0, math.log1p(votes) / math.log1p(p95s[r["source"]]))
            if edges.get((seed_id, cand), (0.0, 0))[0] < strength:
                edges[(seed_id, cand)] = (strength, votes)

    # trope weights: explicit chips + discard-feedback tags as negatives
    requires: dict[int, float] = {}
    excludes: set[int] = set()
    boosts: dict[int, float] = {}
    tag_names: dict[int, str] = {}
    for r in conn.execute(
        "SELECT tw.tag_id, tw.mode, tw.weight, t.name FROM trope_weights tw"
        " JOIN tags t ON t.id=tw.tag_id"
    ):
        tag_names[r["tag_id"]] = r["name"]
        if r["mode"] == "require":
            requires[r["tag_id"]] = r["weight"]
        elif r["mode"] == "exclude":
            excludes.add(r["tag_id"])
        else:
            boosts[r["tag_id"]] = r["weight"]
    for r in conn.execute(
        "SELECT DISTINCT df.tag_id, t.name FROM discard_feedback df"
        " JOIN tags t ON t.id=df.tag_id WHERE df.tag_id IS NOT NULL"
    ):
        if r["tag_id"] in requires or r["tag_id"] in excludes:
            continue  # explicit user chips outrank discard-derived negatives
        boosts[r["tag_id"]] = boosts.get(r["tag_id"], 0.0) + discard_tag_weight
        tag_names[r["tag_id"]] = r["name"]

    seed_franchises = set()
    if excl_franchise:
        positive = [s for s, a in affinities.items() if a > 0]
        if positive:
            ids = ",".join("?" * len(positive))
            seed_franchises = {r["franchise_id"] for r in conn.execute(
                f"SELECT franchise_id FROM works WHERE id IN ({ids})", tuple(positive))}

    effective_gate = gate if min_quality is None else min_quality
    type_sql, type_params = "", []
    if work_type:
        type_sql, type_params = " AND w.type=?", [work_type]
    candidates = conn.execute(
        "SELECT * FROM works w WHERE w.is_stub=0 AND w.quality IS NOT NULL"
        " AND w.id NOT IN (SELECT work_id FROM user_list)"
        " AND w.id NOT IN (SELECT work_id FROM seeds)"
        " AND (? OR w.is_adult=0)" + type_sql,
        (int(show_adult), *type_params),
    ).fetchall()

    scoring_tags = {**{t: w for t, w in requires.items()}, **boosts}
    total_trope_weight = sum(abs(w) for w in scoring_tags.values()) or 1.0
    # normalize by stored mass of seeds currently pulling positive: keeps the
    # perfect-score boost (sole rated seed) while anti-seeds, like discards,
    # stay out of the denominator
    pos_affinity = sum(stored_affinities[k] for k, a in affinities.items() if a > 0) or 1.0

    # Per-candidate tag weights, but only for the tags any filter or score actually
    # reads (requires/excludes/boosts), bulk-loaded in one query keyed by work. The
    # old code ran one work_tags query per candidate — an N+1 that cost ~2.5s on the
    # Pi for thousands of candidates. With no tropes set, relevant_tags is empty and
    # we skip the query entirely; merged.get() below tolerates the empty dict.
    relevant_tags = set(requires) | excludes | set(scoring_tags)
    merged_by_work: dict[int, dict[int, float]] = {}
    if relevant_tags:
        ids = ",".join("?" * len(relevant_tags))
        for r in conn.execute(
            f"SELECT work_id, tag_id, AVG(weight) mw FROM work_tags"
            f" WHERE tag_id IN ({ids}) GROUP BY work_id, tag_id", tuple(relevant_tags)):
            merged_by_work.setdefault(r["work_id"], {})[r["tag_id"]] = r["mw"]

    results: list[Scored] = []
    below_gate = 0
    for w in candidates:
        if w["franchise_id"] is not None and w["franchise_id"] in seed_franchises:
            continue
        merged = merged_by_work.get(w["id"], {})
        if any(merged.get(t, 0.0) < floor for t in requires):
            continue
        if any(merged.get(t, 0.0) >= floor for t in excludes):
            continue

        why: list[str] = []
        sim = 0.0
        for seed_id, affinity in affinities.items():
            strength, votes = edges.get((seed_id, w["id"]), (0.0, 0))
            if strength:
                sim += affinity * strength
                if affinity > 0:
                    why.append(f"{votes:,} votes from {seed_titles.get(seed_id, '?')}")

        trope_score = 0.0
        for tag_id, weight in scoring_tags.items():
            mw = merged.get(tag_id, 0.0)
            if mw:
                trope_score += mw * weight
                if weight > 0:
                    why.append(f"{tag_names[tag_id]} {mw:.0%}")

        quality_score = (w["quality"] - gate) / (10 - gate)
        score = (w_sim * sim / pos_affinity
                 + w_tropes * trope_score / total_trope_weight
                 + w_quality * quality_score)
        if score <= 0:
            continue
        if w["quality"] < effective_gate:
            below_gate += 1  # would be recommended, hidden by the quality tier
            continue
        why.append(f"quality {w['quality']:.1f}")
        results.append(Scored(
            work_id=w["id"], title=w["canonical_title"], type=w["type"], year=w["year"],
            status=w["status"], chapters=w["chapters"],
            quality=w["quality"], cover_url=w["cover_url"], cover_color=w["cover_color"],
            first_seen_at=w["first_seen_at"], score=score, why=tuple(why),
        ))

    # score picks the top-N; a non-default sort then reorders that page
    results.sort(key=lambda s: -s.score)
    results = results[:limit]
    if sort == "quality":
        results.sort(key=lambda s: -(s.quality or 0))
    elif sort == "year":
        results.sort(key=lambda s: s.year or 0, reverse=True)
    elif sort == "added":
        results.sort(key=lambda s: s.first_seen_at or "", reverse=True)
    elif sort == "title":
        results.sort(key=lambda s: s.title.lower())
    if with_skipped:
        return results, below_gate
    return results

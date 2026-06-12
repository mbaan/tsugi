from dataclasses import dataclass, field


@dataclass(frozen=True)
class TagVote:
    name: str
    kind: str  # genre | tag | trope
    weight: float  # 0..1
    votes: int | None = None
    category: str | None = None


@dataclass(frozen=True)
class SimilarRef:
    source: str
    source_key: str
    title: str
    votes: int


@dataclass(frozen=True)
class RelationRef:
    source: str
    source_key: str
    title: str
    rel_type: str


@dataclass(frozen=True)
class SourceHit:
    source: str
    source_key: str
    title: str
    year: int | None
    type: str | None
    score: float | None
    cover_url: str | None = None


@dataclass(frozen=True)
class WorkPayload:
    source: str
    source_key: str
    url: str
    titles: dict[str, tuple[str, ...]] = field(default_factory=dict)
    type: str | None = None
    year: int | None = None
    status: str | None = None
    description: str | None = None
    cover_url: str | None = None
    banner_url: str | None = None
    cover_color: str | None = None
    is_adult: bool = False
    score: float | None = None  # normalized 0..10
    score_votes: int = 0
    tags: tuple[TagVote, ...] = ()
    similar: tuple[SimilarRef, ...] = ()
    relations: tuple[RelationRef, ...] = ()
    links: tuple[tuple[str, str], ...] = ()

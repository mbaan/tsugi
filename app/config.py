import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    data_dir: Path

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
    return Config(data_dir=Path(os.environ.get("TSUGI_DATA", "data")))

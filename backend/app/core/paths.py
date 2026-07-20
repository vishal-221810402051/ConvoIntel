"""Deterministic filesystem paths for the Convointel backend."""

from pathlib import Path


def get_repository_root() -> Path:
    """Resolve the repository root from this module location."""

    return Path(__file__).resolve().parents[3]


def resolve_repository_path(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve(strict=False)
    return (get_repository_root() / candidate).resolve(strict=False)


def default_data_dir() -> Path:
    return (get_repository_root() / "data").resolve(strict=False)

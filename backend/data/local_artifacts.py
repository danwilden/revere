"""Local filesystem implementation of ArtifactRepository for dev/test use."""
from __future__ import annotations

from pathlib import Path

from backend.data.repositories import ArtifactRepository


class LocalArtifactRepository(ArtifactRepository):
    """Stores artifacts as files under a local base directory."""

    def __init__(self, base_path: str | Path) -> None:
        self._base = Path(base_path)
        self._base.mkdir(parents=True, exist_ok=True)

    def save(self, key: str, data: bytes) -> str:
        dest = self._base / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return str(dest)

    def _resolve(self, ref: str) -> Path:
        p = Path(ref)
        return p if p.is_absolute() else self._base / ref

    def load(self, ref: str) -> bytes:
        return self._resolve(ref).read_bytes()

    def exists(self, ref: str) -> bool:
        return self._resolve(ref).exists()

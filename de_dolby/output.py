"""Transactional staging and atomic publication of conversion outputs."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path


def staging_path_for(destination: str | Path) -> Path:
    """Return a unique sibling staging path preserving the media suffix."""
    output = Path(destination)
    token = uuid.uuid4().hex
    return output.with_name(
        f".{output.stem}.de-dolby-{token}.staging{output.suffix}"
    )


@dataclass
class OutputTransaction:
    """Own one staged output until it is published or discarded."""

    destination: Path
    force: bool = False
    staging_path: Path = field(init=False)
    _published: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self.destination = Path(self.destination)
        self.staging_path = staging_path_for(self.destination)

    def publish(self) -> None:
        """Durably publish a complete staging file at the destination."""
        if not self.staging_path.is_file():
            raise RuntimeError(
                f"Staged output does not exist: {self.staging_path}"
            )

        try:
            # Windows FlushFileBuffers requires a write-capable handle.
            with self.staging_path.open("rb+") as staged:
                os.fsync(staged.fileno())
            if self.force:
                os.replace(self.staging_path, self.destination)
            else:
                # A hard link atomically fails if destination already exists.
                os.link(self.staging_path, self.destination)
                self.staging_path.unlink()
        except FileExistsError as exc:
            raise RuntimeError(
                f"Output file already exists: {self.destination} "
                "(use --force to overwrite)"
            ) from exc
        except OSError as exc:
            raise RuntimeError(
                f"Could not atomically publish {self.destination}: {exc}"
            ) from exc
        self._published = True

    def discard(self) -> None:
        """Remove an unpublished staging file, if present."""
        if not self._published:
            try:
                self.staging_path.unlink(missing_ok=True)
            except OSError:
                pass

    def __enter__(self) -> OutputTransaction:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is not None or not self._published:
            self.discard()

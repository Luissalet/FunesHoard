"""The `Probe` interface every platform collector implements."""
from __future__ import annotations

from typing import Protocol

from funes_hoard.core.spans import Sample


class Probe(Protocol):
    def sample(self) -> Sample:
        """Return one sample. Must never raise -- catch everything and
        return a best-effort/degraded sample instead (e.g. app="unknown")."""
        ...

    def status(self) -> str:
        """Short human string describing whether this probe is fully
        functional on this platform (used in the Privacy screen)."""
        ...

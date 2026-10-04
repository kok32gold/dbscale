"""Progress reporting hooks so the CLI (or anything else) can show what is happening."""

from __future__ import annotations

from typing import Protocol


class ProgressListener(Protocol):
    def start(self, message: str) -> None: ...

    def done(self, message: str) -> None: ...

    def fail(self, message: str) -> None: ...

    def note(self, message: str) -> None: ...

    def progress(self, message: str) -> None:
        """Transient sub-step update (e.g. '  orders 3/10M rows')."""


class NullProgress:
    def start(self, message: str) -> None:
        return None

    def done(self, message: str) -> None:
        return None

    def fail(self, message: str) -> None:
        return None

    def note(self, message: str) -> None:
        return None

    def progress(self, message: str) -> None:
        return None

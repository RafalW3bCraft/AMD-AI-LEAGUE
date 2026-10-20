"""Monotonic deadline helper.

The Match 4 grader likely runs each query under a hard wall-clock cap
(default assumption ~30 s, inherited from Match 3). Without a deadline,
a slow upstream or a 5-link discovery chain can exceed it silently.

The ``Deadline`` class is a wall-clock budget that propagates through
search/fetch/verify. Each subsystem asks ``deadline.remaining_s()`` and
stops when remaining < its own reserve. We always reserve a few seconds
for output writing, so even if we hit zero mid-fetch the result JSON
still gets written.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class Deadline:
    """Monotonic deadline.

    Parameters
    ----------
    total_s:
        Total wall-clock budget for the query.
    reserve_s:
        Time reserved for output writing (must always be available at
        the end of the pipeline).
    """

    total_s: float = 28.0
    reserve_s: float = 2.0
    _start: float = 0.0

    def __post_init__(self) -> None:
        self._start = time.monotonic()

    @classmethod
    def from_env(cls, env_var: str = "MATCH4_QUERY_DEADLINE_S",
                 default: float = 28.0) -> "Deadline":
        import os
        try:
            return cls(total_s=float(os.environ.get(env_var, default)))
        except (TypeError, ValueError):
            return cls(total_s=default)

    def remaining_s(self) -> float:
        """Seconds remaining, excluding the reserve."""
        elapsed = time.monotonic() - self._start
        return max(0.0, self.total_s - self.reserve_s - elapsed)

    def exceeded(self) -> bool:
        return self.remaining_s() <= 0.0

    def with_cap(self, requested_s: float) -> float:
        """Return ``min(requested_s, remaining)`` for per-call timeouts.

        Never returns more than the remaining budget (minus reserve)."""
        return max(0.0, min(requested_s, self.remaining_s()))

    def deadline_ts(self) -> float:
        """Return the absolute monotonic deadline timestamp."""
        return self._start + self.total_s - self.reserve_s

    def elapsed_s(self) -> float:
        return time.monotonic() - self._start
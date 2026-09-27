"""Remember which apps you actually launch.

A launcher that only ever sorts alphabetically makes you hunt for the three
things you use.  This keeps a small JSON file of launch counts and which entry
was last chosen, so the common case is one keystroke away.

The file lives in $XDG_STATE_HOME (not $XDG_CACHE_HOME) because losing it to a
cache sweep would silently reset the history.

    {"version": 1, "last": "firefox.desktop", "counts": {"firefox.desktop": 12}}

Entries are keyed by desktop id, not display name: names are localised and can
change with your locale, ids cannot.
"""

from __future__ import annotations

import json
import os
import tempfile

VERSION = 1

# How many dots the most-used app gets.  The scale runs down from here and
# every app keeps at least one dot, so an unused entry still reads as present
# rather than absent.
MAX_DOTS = 6


def state_path() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, "secretsway", "usage.json")


def dots_for(rank: int, max_dots: int = MAX_DOTS) -> int:
    """Dots for the app at `rank` in frequency order.

    First place gets `max_dots`, and each rank below loses one, bottoming out
    at a single dot so that "used once" and "never used" are both still drawn.
    """
    return max(1, max_dots - max(0, rank))


class Usage:
    """Launch counts and the most recent pick, backed by a JSON file."""

    def __init__(self, counts: dict | None = None, last: str = "", path: str | None = None):
        self.counts: dict[str, int] = {str(k): int(v) for k, v in (counts or {}).items()
                                       if isinstance(v, (int, float))}
        self.last: str = last or ""
        self.path = path if path is not None else state_path()

    # --- persistence -------------------------------------------------------

    @classmethod
    def load(cls, path: str | None = None) -> "Usage":
        """Read the file, treating anything unreadable as "no history yet".

        A corrupt or hand-edited file must never stop the launcher from opening,
        so failures degrade to empty rather than raising.
        """
        path = path if path is not None else state_path()
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return cls(path=path)
        if not isinstance(data, dict):
            return cls(path=path)
        counts = data.get("counts")
        last = data.get("last")
        return cls(
            counts=counts if isinstance(counts, dict) else {},
            last=last if isinstance(last, str) else "",
            path=path,
        )

    def save(self) -> bool:
        """Write atomically: a launcher killed mid-write must not truncate.

        Returns False on failure -- losing history is not worth interrupting a
        launch over, so the caller is free to ignore it.
        """
        payload = {
            "version": VERSION,
            "last": self.last,
            "counts": dict(sorted(self.counts.items())),
        }
        directory = os.path.dirname(self.path)
        try:
            os.makedirs(directory, exist_ok=True)
            handle = tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=directory,
                prefix=".usage-", suffix=".tmp", delete=False,
            )
            try:
                with handle:
                    json.dump(payload, handle)
                os.replace(handle.name, self.path)
            except BaseException:
                try:
                    os.unlink(handle.name)
                except OSError:
                    pass
                raise
            return True
        except (OSError, TypeError, ValueError):
            return False

    # --- recording ---------------------------------------------------------

    def record(self, desktop_id: str) -> None:
        if not desktop_id:
            return
        self.counts[desktop_id] = self.counts.get(desktop_id, 0) + 1
        self.last = desktop_id

    def count(self, desktop_id: str) -> int:
        return self.counts.get(desktop_id, 0)

    def last_id(self) -> str:
        return self.last

    def total(self) -> int:
        return sum(self.counts.values())

    # --- ordering ----------------------------------------------------------

    def order(self, apps) -> list:
        """Apps by descending use, ties broken by name for a stable list.

        Apps you have never launched all sit at the back with equal counts, so
        the name tiebreak keeps them alphabetical rather than in whatever order
        the filesystem handed us.
        """
        decorated = [(self.count(a.desktop_id), a.name.lower(), a) for a in apps]
        decorated.sort(key=lambda t: (-t[0], t[1]))
        return [t[2] for t in decorated]

    def frequency_ranks(self, apps) -> dict:
        """desktop_id -> 0-based position in frequency order.

        Computed over the whole catalogue, not the current result set, so an
        app's dots mean the same thing regardless of what you have typed.
        """
        return {app.desktop_id: i for i, app in enumerate(self.order(apps))}

    def promote_last(self, ordered: list) -> list:
        """Move the most recent pick to the front, if it still exists.

        With a 187-entry list the app you launched a moment ago is almost
        always the one you want next; making it slot 1 is the whole point.
        """
        if not self.last:
            return ordered
        for i, app in enumerate(ordered):
            if app.desktop_id == self.last:
                if i:
                    ordered = [ordered[i]] + ordered[:i] + ordered[i + 1:]
                return ordered
        return ordered

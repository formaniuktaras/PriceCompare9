"""Persistence helpers for comparison table state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, Tuple


class ComparisonStateStore:
    """Store persistent state for comparison matches.

    The store keeps track of user actions such as confirming, flagging or
    removing matches between the shop price list and supplier items. Data is
    persisted to a JSON file so that the interface can restore state between
    sessions.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._matches: Dict[str, Dict[str, str]] = {}
        self._load()

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------
    def _load(self) -> None:
        if not self.path.exists():
            self._matches = {}
            return

        try:
            with self.path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            self._matches = {}
            return

        matches = payload.get("matches") if isinstance(payload, dict) else None
        if not isinstance(matches, dict):
            self._matches = {}
            return

        parsed: Dict[str, Dict[str, str]] = {}
        for group_id, group_payload in matches.items():
            if not isinstance(group_payload, dict):
                continue
            parsed[group_id] = {}
            for match_id, status in group_payload.items():
                if isinstance(status, str) and status:
                    parsed[group_id][match_id] = status

        self._matches = parsed

    def save(self) -> None:
        payload = {"matches": self._matches}
        with self.path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def status_for(self, group_id: str, match_id: str) -> str | None:
        """Return stored status for the supplied match, if available."""

        group = self._matches.get(group_id)
        if not group:
            return None
        return group.get(match_id)

    def set_status(self, group_id: str, match_id: str, status: str) -> None:
        """Persist a status for the specified match."""

        group = self._matches.setdefault(group_id, {})
        if status:
            group[match_id] = status
        else:
            group.pop(match_id, None)
            if not group:
                self._matches.pop(group_id, None)

    def clear_status(self, group_id: str, match_id: str) -> None:
        """Remove persisted status information for a match."""

        group = self._matches.get(group_id)
        if not group:
            return
        group.pop(match_id, None)
        if not group:
            self._matches.pop(group_id, None)

    def iter_statuses(self) -> Iterable[Tuple[str, str, str]]:
        """Yield tuples of (group_id, match_id, status)."""

        for group_id, matches in self._matches.items():
            for match_id, status in matches.items():
                yield group_id, match_id, status

    def prune(self, valid_pairs: Iterable[Tuple[str, str]]) -> None:
        """Remove stale statuses not present in ``valid_pairs``.

        The comparison table may change when new price lists are imported. To
        avoid keeping obsolete state around, the board provides the list of
        currently visible (group_id, match_id) pairs so that the store can drop
        entries that no longer exist.
        """

        valid = set(valid_pairs)
        to_remove: Dict[str, Dict[str, str]] = {}
        for group_id, matches in list(self._matches.items()):
            filtered = {
                match_id: status
                for match_id, status in matches.items()
                if (group_id, match_id) in valid
            }
            if filtered:
                to_remove[group_id] = filtered
        self._matches = to_remove


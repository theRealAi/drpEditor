"""Patch objects: an audit trail for every modification.

Every mutation made through :class:`~drp_editor.models.Project` records a
:class:`Patch`. Patches are JSON-serializable so a session's changes can
be exported, reviewed, replayed against another project, or (via
``Project.undo_last``) reverted.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .exceptions import PatchError

__all__ = ["Patch", "PatchLog"]


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


@dataclass(frozen=True, slots=True)
class Patch:
    """One recorded modification.

    Attributes:
        object_type: ``"clip"``, ``"timeline"``, ``"mediaitem"``, ...
        object_id: UUID of the modified object.
        property: Property name; blob edits use ``"fields_blob.<field>"``.
        old_value: Previous value (hex string for blob edits).
        new_value: New value (hex string for blob edits).
        timestamp: ISO-8601 UTC time the patch was recorded.
    """

    object_type: str
    object_id: str
    property: str
    old_value: str
    new_value: str
    timestamp: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly representation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Patch:
        """Rebuild a patch from :meth:`to_dict` output."""
        try:
            return cls(**data)
        except TypeError as exc:
            raise PatchError(f"malformed patch record: {data!r}") from exc


class PatchLog:
    """Ordered log of patches recorded during a session."""

    def __init__(self) -> None:
        self._patches: list[Patch] = []

    def record(
        self,
        *,
        object_type: str,
        object_id: str,
        property: str,
        old_value: str,
        new_value: str,
    ) -> Patch:
        """Create, store, and return a new patch."""
        patch = Patch(
            object_type=object_type,
            object_id=object_id,
            property=property,
            old_value=old_value,
            new_value=new_value,
        )
        self._patches.append(patch)
        return patch

    def pop(self) -> Patch | None:
        """Remove and return the most recent patch (for undo)."""
        return self._patches.pop() if self._patches else None

    def __len__(self) -> int:
        return len(self._patches)

    def __iter__(self) -> Any:
        return iter(self._patches)

    @property
    def patches(self) -> list[Patch]:
        """A copy of all recorded patches, oldest first."""
        return list(self._patches)

    def to_json(self) -> str:
        """Serialize the whole log as a JSON array."""
        return json.dumps([p.to_dict() for p in self._patches], indent=2)

    def save(self, path: Path | str) -> None:
        """Write the log to a JSON file."""
        Path(path).write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: Path | str) -> PatchLog:
        """Load a log previously written by :meth:`save`."""
        log = cls()
        try:
            records = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PatchError(f"cannot load patch log {path}: {exc}") from exc
        for record in records:
            log._patches.append(Patch.from_dict(record))
        return log

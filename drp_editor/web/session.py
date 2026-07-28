"""Session state for the web UI: one open project per server process.

The UI is a local, single-user tool, so state is a plain object rather
than a database. Objects are addressed by *token*: their UUID when they
have one, otherwise a deterministic fallback id assigned in traversal
order (rebuilt after every mutation).
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ..exceptions import DRPError
from ..models import Clip, MediaItem, Project, Timeline
from ..parser import DRPParser

__all__ = ["Session"]

logger = logging.getLogger(__name__)


@dataclass
class Session:
    """Holds the currently open project and its saved versions."""

    project: Project | None = None
    source_path: Path | None = None
    versions: list[Path] = field(default_factory=list)
    _tokens: dict[str, Clip | Timeline | MediaItem] = field(default_factory=dict)

    # -- lifecycle ------------------------------------------------------

    def open(self, path: Path) -> Project:
        """Open a .drp from disk, replacing any previous session."""
        from .. import open_project

        project = open_project(path)
        self.project = project
        self.source_path = path
        self.versions = []
        self.reindex()
        return project

    def open_bytes(self, data: bytes, filename: str) -> Project:
        """Open an uploaded .drp; a temp copy becomes the source path."""
        from ..archive import DRPArchive

        tmp_dir = Path(tempfile.mkdtemp(prefix="drp_editor_"))
        source = tmp_dir / (Path(filename).name or "uploaded.drp")
        source.write_bytes(data)
        archive = DRPArchive.open(source)
        project = DRPParser().parse(archive)
        self.project = project
        self.source_path = source
        self.versions = []
        self.reindex()
        return project

    def require_project(self) -> Project:
        """The open project, or a DRPError if none is open."""
        if self.project is None:
            raise DRPError("no project is open")
        return self.project

    # -- object addressing -------------------------------------------------

    def reindex(self) -> None:
        """Rebuild the token -> object map after any mutation."""
        self._tokens.clear()
        project = self.require_project()
        for i, timeline in enumerate(project.timelines):
            self._tokens[timeline.uuid or f"timeline#{i}"] = timeline
        for i, clip in enumerate(project.all_clips()):
            self._tokens[clip.uuid or f"clip#{i}"] = clip
        for i, item in enumerate(project.media_pool):
            self._tokens[item.uuid or f"media#{i}"] = item

    def token_of(self, obj: Clip | Timeline | MediaItem) -> str:
        """Token addressing *obj* (inverse of :meth:`resolve`)."""
        for token, candidate in self._tokens.items():
            if candidate is obj:
                return token
        raise DRPError(f"object {obj!r} is not indexed")

    def resolve(self, token: str) -> Clip | Timeline | MediaItem:
        """Look up an object by token."""
        obj = self._tokens.get(token)
        if obj is None:
            raise DRPError(f"unknown object {token!r} (stale view? reload the project)")
        return obj

    # -- versioned saving ---------------------------------------------------

    def next_version_path(self) -> Path:
        """First free ``<stem>_vN.drp`` next to the source file."""
        assert self.source_path is not None
        stem = self.source_path.stem
        # Strip an existing _vN suffix so versions don't stack up in the name.
        base = stem.rsplit("_v", 1)[0] if stem.rsplit("_v", 1)[-1].isdigit() else stem
        n = len(self.versions) + 2
        while True:
            candidate = self.source_path.with_name(f"{base}_v{n}.drp")
            if not candidate.exists():
                return candidate
            n += 1

    def save_version(self) -> Path:
        """Save the current state as the next version file."""
        project = self.require_project()
        target = self.next_version_path()
        project.save(target)
        self.versions.append(target)
        logger.info("saved version %s", target)
        return target

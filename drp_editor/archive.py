"""Archive layer: open, extract, verify, and rebuild .drp containers.

A ``.drp`` file exported by DaVinci Resolve is a ZIP archive containing
the project XML plus auxiliary payloads. This layer knows nothing about
project semantics; it deals purely in named byte payloads.

Byte-preservation guarantees
----------------------------
* If **no** member was replaced or added, :meth:`DRPArchive.save` writes
  the original file bytes verbatim -- a perfect byte-for-byte copy.
* If members were changed, the archive is rebuilt keeping member order,
  per-member compression type, timestamps, external attributes, and
  comments. Untouched members keep their exact decompressed content
  (compressed bytes may differ because zlib output is not canonical,
  which ZIP readers -- including Resolve -- do not care about).

Robustness
----------
Some tools hand around bare project XML with a ``.drp`` extension. When
the input is not a ZIP but parses as XML, it is wrapped as a synthetic
single-member archive (member name :data:`RAW_XML_MEMBER`) and saved back
as bare XML, so the rest of the stack is agnostic to the container.
"""

from __future__ import annotations

import io
import logging
import zipfile
import zlib
from pathlib import Path
from types import TracebackType

from .exceptions import ArchiveError, SaveError

__all__ = ["RAW_XML_MEMBER", "DRPArchive"]

logger = logging.getLogger(__name__)

#: Synthetic member name used when the .drp is a bare XML file.
RAW_XML_MEMBER = "project.xml"


class DRPArchive:
    """A .drp container with lazy member loading and dirty tracking.

    Use :meth:`open` to load from disk or :meth:`from_bytes` for
    in-memory data. Instances are context managers::

        with DRPArchive.open("project.drp") as archive:
            data = archive.read("project.xml")
    """

    def __init__(self, raw: bytes, *, path: Path | None = None) -> None:
        self._raw = raw
        self._path = path
        self._replaced: dict[str, bytes] = {}
        self._added: dict[str, bytes] = {}
        self._removed: set[str] = set()
        self._cache: dict[str, bytes] = {}
        self._is_zip = zipfile.is_zipfile(io.BytesIO(raw))
        if self._is_zip:
            try:
                self._zip: zipfile.ZipFile | None = zipfile.ZipFile(io.BytesIO(raw))
            except zipfile.BadZipFile as exc:
                raise ArchiveError(f"corrupted ZIP archive: {exc}") from exc
            self._names = self._zip.namelist()
        elif raw.lstrip()[:1] == b"<":
            logger.info("input is bare XML, wrapping as single-member archive")
            self._zip = None
            self._names = [RAW_XML_MEMBER]
            self._cache[RAW_XML_MEMBER] = raw
        else:
            raise ArchiveError("not a .drp archive: neither a ZIP container nor an XML document")

    # -- construction ---------------------------------------------------

    @classmethod
    def open(cls, path: Path | str) -> DRPArchive:
        """Load an archive from disk.

        Raises:
            ArchiveError: if the file is missing or not a recognizable .drp.
        """
        path = Path(path)
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ArchiveError(f"cannot read {path}: {exc}") from exc
        logger.info("opened %s (%d bytes)", path, len(raw))
        return cls(raw, path=path)

    @classmethod
    def from_bytes(cls, raw: bytes) -> DRPArchive:
        """Load an archive from an in-memory buffer."""
        return cls(raw, path=None)

    # -- context manager --------------------------------------------------

    def __enter__(self) -> DRPArchive:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Release the underlying ZIP handle."""
        if self._zip is not None:
            self._zip.close()

    # -- inspection -------------------------------------------------------

    @property
    def path(self) -> Path | None:
        """Path the archive was opened from, if any."""
        return self._path

    @property
    def is_zip(self) -> bool:
        """``False`` when the input was bare XML wrapped as an archive."""
        return self._is_zip

    @property
    def dirty(self) -> bool:
        """``True`` once any member was replaced, added, or removed."""
        return bool(self._replaced or self._added or self._removed)

    def namelist(self) -> list[str]:
        """All member names, original order first, then additions."""
        names = [n for n in self._names if n not in self._removed]
        names.extend(n for n in self._added if n not in self._removed)
        return names

    def xml_members(self) -> list[str]:
        """Member names that look like XML files."""
        result = []
        for name in self.namelist():
            if name.lower().endswith(".xml") or self.read(name).lstrip()[:1] == b"<":
                result.append(name)
        return result

    # -- member IO ----------------------------------------------------------

    def read(self, name: str) -> bytes:
        """Read a member's (possibly replaced) content, lazily and cached."""
        if name in self._removed:
            raise ArchiveError(f"member {name!r} has been removed")
        if name in self._replaced:
            return self._replaced[name]
        if name in self._added:
            return self._added[name]
        if name in self._cache:
            return self._cache[name]
        if self._zip is None or name not in self._names:
            raise ArchiveError(f"no such archive member: {name!r}")
        try:
            data = self._zip.read(name)
        except (zipfile.BadZipFile, KeyError) as exc:
            raise ArchiveError(f"cannot read member {name!r}: {exc}") from exc
        self._cache[name] = data
        return data

    def replace(self, name: str, data: bytes) -> None:
        """Replace an existing member's content."""
        if name not in self.namelist():
            raise ArchiveError(f"cannot replace missing member {name!r}")
        if name in self._added:
            self._added[name] = data
            return
        if data == self._original_content(name):
            # Writing back identical bytes keeps the archive clean so an
            # unchanged project still saves byte-for-byte.
            self._replaced.pop(name, None)
            return
        self._replaced[name] = data
        logger.debug("member %s replaced (%d bytes)", name, len(data))

    def add(self, name: str, data: bytes) -> None:
        """Add a new member (must not already exist)."""
        if name in self.namelist():
            raise ArchiveError(f"member already exists: {name!r}")
        self._added[name] = data

    def remove(self, name: str) -> None:
        """Remove a member from the archive (reversible via :meth:`restore`)."""
        if name not in self.namelist():
            raise ArchiveError(f"cannot remove missing member {name!r}")
        self._removed.add(name)
        logger.debug("member %s removed", name)

    def restore(self, name: str) -> None:
        """Undo a :meth:`remove` of *name*."""
        if name not in self._removed:
            raise ArchiveError(f"member {name!r} was not removed")
        self._removed.discard(name)

    def _original_content(self, name: str) -> bytes:
        if name in self._cache and name not in self._replaced:
            return self._cache[name]
        if self._zip is not None and name in self._names:
            data = self._zip.read(name)
            self._cache.setdefault(name, data)
            return data
        return self._cache.get(name, b"")

    # -- extraction --------------------------------------------------------

    def extract(self, dest_dir: Path | str) -> list[Path]:
        """Extract every member below *dest_dir*, returning written paths.

        Member names are sanitized against path traversal.
        """
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for name in self.namelist():
            target = (dest / name).resolve()
            if not target.is_relative_to(dest.resolve()):
                raise ArchiveError(f"unsafe member path: {name!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.read(name))
            written.append(target)
        logger.info("extracted %d members to %s", len(written), dest)
        return written

    # -- verification ---------------------------------------------------------

    def verify(self) -> list[str]:
        """CRC-check every original member.

        Returns:
            List of member names that failed verification (empty = OK).
        """
        if self._zip is None:
            return []
        bad: list[str] = []
        for name in self._names:
            try:
                self._zip.read(name)
            except (zipfile.BadZipFile, zlib.error, OSError):
                bad.append(name)
        try:
            first_bad = self._zip.testzip()
        except (zipfile.BadZipFile, zlib.error, OSError):
            first_bad = None  # already recorded by the per-member pass
        if first_bad and first_bad not in bad:
            bad.append(first_bad)
        if bad:
            logger.warning("CRC verification failed for: %s", ", ".join(bad))
        return bad

    # -- rebuild / save ----------------------------------------------------------

    def rebuild(self) -> bytes:
        """Serialize the archive to bytes.

        Untouched archives return the original bytes verbatim.
        """
        if not self.dirty:
            return self._raw
        if not self._is_zip:
            # Bare-XML mode: the single member IS the file.
            return self.read(RAW_XML_MEMBER)
        buffer = io.BytesIO()
        assert self._zip is not None
        with zipfile.ZipFile(buffer, "w") as out:
            for info in self._zip.infolist():
                if info.filename in self._removed:
                    continue
                new_info = zipfile.ZipInfo(filename=info.filename, date_time=info.date_time)
                new_info.compress_type = info.compress_type
                new_info.external_attr = info.external_attr
                new_info.internal_attr = info.internal_attr
                new_info.create_system = info.create_system
                new_info.comment = info.comment
                out.writestr(new_info, self.read(info.filename))
            for name, data in self._added.items():
                if name not in self._removed:
                    out.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
        return buffer.getvalue()

    def save(self, path: Path | str) -> None:
        """Write the archive to *path* (see class docstring for guarantees)."""
        target = Path(path)
        try:
            target.write_bytes(self.rebuild())
        except OSError as exc:
            raise SaveError(f"cannot write {target}: {exc}") from exc
        logger.info("saved archive to %s", target)

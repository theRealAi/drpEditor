"""Project validation: structural checks that never modify anything.

The validator inspects a parsed project and its archive and returns a
list of :class:`ValidationIssue` records. It intentionally raises
nothing for content problems -- callers decide how strict to be -- and
only raises for programmer errors.

Checks performed:

* archive CRC failures,
* missing / unparseable project XML (already fatal at parse time, but
  reported when the model is empty),
* duplicate UUIDs across timelines, clips, and media items,
* clip ``source`` references pointing at no known media item,
* media file paths that do not exist on disk (optional, off by default
  since projects usually move between machines),
* FieldsBlobs that are not valid hex.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .exceptions import BinaryDecodeError
from .models import Project

__all__ = ["ValidationIssue", "Validator"]

logger = logging.getLogger(__name__)

Severity = Literal["error", "warning"]


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One problem found during validation.

    Attributes:
        severity: ``"error"`` (project likely broken) or ``"warning"``.
        code: Stable machine-readable identifier, e.g. ``"duplicate-uuid"``.
        message: Human-readable description.
        object_id: UUID of the offending object, if applicable.
    """

    severity: Severity
    code: str
    message: str
    object_id: str = ""


class Validator:
    """Runs all validation checks against one project.

    Args:
        project: The parsed project to inspect.
        check_files: Also verify media file paths exist locally.
    """

    def __init__(self, project: Project, *, check_files: bool = False) -> None:
        self._project = project
        self._check_files = check_files

    def run(self) -> list[ValidationIssue]:
        """Execute every check and return all issues found."""
        issues: list[ValidationIssue] = []
        issues.extend(self._check_archive())
        issues.extend(
            ValidationIssue(
                severity="error",
                code="unparseable-member",
                message=f"XML member {member!r} could not be parsed: {error}",
            )
            for member, error in self._project.load_errors.items()
        )
        issues.extend(self._check_model_presence())
        issues.extend(self._check_duplicate_uuids())
        issues.extend(self._check_references())
        issues.extend(self._check_blobs())
        if self._check_files:
            issues.extend(self._check_media_files())
        logger.info("validation finished: %d issue(s)", len(issues))
        return issues

    # -- individual checks ---------------------------------------------------

    def _check_archive(self) -> list[ValidationIssue]:
        bad = self._project.archive.verify()
        return [
            ValidationIssue(
                severity="error",
                code="crc-failure",
                message=f"archive member {name!r} failed CRC verification",
            )
            for name in bad
        ]

    def _check_model_presence(self) -> list[ValidationIssue]:
        if not self._project.timelines and not self._project.all_clips():
            return [
                ValidationIssue(
                    severity="warning",
                    code="empty-model",
                    message=(
                        "no timelines or clips were recognized; the XML may use "
                        "an unknown schema (consider a custom ParserConfig)"
                    ),
                )
            ]
        return []

    def _check_duplicate_uuids(self) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        seen: dict[str, str] = {}
        objects: list[tuple[str, str]] = []
        objects.extend(("timeline", t.uuid) for t in self._project.timelines)
        objects.extend(("clip", c.uuid) for c in self._project.all_clips())
        objects.extend(("media", m.uuid) for m in self._project.media_pool)
        for kind, uuid in objects:
            if not uuid:
                continue
            if uuid in seen:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="duplicate-uuid",
                        message=f"UUID {uuid} used by both {seen[uuid]} and {kind}",
                        object_id=uuid,
                    )
                )
            else:
                seen[uuid] = kind
        return issues

    def _check_references(self) -> list[ValidationIssue]:
        media_uuids = {m.uuid for m in self._project.media_pool if m.uuid}
        issues: list[ValidationIssue] = []
        if not media_uuids:
            return issues  # cannot judge references without a media pool
        for clip in self._project.all_clips():
            if clip.source and clip.source not in media_uuids:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="broken-reference",
                        message=(
                            f"clip {clip.name!r} references media {clip.source} "
                            "which is not in the media pool"
                        ),
                        object_id=clip.uuid,
                    )
                )
        return issues

    def _check_blobs(self) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for clip in self._project.all_clips():
            holder = clip.blob_holder
            if holder is None:
                continue
            try:
                holder.get()
            except BinaryDecodeError as exc:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="corrupt-blob",
                        message=f"clip {clip.name!r} has a corrupted FieldsBlob: {exc}",
                        object_id=clip.uuid,
                    )
                )
        return issues

    def _check_media_files(self) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for item in self._project.media_pool:
            if item.file_path and not Path(item.file_path).exists():
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        code="missing-media",
                        message=f"media file not found: {item.file_path}",
                        object_id=item.uuid,
                    )
                )
        return issues

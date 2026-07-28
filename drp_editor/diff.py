"""Diff engine: compare projects, timelines, clips, and binary blobs.

This is the primary reverse-engineering tool: export a project, toggle
one setting in Resolve, export again, then diff the two files to see
exactly which bytes changed::

    drp diff before.drp after.drp

Objects are matched across the two projects by UUID when available and
by name otherwise; unmatched objects are reported as added/removed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .fields_blob import ByteChange, FieldsBlob, diff_bytes, field_diff
from .models import Clip, MediaItem, Project, Timeline

__all__ = [
    "ClipDiff",
    "MediaDiff",
    "ProjectDiff",
    "PropertyChange",
    "TimelineDiff",
    "diff_projects",
    "format_project_diff",
]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PropertyChange:
    """One changed scalar property."""

    property: str
    old: str
    new: str


@dataclass(slots=True)
class ClipDiff:
    """Changes to one clip."""

    uuid: str
    name: str
    status: str = "changed"  # "added" | "removed" | "changed"
    properties: list[PropertyChange] = field(default_factory=list)
    blob_changes: list[ByteChange] = field(default_factory=list)
    blob_fields: dict[str, tuple[object, object]] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        """``True`` if nothing actually changed."""
        return self.status == "changed" and not (self.properties or self.blob_changes)


@dataclass(slots=True)
class TimelineDiff:
    """Changes to one timeline."""

    uuid: str
    name: str
    status: str = "changed"
    properties: list[PropertyChange] = field(default_factory=list)
    clips: list[ClipDiff] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        """``True`` if nothing actually changed."""
        return self.status == "changed" and not (self.properties or self.clips)


@dataclass(slots=True)
class MediaDiff:
    """Changes to one media pool item."""

    uuid: str
    name: str
    status: str = "changed"
    properties: list[PropertyChange] = field(default_factory=list)


@dataclass(slots=True)
class ProjectDiff:
    """Full comparison of two projects."""

    timelines: list[TimelineDiff] = field(default_factory=list)
    media: list[MediaDiff] = field(default_factory=list)
    unattached_clips: list[ClipDiff] = field(default_factory=list)
    archive_members: list[PropertyChange] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        """``True`` when the two projects are equivalent."""
        return not (self.timelines or self.media or self.unattached_clips or self.archive_members)


def _key(obj: Clip | Timeline | MediaItem) -> str:
    return obj.uuid or f"name:{obj.name}"


def _diff_scalar(name: str, old: str, new: str) -> PropertyChange | None:
    return PropertyChange(name, old, new) if old != new else None


def _diff_clip(old: Clip, new: Clip) -> ClipDiff:
    diff = ClipDiff(uuid=new.uuid, name=new.name)
    for prop in ("name", "source"):
        change = _diff_scalar(prop, getattr(old, prop), getattr(new, prop))
        if change:
            diff.properties.append(change)
    old_blob = old.fields_blob
    new_blob = new.fields_blob
    if old_blob is not None and new_blob is not None:
        diff.blob_changes = old_blob.diff(new_blob)
        diff.blob_fields = field_diff(old_blob, new_blob)
    elif (old_blob is None) != (new_blob is None):
        old_hex = old_blob.to_hex() if old_blob else ""
        new_hex = new_blob.to_hex() if new_blob else ""
        diff.blob_changes = diff_bytes(
            FieldsBlob.from_hex(old_hex).raw_bytes, FieldsBlob.from_hex(new_hex).raw_bytes
        )
    return diff


def _diff_clip_lists(old_clips: list[Clip], new_clips: list[Clip]) -> list[ClipDiff]:
    old_map = {_key(c): c for c in old_clips}
    new_map = {_key(c): c for c in new_clips}
    result: list[ClipDiff] = []
    for key, old_clip in old_map.items():
        new_clip = new_map.get(key)
        if new_clip is None:
            result.append(ClipDiff(uuid=old_clip.uuid, name=old_clip.name, status="removed"))
        else:
            clip_diff = _diff_clip(old_clip, new_clip)
            if not clip_diff.empty:
                result.append(clip_diff)
    for key, new_clip in new_map.items():
        if key not in old_map:
            result.append(ClipDiff(uuid=new_clip.uuid, name=new_clip.name, status="added"))
    return result


def diff_projects(old: Project, new: Project) -> ProjectDiff:
    """Compare two parsed projects, including archive-level changes."""
    result = ProjectDiff()

    old_timelines = {_key(t): t for t in old.timelines}
    new_timelines = {_key(t): t for t in new.timelines}
    for key, old_tl in old_timelines.items():
        new_tl = new_timelines.get(key)
        if new_tl is None:
            result.timelines.append(
                TimelineDiff(uuid=old_tl.uuid, name=old_tl.name, status="removed")
            )
            continue
        tl_diff = TimelineDiff(uuid=new_tl.uuid, name=new_tl.name)
        name_change = _diff_scalar("name", old_tl.name, new_tl.name)
        if name_change:
            tl_diff.properties.append(name_change)
        tl_diff.clips = _diff_clip_lists(old_tl.clips, new_tl.clips)
        if not tl_diff.empty:
            result.timelines.append(tl_diff)
    for key, new_tl in new_timelines.items():
        if key not in old_timelines:
            result.timelines.append(
                TimelineDiff(uuid=new_tl.uuid, name=new_tl.name, status="added")
            )

    result.unattached_clips = _diff_clip_lists(old.unattached_clips, new.unattached_clips)

    old_media = {_key(m): m for m in old.media_pool}
    new_media = {_key(m): m for m in new.media_pool}
    for key, old_item in old_media.items():
        new_item = new_media.get(key)
        if new_item is None:
            result.media.append(MediaDiff(uuid=old_item.uuid, name=old_item.name, status="removed"))
            continue
        changes = [
            c
            for prop in ("name", "file_path")
            if (c := _diff_scalar(prop, getattr(old_item, prop), getattr(new_item, prop)))
        ]
        if changes:
            result.media.append(
                MediaDiff(uuid=new_item.uuid, name=new_item.name, properties=changes)
            )
    for key, new_item in new_media.items():
        if key not in old_media:
            result.media.append(MediaDiff(uuid=new_item.uuid, name=new_item.name, status="added"))

    result.archive_members = _diff_archives(old, new)
    return result


def _diff_archives(old: Project, new: Project) -> list[PropertyChange]:
    changes: list[PropertyChange] = []
    old_names = set(old.archive.namelist())
    new_names = set(new.archive.namelist())
    for name in sorted(old_names - new_names):
        changes.append(PropertyChange(name, "present", "missing"))
    for name in sorted(new_names - old_names):
        changes.append(PropertyChange(name, "missing", "present"))
    for name in sorted(old_names & new_names):
        if name == old.xml_member or name == new.xml_member:
            continue  # XML differences are covered by the model diff
        old_data = old.archive.read(name)
        new_data = new.archive.read(name)
        if old_data != new_data:
            runs = diff_bytes(old_data, new_data)
            changes.append(
                PropertyChange(
                    name,
                    f"{len(old_data)} bytes",
                    f"{len(new_data)} bytes, {len(runs)} changed region(s)",
                )
            )
    return changes


# -- text rendering ---------------------------------------------------------


def _format_byte_change(change: ByteChange, indent: str) -> list[str]:
    return [
        f"{indent}Offset 0x{change.offset:04x}",
        f"{indent}  Old: {change.old.hex() or '(none)'}",
        f"{indent}  New: {change.new.hex() or '(none)'}",
    ]


def _format_clip_diff(clip: ClipDiff, indent: str = "  ") -> list[str]:
    label = clip.name or clip.uuid or "<unnamed clip>"
    if clip.status != "changed":
        return [f"{indent}Clip: {label} [{clip.status}]"]
    lines = [f"{indent}Clip: {label}"]
    if clip.uuid:
        lines.append(f"{indent}  UUID: {clip.uuid}")
    for prop in clip.properties:
        lines.append(f"{indent}  {prop.property}: {prop.old!r} -> {prop.new!r}")
    if clip.blob_changes:
        lines.append(f"{indent}  FieldsBlob")
        for field_name, (old_v, new_v) in clip.blob_fields.items():
            lines.append(f"{indent}    [{field_name}] {old_v!r} -> {new_v!r}")
        for change in clip.blob_changes:
            lines.extend(_format_byte_change(change, indent + "    "))
    return lines


def format_project_diff(diff: ProjectDiff) -> str:
    """Render a diff in the human-readable report format."""
    if diff.empty:
        return "No changes"
    lines: list[str] = []
    for timeline in diff.timelines:
        label = timeline.name or timeline.uuid or "<unnamed timeline>"
        if timeline.status != "changed":
            lines.append(f"Timeline: {label} [{timeline.status}]")
            continue
        lines.append(f"Timeline: {label}")
        for prop in timeline.properties:
            lines.append(f"  {prop.property}: {prop.old!r} -> {prop.new!r}")
        for clip in timeline.clips:
            lines.extend(_format_clip_diff(clip))
    if diff.unattached_clips:
        lines.append("Unattached clips:")
        for clip in diff.unattached_clips:
            lines.extend(_format_clip_diff(clip))
    for media in diff.media:
        label = media.name or media.uuid
        if media.status != "changed":
            lines.append(f"Media: {label} [{media.status}]")
        else:
            lines.append(f"Media: {label}")
            for prop in media.properties:
                lines.append(f"  {prop.property}: {prop.old!r} -> {prop.new!r}")
    if diff.archive_members:
        lines.append("Archive members:")
        for prop in diff.archive_members:
            lines.append(f"  {prop.property}: {prop.old} -> {prop.new}")
    return "\n".join(lines)

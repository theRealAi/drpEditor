"""Object model for parsed .drp projects.

Every model object keeps a reference to its underlying lxml node
(``xml_node``) plus *carriers* describing exactly where each property
lives in the XML (attribute vs. child element). All mutation flows
through :class:`Project` so that:

* only the carrier for the edited property is touched in the XML,
* every change is recorded as a :class:`~drp_editor.patch.Patch`,
* lookup caches stay consistent.

Performance notes: the parser builds UUID/name caches once; lookups are
O(1) and blobs are decoded lazily on first access, so projects with
100k+ clips stay responsive.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from lxml import etree

from .exceptions import PatchError
from .fields_blob import BlobSchema, FieldsBlob
from .patch import Patch, PatchLog
from .xml_editor import XMLDocument

if TYPE_CHECKING:
    from .archive import DRPArchive

__all__ = [
    "Clip",
    "MediaItem",
    "Project",
    "PropertyCarrier",
    "SearchResult",
    "Settings",
    "Timeline",
]

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PropertyCarrier:
    """Where a model property physically lives in the XML.

    Attributes:
        kind: ``"attr"`` -- attribute on ``element``; ``"child"`` -- text
            of the child ``element``.
        key: Attribute name (only meaningful for ``kind == "attr"``).
        element: The element carrying the value.
        document: The document the element belongs to. Carriers can point
            into a *different* document than their owning object (e.g. a
            timeline's name living in a media-pool file), so each carrier
            tracks its own document for correct dirty flagging.
    """

    kind: Literal["attr", "child"]
    key: str
    element: etree._Element
    document: XMLDocument

    def read(self) -> str | None:
        """Current raw string value in the XML."""
        if self.kind == "attr":
            return self.element.get(self.key)
        return self.element.text

    def write(self, value: str) -> str | None:
        """Write *value* through the carrier, returning the old value."""
        if self.kind == "attr":
            return self.document.set_attribute(self.element, self.key, value)
        return self.document.set_text(self.element, value)


@dataclass(slots=True)
class _BlobHolder:
    """Lazy container for a clip's FieldsBlob (parsed on first access)."""

    carrier: PropertyCarrier
    schema: BlobSchema | None = None
    _blob: FieldsBlob | None = None

    def get(self) -> FieldsBlob:
        if self._blob is None:
            self._blob = FieldsBlob.from_hex(self.carrier.read() or "", schema=self.schema)
        return self._blob

    @property
    def loaded(self) -> bool:
        return self._blob is not None


@dataclass(slots=True)
class Settings:
    """Project-level settings (key/value view over one XML element)."""

    xml_node: etree._Element
    values: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly representation."""
        return dict(self.values)


@dataclass(slots=True)
class MediaItem:
    """One media pool entry."""

    uuid: str
    name: str
    file_path: str
    xml_node: etree._Element
    document: XMLDocument
    carriers: dict[str, PropertyCarrier] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly representation."""
        return {"uuid": self.uuid, "name": self.name, "file_path": self.file_path}


@dataclass(slots=True)
class Clip:
    """One clip, either inside a timeline or unattached.

    Attributes:
        uuid: Clip UUID ("" when the XML carries none).
        name: Display name.
        source: UUID of the referenced media pool item ("" if none).
        xml_node: Backing element.
        timeline_uuid: Owning timeline's UUID ("" for unattached clips).
    """

    uuid: str
    name: str
    source: str
    xml_node: etree._Element
    document: XMLDocument
    timeline_uuid: str = ""
    carriers: dict[str, PropertyCarrier] = field(default_factory=dict)
    blob_holder: _BlobHolder | None = None

    @property
    def fields_blob(self) -> FieldsBlob | None:
        """The clip's FieldsBlob, decoded lazily; ``None`` if absent."""
        return self.blob_holder.get() if self.blob_holder else None

    def to_dict(self, *, include_fields: bool = False) -> dict[str, Any]:
        """JSON-friendly representation."""
        data: dict[str, Any] = {
            "uuid": self.uuid,
            "name": self.name,
            "source": self.source,
            "timeline_uuid": self.timeline_uuid,
        }
        if include_fields and self.fields_blob is not None:
            blob = self.fields_blob
            data["fields_blob"] = {
                "hex": blob.to_hex(),
                "size": len(blob),
                "known_fields": {
                    k: (v.hex() if isinstance(v, bytes) else v) for k, v in blob.decode().items()
                },
            }
        return data


@dataclass(slots=True)
class Timeline:
    """One timeline and its clips.

    Attributes:
        member: Archive member name when this timeline is the root of its
            own XML file (Resolve's ``SeqContainer/<uuid>.xml`` layout);
            "" when it is an element inside a shared document.
        pool_item_uuid: UUID of the media-pool item representing this
            timeline (used so removing the timeline also removes its pool
            entry), "" if unknown.
    """

    uuid: str
    name: str
    xml_node: etree._Element
    document: XMLDocument
    clips: list[Clip] = field(default_factory=list)
    carriers: dict[str, PropertyCarrier] = field(default_factory=dict)
    member: str = ""
    pool_item_uuid: str = ""

    def to_dict(self, *, include_clips: bool = True) -> dict[str, Any]:
        """JSON-friendly representation."""
        data: dict[str, Any] = {"uuid": self.uuid, "name": self.name}
        if include_clips:
            data["clips"] = [c.to_dict() for c in self.clips]
        return data


@dataclass(slots=True)
class _Removal:
    """Everything needed to undo one removal.

    Element removals populate ``element``/``parent``/``element_index``;
    whole-member removals (timelines that own their own archive file)
    populate ``member`` instead. ``linked`` chains a secondary removal
    performed as part of the same logical operation (e.g. a timeline's
    media-pool entry).
    """

    obj: Clip | Timeline | MediaItem
    owner_list: list[Any]
    list_index: int
    element: etree._Element | None = None
    parent: etree._Element | None = None
    element_index: int = 0
    member: str = ""
    linked: _Removal | None = None


#: Sentinel property name used in patches recording element removals.
REMOVED_PROPERTY = "__removed__"

#: Tags that are pure vector-entry wrappers in Resolve's XML. When an
#: object is the sole child of such a wrapper, removing the object removes
#: the wrapper too, so no empty ``<Element/>`` entries are left behind.
WRAPPER_TAGS = frozenset({"Element"})


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One hit from :meth:`Project.search`."""

    object_type: str
    object_id: str
    property: str
    value: str
    obj: object


class Project:
    """A fully parsed .drp project.

    Construction is handled by :class:`~drp_editor.parser.DRPParser`; the
    typical entry point is :func:`drp_editor.open_project`.
    """

    def __init__(
        self,
        *,
        name: str,
        archive: DRPArchive,
        documents: dict[str, XMLDocument],
        xml_member: str,
        timelines: list[Timeline],
        media_pool: list[MediaItem],
        settings: Settings | None,
        unattached_clips: list[Clip],
    ) -> None:
        self.name = name
        self.archive = archive
        #: All parsed XML documents, keyed by archive member name.
        self.documents = documents
        #: Member name of the primary project document.
        self.xml_member = xml_member
        self.timelines = timelines
        self.media_pool = media_pool
        self.settings = settings
        self.unattached_clips = unattached_clips
        self.patch_log = PatchLog()
        #: Members that failed to parse (member name -> error message).
        self.load_errors: dict[str, str] = {}
        self._removals: dict[int, _Removal] = {}
        self._build_caches()

    @property
    def document(self) -> XMLDocument:
        """The primary project document (``project.xml``)."""
        return self.documents[self.xml_member]

    # -- caches -------------------------------------------------------

    def _build_caches(self) -> None:
        self._timelines_by_uuid: dict[str, Timeline] = {}
        self._timelines_by_name: dict[str, list[Timeline]] = {}
        self._clips_by_uuid: dict[str, Clip] = {}
        self._clips_by_name: dict[str, list[Clip]] = {}
        self._media_by_uuid: dict[str, MediaItem] = {}
        for timeline in self.timelines:
            if timeline.uuid:
                self._timelines_by_uuid[timeline.uuid] = timeline
            self._timelines_by_name.setdefault(timeline.name, []).append(timeline)
        for clip in self.all_clips():
            if clip.uuid:
                self._clips_by_uuid[clip.uuid] = clip
            self._clips_by_name.setdefault(clip.name, []).append(clip)
        for item in self.media_pool:
            if item.uuid:
                self._media_by_uuid[item.uuid] = item

    # -- enumeration ----------------------------------------------------

    def all_clips(self) -> list[Clip]:
        """Every clip in every timeline plus unattached clips."""
        result: list[Clip] = []
        for timeline in self.timelines:
            result.extend(timeline.clips)
        result.extend(self.unattached_clips)
        return result

    # -- search API -------------------------------------------------------

    def find_clip(self, *, name: str | None = None, uuid: str | None = None) -> Clip | None:
        """Find one clip by name or UUID (first match wins for names)."""
        if uuid is not None:
            return self._clips_by_uuid.get(uuid)
        if name is not None:
            matches = self._clips_by_name.get(name)
            return matches[0] if matches else None
        raise ValueError("provide either name= or uuid=")

    def find_clips(self, name: str) -> list[Clip]:
        """All clips with an exact name match."""
        return list(self._clips_by_name.get(name, []))

    def find_timeline(self, *, name: str | None = None, uuid: str | None = None) -> Timeline | None:
        """Find one timeline by name or UUID."""
        if uuid is not None:
            return self._timelines_by_uuid.get(uuid)
        if name is not None:
            matches = self._timelines_by_name.get(name)
            return matches[0] if matches else None
        raise ValueError("provide either name= or uuid=")

    def find_media(self, *, name: str | None = None, uuid: str | None = None) -> MediaItem | None:
        """Find one media pool item by name or UUID."""
        if uuid is not None:
            return self._media_by_uuid.get(uuid)
        if name is not None:
            return next((m for m in self.media_pool if m.name == name), None)
        raise ValueError("provide either name= or uuid=")

    def search(self, pattern: str) -> list[SearchResult]:
        """Regex search across names, UUIDs, and media file paths."""
        regex = re.compile(pattern)
        hits: list[SearchResult] = []

        def check(obj_type: str, obj_id: str, prop: str, value: str, obj: object) -> None:
            if value and regex.search(value):
                hits.append(SearchResult(obj_type, obj_id, prop, value, obj))

        for timeline in self.timelines:
            check("timeline", timeline.uuid, "name", timeline.name, timeline)
            check("timeline", timeline.uuid, "uuid", timeline.uuid, timeline)
        for clip in self.all_clips():
            check("clip", clip.uuid, "name", clip.name, clip)
            check("clip", clip.uuid, "uuid", clip.uuid, clip)
        for item in self.media_pool:
            check("media", item.uuid, "name", item.name, item)
            check("media", item.uuid, "uuid", item.uuid, item)
            check("media", item.uuid, "file_path", item.file_path, item)
        return hits

    # -- mutation (patch-recording) ------------------------------------------

    def set_property(self, obj: Clip | Timeline | MediaItem, prop: str, value: str) -> Patch:
        """Change a model property, editing only its XML carrier.

        Raises:
            PatchError: if the property has no known XML carrier.
        """
        carrier = obj.carriers.get(prop)
        if carrier is None:
            raise PatchError(
                f"property {prop!r} of {type(obj).__name__} has no XML carrier; "
                "cannot edit it safely"
            )
        old = carrier.read() or ""
        carrier.write(value)
        if hasattr(obj, prop):
            setattr(obj, prop, value)
        patch = self.patch_log.record(
            object_type=type(obj).__name__.lower(),
            object_id=getattr(obj, "uuid", ""),
            property=prop,
            old_value=old,
            new_value=value,
        )
        if prop in ("name", "uuid"):
            self._build_caches()
        return patch

    def set_blob_field(self, clip: Clip, field_name: str, value: int | float | bytes) -> Patch:
        """Change one known field inside a clip's FieldsBlob.

        Only the field's bytes change; the new hex is written back through
        the blob's original XML carrier.
        """
        blob = clip.fields_blob
        if blob is None or clip.blob_holder is None:
            raise PatchError(f"clip {clip.name!r} has no FieldsBlob")
        old_raw, new_raw = blob.set_field(field_name, value)
        clip.blob_holder.carrier.write(blob.to_hex())
        return self.patch_log.record(
            object_type="clip",
            object_id=clip.uuid,
            property=f"fields_blob.{field_name}",
            old_value=old_raw.hex(),
            new_value=new_raw.hex(),
        )

    def remove_object(self, obj: Clip | Timeline | MediaItem) -> Patch:
        """Remove an element from the project (undoable).

        Regular objects: the XML node is detached in place; sibling
        formatting is untouched, and undo reinserts the exact same node at
        the exact same position.

        Timelines that own their own archive member (Resolve's
        ``SeqContainer/<uuid>.xml`` layout): the whole member is removed,
        and the timeline's media-pool entry is removed with it when known.

        Removing a timeline removes all of its clips with it.

        Raises:
            PatchError: if the object was already removed or cannot be
                located in the project.
        """
        owner_list = self._owner_list(obj)
        if owner_list is None:
            raise PatchError(f"{type(obj).__name__} {obj.name!r} is not part of this project")
        list_index = owner_list.index(obj)

        if isinstance(obj, Timeline) and obj.member:
            removal = self._remove_member_timeline(obj, owner_list, list_index)
        else:
            removal = self._remove_element(obj, owner_list, list_index)
        self._build_caches()

        patch = self.patch_log.record(
            object_type=type(obj).__name__.lower(),
            object_id=obj.uuid or obj.name,
            property=REMOVED_PROPERTY,
            old_value=obj.name,
            new_value="",
        )
        self._removals[id(patch)] = removal
        logger.info("removed %s %r", type(obj).__name__.lower(), obj.name or obj.uuid)
        return patch

    def _remove_element(
        self, obj: Clip | Timeline | MediaItem, owner_list: list[Any], list_index: int
    ) -> _Removal:
        el = obj.xml_node
        parent = el.getparent()
        # Take sole-child vector wrappers (<Element>) out with the object.
        while (
            parent is not None
            and isinstance(parent.tag, str)
            and etree.QName(parent).localname in WRAPPER_TAGS
            and len(parent) == 1
        ):
            el = parent
            parent = el.getparent()
        if parent is None:
            raise PatchError(f"cannot remove {type(obj).__name__} {obj.name!r}: no XML parent")
        element_index = parent.index(el)
        parent.remove(el)
        obj.document.mark_dirty()
        owner_list.pop(list_index)
        return _Removal(
            obj=obj,
            owner_list=owner_list,
            list_index=list_index,
            element=el,
            parent=parent,
            element_index=element_index,
        )

    def _remove_member_timeline(
        self, timeline: Timeline, owner_list: list[Any], list_index: int
    ) -> _Removal:
        self.archive.remove(timeline.member)
        owner_list.pop(list_index)
        removal = _Removal(
            obj=timeline,
            owner_list=owner_list,
            list_index=list_index,
            member=timeline.member,
        )
        pool_item = (
            self._media_by_uuid.get(timeline.pool_item_uuid) if timeline.pool_item_uuid else None
        )
        if pool_item is not None and pool_item in self.media_pool:
            pool_index = self.media_pool.index(pool_item)
            removal.linked = self._remove_element(pool_item, self.media_pool, pool_index)
            logger.info("removed pool entry %r of timeline %r", pool_item.name, timeline.name)
        return removal

    def _owner_list(self, obj: Clip | Timeline | MediaItem) -> list[Any] | None:
        """The model list holding *obj*, or ``None`` if it is not present."""
        if isinstance(obj, Timeline):
            return self.timelines if obj in self.timelines else None
        if isinstance(obj, MediaItem):
            return self.media_pool if obj in self.media_pool else None
        if obj in self.unattached_clips:
            return self.unattached_clips
        for timeline in self.timelines:
            if obj in timeline.clips:
                return timeline.clips
        return None

    def undo_last(self) -> Patch | None:
        """Revert the most recent patch, if any."""
        patch = self.patch_log.pop()
        if patch is None:
            return None
        if patch.property == REMOVED_PROPERTY:
            removal = self._removals.pop(id(patch), None)
            if removal is None:
                raise PatchError(f"no removal record for patch on {patch.object_id!r}")
            self._undo_removal(removal)
            self._build_caches()
            return patch
        target = self._resolve_patch_target(patch)
        if target is None:
            raise PatchError(f"cannot resolve object {patch.object_id!r} to undo")
        if patch.property.startswith("fields_blob."):
            clip = target
            assert isinstance(clip, Clip) and clip.blob_holder is not None
            blob = clip.blob_holder.get()
            spec = blob.known_fields().get(patch.property.removeprefix("fields_blob."))
            if spec is None:
                raise PatchError(f"cannot undo unknown blob field {patch.property!r}")
            blob.set_bytes(spec.offset, bytes.fromhex(patch.old_value))
            clip.blob_holder.carrier.write(blob.to_hex())
        else:
            carrier = target.carriers.get(patch.property)
            if carrier is None:
                raise PatchError(f"cannot undo property {patch.property!r}: no carrier")
            carrier.write(patch.old_value)
            if hasattr(target, patch.property):
                setattr(target, patch.property, patch.old_value)
            self._build_caches()
        return patch

    def _undo_removal(self, removal: _Removal) -> None:
        if removal.member:
            self.archive.restore(removal.member)
        elif removal.parent is not None and removal.element is not None:
            removal.parent.insert(removal.element_index, removal.element)
            removal.obj.document.mark_dirty()
        removal.owner_list.insert(removal.list_index, removal.obj)
        if removal.linked is not None:
            self._undo_removal(removal.linked)

    def _resolve_patch_target(self, patch: Patch) -> Clip | Timeline | MediaItem | None:
        if patch.object_type == "clip":
            return self._clips_by_uuid.get(patch.object_id)
        if patch.object_type == "timeline":
            return self._timelines_by_uuid.get(patch.object_id)
        if patch.object_type == "mediaitem":
            return self._media_by_uuid.get(patch.object_id)
        return None

    # -- persistence -----------------------------------------------------------

    def flush(self) -> None:
        """Push all pending model changes into the archive layer."""
        for clip in self.all_clips():
            holder = clip.blob_holder
            if holder is not None and holder.loaded and holder.get().dirty:
                holder.carrier.write(holder.get().to_hex())
        current = set(self.archive.namelist())
        for member, document in self.documents.items():
            if member in current:  # skip members removed with their timeline
                self.archive.replace(member, document.to_bytes())

    def save(self, path: Path | str) -> None:
        """Flush changes and write the .drp to *path*.

        An unmodified project is written byte-for-byte identical to the
        input archive.
        """
        self.flush()
        self.archive.save(path)

    # -- export ---------------------------------------------------------------

    def to_dict(self, *, include_fields: bool = False) -> dict[str, Any]:
        """JSON-friendly representation of the whole project."""
        return {
            "name": self.name,
            "settings": self.settings.to_dict() if self.settings else {},
            "timelines": [
                {
                    "uuid": t.uuid,
                    "name": t.name,
                    "clips": [c.to_dict(include_fields=include_fields) for c in t.clips],
                }
                for t in self.timelines
            ],
            "unattached_clips": [
                c.to_dict(include_fields=include_fields) for c in self.unattached_clips
            ],
            "media_pool": [m.to_dict() for m in self.media_pool],
        }

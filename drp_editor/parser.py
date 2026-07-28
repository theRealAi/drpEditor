"""Parser: build the object model from a .drp archive's XML members.

Real Resolve exports spread the project across many XML files:

* ``project.xml`` -- the project root (name, settings, global blobs),
* ``MediaPool/**/MpFolder.xml`` -- one file per media-pool folder holding
  media items (``Sm2MpVideoClip``, ``Sm2MpTimelineClip``, ...) and
  *timeline handles* (``Sm2Timeline`` elements carrying the timeline name),
* ``SeqContainer/<uuid>.xml`` -- one file per timeline
  (``Sm2SequenceContainer``) holding tracks and clips (``Sm2TiVideoClip``).

The parser reads **all** XML members and merges them into one
:class:`~drp_editor.models.Project`. Timeline names are recovered by
linking each sequence container to its media-pool handle: the handle's
blob data embeds the container's UUID as UTF-16 hex.

Tag matching is driven by :class:`ParserConfig` (case-insensitive
regexes) rather than hard-coded names, because Resolve's schema is
undocumented and drifts between versions.

Known limitations
-----------------
* Elements are classified by tag patterns; exotic schemas may need a
  custom :class:`ParserConfig`.
* A timeline handle that references a missing container (or vice versa)
  degrades gracefully: the container shows up unnamed.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from lxml import etree

from .archive import DRPArchive
from .exceptions import XMLParseError
from .fields_blob import BlobSchemaRegistry, default_registry
from .models import Clip, MediaItem, Project, PropertyCarrier, Settings, Timeline, _BlobHolder
from .xml_editor import XMLDocument

__all__ = ["DRPParser", "ParserConfig"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParserConfig:
    """Tag patterns and property-key conventions for one schema flavor.

    All tag patterns are case-insensitive regexes matched against the
    element's local tag name.
    """

    timeline_tag: str = r"(timeline|sequencecontainer)$"
    clip_tag: str = r"clip$"
    media_tag: str = r"(media\w*item|mp\w*clip)$"
    media_pool_tag: str = r"mediapool$"
    settings_tag: str = r"settings?$"
    uuid_keys: tuple[str, ...] = ("uuid", "Uuid", "UUID", "DbId", "id", "Id")
    name_keys: tuple[str, ...] = ("name", "Name", "ClipName")
    source_keys: tuple[str, ...] = (
        "source",
        "Source",
        "MediaRef",
        "MediaPoolItem",
        "mediaId",
        "MediaId",
    )
    file_path_keys: tuple[str, ...] = (
        "filePath",
        "FilePath",
        "MediaFilePath",
        "path",
        "Path",
        "file",
        "File",
    )
    blob_keys: tuple[str, ...] = ("fields", "Fields", "FieldsBlob", "fieldsBlob")
    project_name_keys: tuple[str, ...] = ("name", "Name", "ProjectName")
    xml_member_candidates: tuple[str, ...] = ("project.xml", "Project.xml", "project.drp.xml")
    #: Blob-schema kind (in the registry) applied to clip blobs.
    clip_blob_kind: str = "clip"


DEFAULT_CONFIG = ParserConfig()


@dataclass
class _DocumentScan:
    """Everything classified from one XML member."""

    member: str
    document: XMLDocument
    timelines: list[Timeline] = field(default_factory=list)
    media: list[MediaItem] = field(default_factory=list)
    clips: dict[int, tuple[etree._Element, Clip]] = field(default_factory=dict)
    timeline_elements: dict[int, Timeline] = field(default_factory=dict)
    media_elements: dict[int, MediaItem] = field(default_factory=dict)
    settings: Settings | None = None


@dataclass
class DRPParser:
    """Builds :class:`~drp_editor.models.Project` objects from archives.

    Args:
        config: Schema conventions; defaults to :data:`DEFAULT_CONFIG`.
        registry: Blob schema registry used to attach known-field schemas
            to clip blobs; defaults to the process-wide registry.
    """

    config: ParserConfig = field(default_factory=ParserConfig)
    registry: BlobSchemaRegistry = field(default_factory=lambda: default_registry)

    def __post_init__(self) -> None:
        flags = re.IGNORECASE
        self._timeline_re = re.compile(self.config.timeline_tag, flags)
        self._clip_re = re.compile(self.config.clip_tag, flags)
        self._media_re = re.compile(self.config.media_tag, flags)
        self._media_pool_re = re.compile(self.config.media_pool_tag, flags)
        self._settings_re = re.compile(self.config.settings_tag, flags)

    # -- entry point ------------------------------------------------------

    def parse(self, archive: DRPArchive) -> Project:
        """Parse every XML member of *archive* into one project model."""
        primary = self._select_primary_member(archive)
        members = [primary] + [m for m in archive.xml_members() if m != primary]

        documents: dict[str, XMLDocument] = {}
        scans: list[_DocumentScan] = []
        load_errors: dict[str, str] = {}
        for member in members:
            try:
                document = XMLDocument(archive.read(member), source_name=member)
            except XMLParseError as exc:
                if member == primary:
                    raise
                # Broken auxiliary members must not block opening the project.
                logger.warning("skipping unparseable member %s: %s", member, exc)
                load_errors[member] = str(exc)
                continue
            documents[member] = document
            scans.append(self._scan_document(member, document))

        timelines: list[Timeline] = []
        media_pool: list[MediaItem] = []
        settings: Settings | None = None
        unattached: list[Clip] = []
        for scan in scans:
            unattached.extend(self._assign_clips(scan))
            timelines.extend(scan.timelines)
            media_pool.extend(scan.media)
            if settings is None:
                settings = scan.settings

        timelines = self._merge_timeline_handles(timelines, scans)
        name = self._project_name(documents[primary].root, archive)
        logger.info(
            "parsed project %r: %d members, %d timelines, %d media items",
            name,
            len(documents),
            len(timelines),
            len(media_pool),
        )
        project = Project(
            name=name,
            archive=archive,
            documents=documents,
            xml_member=primary,
            timelines=timelines,
            media_pool=media_pool,
            settings=settings,
            unattached_clips=unattached,
        )
        project.load_errors = load_errors
        return project

    def _select_primary_member(self, archive: DRPArchive) -> str:
        names = archive.namelist()
        for candidate in self.config.xml_member_candidates:
            if candidate in names:
                return candidate
        xml_members = archive.xml_members()
        if not xml_members:
            raise XMLParseError("archive contains no XML document")
        # Largest XML member is almost certainly the project database dump.
        chosen = max(xml_members, key=lambda n: len(archive.read(n)))
        logger.info("using %s as primary project XML", chosen)
        return chosen

    # -- per-document classification -------------------------------------

    def _scan_document(self, member: str, document: XMLDocument) -> _DocumentScan:
        scan = _DocumentScan(member=member, document=document)
        for el in document.root.iter():
            if not isinstance(el.tag, str):
                continue  # comments / processing instructions
            tag = etree.QName(el).localname
            if self._media_pool_re.search(tag):
                continue  # structural container, not an object
            # Leaf elements with only text are data fields or references
            # (e.g. <CurrentTimeline>uuid</CurrentTimeline>), not objects.
            is_leaf = len(el) == 0 and not el.attrib
            if self._timeline_re.search(tag):
                if is_leaf:
                    continue
                timeline = self._build_timeline(el, document)
                if el.getparent() is None:
                    timeline.member = member
                scan.timelines.append(timeline)
                scan.timeline_elements[id(el)] = timeline
            elif self._media_re.search(tag):
                if is_leaf:
                    continue
                item = self._build_media_item(el, document)
                scan.media.append(item)
                scan.media_elements[id(el)] = item
            elif self._clip_re.search(tag):
                if is_leaf:
                    continue
                clip = self._build_clip(el, document)
                scan.clips[id(el)] = (el, clip)
            elif scan.settings is None and self._settings_re.search(tag):
                scan.settings = self._build_settings(el)
        return scan

    def _assign_clips(self, scan: _DocumentScan) -> list[Clip]:
        """Attach each clip to its nearest timeline ancestor (same doc)."""
        unattached: list[Clip] = []
        for el, clip in scan.clips.values():
            owner: Timeline | None = None
            parent = el.getparent()
            while parent is not None:
                found = scan.timeline_elements.get(id(parent))
                if found is not None:
                    owner = found
                    break
                parent = parent.getparent()
            if owner is not None:
                clip.timeline_uuid = owner.uuid
                owner.clips.append(clip)
            else:
                unattached.append(clip)
        return unattached

    # -- timeline handle merging -----------------------------------------

    def _merge_timeline_handles(
        self, timelines: list[Timeline], scans: list[_DocumentScan]
    ) -> list[Timeline]:
        """Merge media-pool timeline handles into their containers.

        A *handle* is a timeline element nested inside a media-pool item
        (e.g. ``Sm2Timeline`` inside ``Sm2MpTimelineClip``): it carries
        the display name but no clips. A *container* owns the clips but
        has no name (``Sm2SequenceContainer``). The handle's blob data
        embeds the container's UUID as UTF-16 hex, which is how we link
        the two. Sequences inside compound clips have no handle; their
        pool item itself embeds the UUID and donates its name instead.
        """
        media_elements: dict[int, MediaItem] = {}
        all_media: list[MediaItem] = []
        for scan in scans:
            media_elements.update(scan.media_elements)
            all_media.extend(scan.media)

        handles: list[tuple[Timeline, MediaItem]] = []
        containers: list[Timeline] = []
        for timeline in timelines:
            pool_item = self._media_ancestor(timeline.xml_node, media_elements)
            if pool_item is not None and not timeline.clips:
                handles.append((timeline, pool_item))
            else:
                containers.append(timeline)

        unnamed = [c for c in containers if not c.name and c.uuid]
        if unnamed:
            # Name donors: handles first (real timelines), then pool items
            # themselves (compound clips). Subtrees are serialized lazily.
            donors: list[tuple[Timeline | MediaItem, MediaItem]] = [
                *((handle, pool_item) for handle, pool_item in handles),
                *((item, item) for item in all_media),
            ]
            subtree_cache: dict[int, bytes] = {}
            for container in unnamed:
                needles = (
                    container.uuid.encode("utf-16-be").hex().encode(),
                    container.uuid.encode("utf-16-le").hex().encode(),
                )
                for donor, pool_item in donors:
                    key = id(donor.xml_node)
                    if key not in subtree_cache:
                        subtree_cache[key] = etree.tostring(donor.xml_node)
                    if any(needle in subtree_cache[key] for needle in needles):
                        container.name = donor.name
                        if "name" in donor.carriers:
                            container.carriers["name"] = donor.carriers["name"]
                        container.pool_item_uuid = pool_item.uuid
                        logger.debug("linked timeline %s to %r", container.uuid, donor.name)
                        break
        return containers

    @staticmethod
    def _media_ancestor(
        el: etree._Element, media_elements: dict[int, MediaItem]
    ) -> MediaItem | None:
        parent = el.getparent()
        while parent is not None:
            item = media_elements.get(id(parent))
            if item is not None:
                return item
            parent = parent.getparent()
        return None

    # -- per-object builders -----------------------------------------------

    def _build_timeline(self, el: etree._Element, document: XMLDocument) -> Timeline:
        carriers: dict[str, PropertyCarrier] = {}
        uuid = self._extract(el, self.config.uuid_keys, "uuid", carriers, document) or ""
        name = self._extract(el, self.config.name_keys, "name", carriers, document) or ""
        return Timeline(uuid=uuid, name=name, xml_node=el, document=document, carriers=carriers)

    def _build_clip(self, el: etree._Element, document: XMLDocument) -> Clip:
        carriers: dict[str, PropertyCarrier] = {}
        uuid = self._extract(el, self.config.uuid_keys, "uuid", carriers, document) or ""
        name = self._extract(el, self.config.name_keys, "name", carriers, document) or ""
        source = self._extract(el, self.config.source_keys, "source", carriers, document) or ""
        blob_holder = self._find_blob(el, document)
        return Clip(
            uuid=uuid,
            name=name,
            source=source,
            xml_node=el,
            document=document,
            carriers=carriers,
            blob_holder=blob_holder,
        )

    def _build_media_item(self, el: etree._Element, document: XMLDocument) -> MediaItem:
        carriers: dict[str, PropertyCarrier] = {}
        uuid = self._extract(el, self.config.uuid_keys, "uuid", carriers, document) or ""
        name = self._extract(el, self.config.name_keys, "name", carriers, document) or ""
        path = self._extract(el, self.config.file_path_keys, "file_path", carriers, document) or ""
        return MediaItem(
            uuid=uuid,
            name=name,
            file_path=path,
            xml_node=el,
            document=document,
            carriers=carriers,
        )

    def _build_settings(self, el: etree._Element) -> Settings:
        values: dict[str, str] = {str(k): str(v) for k, v in el.attrib.items()}
        for child in el:
            if isinstance(child.tag, str) and len(child) == 0 and child.text is not None:
                values[etree.QName(child).localname] = child.text
        return Settings(xml_node=el, values=values)

    # -- extraction helpers ---------------------------------------------------

    def _extract(
        self,
        el: etree._Element,
        keys: tuple[str, ...],
        prop: str,
        carriers: dict[str, PropertyCarrier],
        document: XMLDocument,
    ) -> str | None:
        """Find *prop* among attributes then child elements; record carrier."""
        for key in keys:
            value = el.get(key)
            if value is not None:
                carriers[prop] = PropertyCarrier(
                    kind="attr", key=key, element=el, document=document
                )
                return value
        for child in el:
            if not isinstance(child.tag, str):
                continue
            if etree.QName(child).localname in keys and len(child) == 0:
                carriers[prop] = PropertyCarrier(
                    kind="child", key="", element=child, document=document
                )
                return child.text or ""
        return None

    def _find_blob(self, el: etree._Element, document: XMLDocument) -> _BlobHolder | None:
        """Attach a blob holder when a blob-named carrier exists.

        Detection is by key name only; hex validity is checked lazily so
        that corrupted blobs surface as validation issues instead of
        being silently ignored.
        """
        schema = self.registry.get(self.config.clip_blob_kind)
        for key in self.config.blob_keys:
            value = el.get(key)
            if value is not None and value.strip():
                carrier = PropertyCarrier(kind="attr", key=key, element=el, document=document)
                return _BlobHolder(carrier=carrier, schema=schema)
        for child in el:
            if not isinstance(child.tag, str):
                continue
            if (
                etree.QName(child).localname in self.config.blob_keys
                and len(child) == 0
                and (child.text or "").strip()
            ):
                carrier = PropertyCarrier(kind="child", key="", element=child, document=document)
                return _BlobHolder(carrier=carrier, schema=schema)
        return None

    def _project_name(self, root: etree._Element, archive: DRPArchive) -> str:
        for key in self.config.project_name_keys:
            value = root.get(key)
            if value:
                return value
        for child in root:
            if (
                isinstance(child.tag, str)
                and etree.QName(child).localname in self.config.project_name_keys
                and len(child) == 0
                and child.text
            ):
                return child.text
        if archive.path is not None:
            return archive.path.stem
        return "<unnamed>"

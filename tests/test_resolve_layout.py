"""Tests for real Resolve export layout: multi-file archives, C++ tag
names, timeline handle linkage, and member-level timeline removal.

These mirror the structures observed in actual Resolve 21 exports:
``project.xml`` + ``MediaPool/**/MpFolder.xml`` + ``SeqContainer/*.xml``,
with tags like ``<ListMgt::LmPowerNodeList>``.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

import drp_editor
from drp_editor.archive import DRPArchive
from drp_editor.exceptions import ArchiveError
from drp_editor.xml_editor import XMLDocument
from tests.conftest import SEQ_UUID


class TestCppTagNames:
    def test_parses_double_colon_tags(self) -> None:
        data = (
            b'<?xml version="1.0"?>\n'
            b'<Root><ListMgt::LmThing DbId="x"><Sub/></ListMgt::LmThing></Root>'
        )
        doc = XMLDocument(data)
        tags = [doc.real_tag(el) for el in doc.root.iter()]
        assert "ListMgt::LmThing" in tags

    def test_clean_document_returns_original_bytes(self) -> None:
        data = b'<?xml version="1.0"?>\n<Root><A::B x="1"/></Root>'
        assert XMLDocument(data).to_bytes() == data

    def test_edited_document_restores_colons(self) -> None:
        data = b'<?xml version="1.0"?>\n<Root><A::B x="1"/><Plain/></Root>'
        doc = XMLDocument(data)
        plain = doc.root[-1]
        doc.set_attribute(plain, "edited", "yes")
        out = doc.to_bytes()
        assert b'<A::B x="1"/>' in out
        assert b"__cln__" not in out

    def test_sentinel_collision_avoided(self) -> None:
        data = b'<?xml version="1.0"?>\n<Root note="__cln__"><A::B/></Root>'
        doc = XMLDocument(data)
        doc.set_attribute(doc.root, "edited", "yes")
        out = doc.to_bytes()
        assert b"<A::B/>" in out
        assert b'note="__cln__"' in out


class TestArchiveRemoval:
    def test_remove_and_rebuild(self, drp_file: Path) -> None:
        archive = DRPArchive.open(drp_file)
        archive.remove("render/thumbnail.bin")
        assert "render/thumbnail.bin" not in archive.namelist()
        names = zipfile.ZipFile(io.BytesIO(archive.rebuild())).namelist()
        assert names == ["project.xml"]

    def test_read_removed_raises(self, drp_file: Path) -> None:
        archive = DRPArchive.open(drp_file)
        archive.remove("render/thumbnail.bin")
        with pytest.raises(ArchiveError):
            archive.read("render/thumbnail.bin")

    def test_restore_undoes_removal(self, drp_file: Path) -> None:
        archive = DRPArchive.open(drp_file)
        archive.remove("render/thumbnail.bin")
        archive.restore("render/thumbnail.bin")
        assert "render/thumbnail.bin" in archive.namelist()
        assert not archive.dirty


class TestResolveLayoutParsing:
    def test_project_name_from_child_element(self, resolve_drp: Path) -> None:
        project = drp_editor.open_project(resolve_drp)
        assert project.name == "RealStyle"

    def test_all_members_parsed(self, resolve_drp: Path) -> None:
        project = drp_editor.open_project(resolve_drp)
        assert len(project.documents) == 3
        assert not project.load_errors

    def test_timeline_linked_to_handle_name(self, resolve_drp: Path) -> None:
        project = drp_editor.open_project(resolve_drp)
        assert len(project.timelines) == 1
        timeline = project.timelines[0]
        assert timeline.name == "Main Timeline"
        assert timeline.uuid == SEQ_UUID
        assert timeline.member == f"SeqContainer/{SEQ_UUID}.xml"
        assert timeline.pool_item_uuid == "pool-tl-1"

    def test_clips_attached_with_media_refs(self, resolve_drp: Path) -> None:
        project = drp_editor.open_project(resolve_drp)
        clips = project.timelines[0].clips
        assert [c.uuid for c in clips] == ["ticlip-01", "ticlip-02"]
        assert all(c.source == "pool-vid-1" for c in clips)
        assert all(c.blob_holder is not None for c in clips)

    def test_media_pool_items(self, resolve_drp: Path) -> None:
        project = drp_editor.open_project(resolve_drp)
        uuids = {m.uuid for m in project.media_pool}
        assert uuids == {"pool-vid-1", "pool-tl-1"}

    def test_leaf_clip_tags_are_not_clips(self, resolve_drp: Path) -> None:
        # <Clip>00aa11bb</Clip> inside BtVideoInfo is a data field.
        project = drp_editor.open_project(resolve_drp)
        assert not project.unattached_clips

    def test_round_trip_unmodified_is_identical(self, resolve_drp: Path, tmp_path: Path) -> None:
        project = drp_editor.open_project(resolve_drp)
        out = tmp_path / "copy.drp"
        project.save(out)
        assert out.read_bytes() == resolve_drp.read_bytes()


class TestResolveLayoutMutation:
    def test_remove_clip_removes_element_wrapper(self, resolve_drp: Path, tmp_path: Path) -> None:
        project = drp_editor.open_project(resolve_drp)
        clip = project.timelines[0].clips[0]
        project.remove_object(clip)
        out = tmp_path / "noclip.drp"
        project.save(out)
        seq_xml = zipfile.ZipFile(out).read(f"SeqContainer/{SEQ_UUID}.xml")
        assert b"ticlip-01" not in seq_xml
        assert b"<Element>\n      </Element>" not in seq_xml
        assert b"<Element/>" not in seq_xml
        reopened = drp_editor.open_project(out)
        assert [c.uuid for c in reopened.timelines[0].clips] == ["ticlip-02"]

    def test_undo_clip_removal_restores_wrapper(self, resolve_drp: Path, tmp_path: Path) -> None:
        project = drp_editor.open_project(resolve_drp)
        project.remove_object(project.timelines[0].clips[0])
        project.undo_last()
        out = tmp_path / "undone.drp"
        project.save(out)
        reopened = drp_editor.open_project(out)
        assert [c.uuid for c in reopened.timelines[0].clips] == ["ticlip-01", "ticlip-02"]

    def test_remove_timeline_drops_member_and_pool_entry(
        self, resolve_drp: Path, tmp_path: Path
    ) -> None:
        project = drp_editor.open_project(resolve_drp)
        project.remove_object(project.timelines[0])
        assert not project.timelines
        assert {m.uuid for m in project.media_pool} == {"pool-vid-1"}
        out = tmp_path / "notl.drp"
        project.save(out)
        names = zipfile.ZipFile(out).namelist()
        assert f"SeqContainer/{SEQ_UUID}.xml" not in names
        reopened = drp_editor.open_project(out)
        assert not reopened.timelines
        assert b"pool-tl-1" not in zipfile.ZipFile(out).read("MediaPool/Master/MpFolder.xml")

    def test_undo_timeline_removal(self, resolve_drp: Path, tmp_path: Path) -> None:
        project = drp_editor.open_project(resolve_drp)
        project.remove_object(project.timelines[0])
        project.undo_last()
        assert len(project.timelines) == 1
        assert len(project.media_pool) == 2
        out = tmp_path / "restored.drp"
        project.save(out)
        assert out.read_bytes() == resolve_drp.read_bytes()

    def test_rename_timeline_writes_through_handle(self, resolve_drp: Path, tmp_path: Path) -> None:
        project = drp_editor.open_project(resolve_drp)
        project.set_property(project.timelines[0], "name", "Renamed")
        out = tmp_path / "renamed.drp"
        project.save(out)
        reopened = drp_editor.open_project(out)
        assert reopened.timelines[0].name == "Renamed"
        pool_xml = zipfile.ZipFile(out).read("MediaPool/Master/MpFolder.xml")
        assert b"<Name>Renamed</Name>" in pool_xml

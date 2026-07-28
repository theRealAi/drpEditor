"""Tests for parsing, the object model, search, editing, and round-trips."""

from __future__ import annotations

from pathlib import Path

import pytest

from drp_editor import DRPArchive, DRPParser, open_project
from drp_editor.exceptions import PatchError
from drp_editor.fields_blob import BlobSchemaRegistry

from .conftest import BINARY_MEMBER, SUPER_SCALE_OFFSET


@pytest.fixture()
def project(drp_file: Path, clip_registry: BlobSchemaRegistry):
    archive = DRPArchive.open(drp_file)
    return DRPParser(registry=clip_registry).parse(archive)


class TestParsing:
    def test_project_name(self, project):
        assert project.name == "Demo Project"

    def test_enumerate_timelines(self, project):
        assert [t.name for t in project.timelines] == ["Main Timeline", "Second Timeline"]
        assert project.timelines[0].uuid == "t-001"

    def test_enumerate_clips(self, project):
        assert len(project.all_clips()) == 3
        assert [c.uuid for c in project.timelines[0].clips] == ["c-001", "c-002"]

    def test_clip_with_child_element_properties(self, project):
        clip = project.find_clip(uuid="c-003")
        assert clip is not None
        assert clip.name == "NestedNameClip"
        assert clip.fields_blob is not None

    def test_enumerate_media_pool(self, project):
        assert [m.uuid for m in project.media_pool] == ["m-001", "m-002"]
        assert project.media_pool[0].file_path == "/media/Camera001.mov"

    def test_settings(self, project):
        assert project.settings is not None
        assert project.settings.values["FrameRate"] == "24"

    def test_clip_timeline_ownership(self, project):
        clip = project.find_clip(uuid="c-001")
        assert clip.timeline_uuid == "t-001"
        assert project.unattached_clips == []

    def test_blob_lazily_decoded(self, project):
        clip = project.find_clip(uuid="c-001")
        assert clip.blob_holder is not None
        assert not clip.blob_holder.loaded
        assert clip.fields_blob is not None
        assert clip.blob_holder.loaded


class TestSearch:
    def test_find_clip_by_name_and_uuid(self, project):
        assert project.find_clip(name="Camera001.mov").uuid == "c-001"
        assert project.find_clip(uuid="c-002").name == "Camera002.mov"
        assert project.find_clip(uuid="missing") is None

    def test_find_timeline(self, project):
        assert project.find_timeline(name="Main Timeline").uuid == "t-001"
        assert project.find_timeline(uuid="t-002").name == "Second Timeline"

    def test_find_media(self, project):
        assert project.find_media(uuid="m-001").name == "Camera001.mov"
        assert project.find_media(name="Camera002.mov").uuid == "m-002"

    def test_regex_search(self, project):
        hits = project.search(r"Camera00\d")
        types = {h.object_type for h in hits}
        assert types == {"clip", "media"}
        assert any(h.property == "file_path" for h in hits)

    def test_requires_criteria(self, project):
        with pytest.raises(ValueError):
            project.find_clip()


class TestRoundTrip:
    def test_unmodified_project_saves_byte_identical(self, project, drp_file, tmp_path):
        out = tmp_path / "copy.drp"
        project.save(out)
        assert out.read_bytes() == drp_file.read_bytes()

    def test_rename_clip_preserves_everything_else(
        self, project, tmp_path, clip_registry: BlobSchemaRegistry
    ):
        out = tmp_path / "renamed.drp"
        clip = project.find_clip(uuid="c-001")
        project.set_property(clip, "name", "Renamed.mov")
        project.save(out)

        reopened = DRPParser(registry=clip_registry).parse(DRPArchive.open(out))
        assert reopened.find_clip(uuid="c-001").name == "Renamed.mov"
        # Unknown XML and binary members untouched.
        xml = reopened.archive.read("project.xml")
        assert b"exported by the drp_editor test suite" in xml
        assert b'<UnknownFutureBlock keep="me">' in xml
        assert reopened.archive.read("render/thumbnail.bin") == BINARY_MEMBER
        # Untouched blobs keep their exact hex.
        c2 = reopened.find_clip(uuid="c-002")
        assert c2.fields_blob.raw_bytes == project.find_clip(uuid="c-002").fields_blob.raw_bytes

    def test_blob_field_edit_touches_one_byte(self, project, tmp_path, clip_registry):
        clip = project.find_clip(uuid="c-001")
        before = clip.fields_blob.raw_bytes
        project.set_blob_field(clip, "super_scale", 0)
        out = tmp_path / "blob.drp"
        project.save(out)

        reopened = DRPParser(registry=clip_registry).parse(DRPArchive.open(out))
        after = reopened.find_clip(uuid="c-001").fields_blob.raw_bytes
        assert after[SUPER_SCALE_OFFSET] == 0
        assert before[:SUPER_SCALE_OFFSET] == after[:SUPER_SCALE_OFFSET]
        assert before[SUPER_SCALE_OFFSET + 1 :] == after[SUPER_SCALE_OFFSET + 1 :]

    def test_open_project_helper(self, drp_file):
        project = open_project(drp_file)
        assert project.name == "Demo Project"


class TestMutation:
    def test_set_property_records_patch(self, project):
        clip = project.find_clip(uuid="c-001")
        patch = project.set_property(clip, "name", "New Name")
        assert patch.old_value == "Camera001.mov"
        assert patch.new_value == "New Name"
        assert len(project.patch_log) == 1
        # cache updated
        assert project.find_clip(name="New Name") is clip

    def test_set_property_without_carrier_raises(self, project):
        clip = project.find_clip(uuid="c-001")
        with pytest.raises(PatchError):
            project.set_property(clip, "nonexistent", "x")

    def test_set_blob_field_records_patch(self, project):
        clip = project.find_clip(uuid="c-001")
        patch = project.set_blob_field(clip, "super_scale", 0)
        assert patch.property == "fields_blob.super_scale"
        assert patch.old_value == "01"
        assert patch.new_value == "00"

    def test_undo_property_change(self, project):
        clip = project.find_clip(uuid="c-001")
        project.set_property(clip, "name", "Temporary")
        undone = project.undo_last()
        assert undone is not None
        assert clip.name == "Camera001.mov"
        assert len(project.patch_log) == 0

    def test_undo_blob_change(self, project):
        clip = project.find_clip(uuid="c-001")
        project.set_blob_field(clip, "super_scale", 0)
        project.undo_last()
        assert clip.fields_blob.get_field("super_scale") == 1

    def test_remove_clip(self, project, tmp_path, clip_registry):
        clip = project.find_clip(uuid="c-002")
        patch = project.remove_object(clip)
        assert patch.property == "__removed__"
        assert project.find_clip(uuid="c-002") is None
        assert len(project.timelines[0].clips) == 1

        out = tmp_path / "removed.drp"
        project.save(out)
        reopened = DRPParser(registry=clip_registry).parse(DRPArchive.open(out))
        assert reopened.find_clip(uuid="c-002") is None
        assert reopened.find_clip(uuid="c-001") is not None
        assert b"c-002" not in reopened.archive.read("project.xml")

    def test_remove_timeline_removes_its_clips(self, project):
        timeline = project.find_timeline(uuid="t-002")
        project.remove_object(timeline)
        assert project.find_timeline(uuid="t-002") is None
        assert project.find_clip(uuid="c-003") is None
        assert len(project.all_clips()) == 2

    def test_remove_media_item(self, project):
        item = project.find_media(uuid="m-002")
        project.remove_object(item)
        assert project.find_media(uuid="m-002") is None
        assert len(project.media_pool) == 1

    def test_undo_removal_restores_object_and_position(self, project):
        timeline = project.timelines[0]
        clip = project.find_clip(uuid="c-001")
        project.remove_object(clip)
        assert timeline.clips[0].uuid == "c-002"

        project.undo_last()
        assert project.find_clip(uuid="c-001") is clip
        assert [c.uuid for c in timeline.clips] == ["c-001", "c-002"]
        # XML node is back in its original slot too.
        assert clip.xml_node.getparent() is not None
        assert b"c-001" in project.document.to_bytes()

    def test_remove_twice_raises(self, project):
        clip = project.find_clip(uuid="c-001")
        project.remove_object(clip)
        with pytest.raises(PatchError):
            project.remove_object(clip)

    def test_export_to_dict(self, project):
        data = project.to_dict(include_fields=True)
        assert data["name"] == "Demo Project"
        assert len(data["timelines"]) == 2
        blob_info = data["timelines"][0]["clips"][0]["fields_blob"]
        assert blob_info["known_fields"]["super_scale"] == 1

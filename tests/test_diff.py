"""Tests for the project diff engine."""

from __future__ import annotations

from pathlib import Path

import pytest

from drp_editor import DRPArchive, DRPParser
from drp_editor.diff import diff_projects, format_project_diff
from drp_editor.fields_blob import BlobSchemaRegistry

from .conftest import SUPER_SCALE_OFFSET, build_drp


def load(path: Path, registry: BlobSchemaRegistry):
    return DRPParser(registry=registry).parse(DRPArchive.open(path))


@pytest.fixture()
def two_copies(tmp_path: Path):
    return build_drp(tmp_path / "a.drp"), build_drp(tmp_path / "b.drp")


class TestDiff:
    def test_identical_projects_no_changes(self, two_copies, clip_registry):
        old = load(two_copies[0], clip_registry)
        new = load(two_copies[1], clip_registry)
        diff = diff_projects(old, new)
        assert diff.empty
        assert format_project_diff(diff) == "No changes"

    def test_property_change_detected(self, two_copies, clip_registry, tmp_path):
        old = load(two_copies[0], clip_registry)
        modified = load(two_copies[1], clip_registry)
        modified.set_property(modified.find_clip(uuid="c-001"), "name", "Renamed.mov")
        saved = tmp_path / "renamed.drp"
        modified.save(saved)

        diff = diff_projects(old, load(saved, clip_registry))
        assert len(diff.timelines) == 1
        clip_diff = diff.timelines[0].clips[0]
        assert clip_diff.properties[0].property == "name"
        assert clip_diff.properties[0].new == "Renamed.mov"

    def test_blob_byte_change_reports_offset(self, two_copies, clip_registry, tmp_path):
        old = load(two_copies[0], clip_registry)
        modified = load(two_copies[1], clip_registry)
        modified.set_blob_field(modified.find_clip(uuid="c-001"), "super_scale", 0)
        saved = tmp_path / "fixed.drp"
        modified.save(saved)

        diff = diff_projects(old, load(saved, clip_registry))
        clip_diff = diff.timelines[0].clips[0]
        assert len(clip_diff.blob_changes) == 1
        change = clip_diff.blob_changes[0]
        assert change.offset == SUPER_SCALE_OFFSET
        assert change.old == b"\x01"
        assert change.new == b"\x00"
        assert clip_diff.blob_fields == {"super_scale": (1, 0)}

        report = format_project_diff(diff)
        assert f"Offset 0x{SUPER_SCALE_OFFSET:04x}" in report
        assert "Old: 01" in report
        assert "New: 00" in report

    def test_removed_timeline_reported(self, two_copies, clip_registry):
        old = load(two_copies[0], clip_registry)
        new = load(two_copies[1], clip_registry)
        new.timelines.pop()
        new._build_caches()
        diff = diff_projects(old, new)
        assert any(t.status == "removed" for t in diff.timelines)

    def test_binary_member_change_reported(self, two_copies, clip_registry):
        old = load(two_copies[0], clip_registry)
        new = load(two_copies[1], clip_registry)
        new.archive.replace("render/thumbnail.bin", b"different")
        diff = diff_projects(old, new)
        assert any(c.property == "render/thumbnail.bin" for c in diff.archive_members)

"""Tests for the repair plugin framework and the AI upscale repair."""

from __future__ import annotations

from pathlib import Path

import pytest

from drp_editor import DRPArchive, DRPParser
from drp_editor.exceptions import RepairError
from drp_editor.fields_blob import BlobSchemaRegistry
from drp_editor.repairs import available_repairs, get_repair
from drp_editor.repairs.ai_upscale import AIUpscaleRepair

from .conftest import SUPER_SCALE_OFFSET


def load(path: Path, registry: BlobSchemaRegistry):
    return DRPParser(registry=registry).parse(DRPArchive.open(path))


class TestRegistry:
    def test_ai_upscale_registered(self):
        assert "ai-upscale" in available_repairs()
        assert isinstance(get_repair("ai-upscale"), AIUpscaleRepair)

    def test_unknown_repair_raises(self):
        with pytest.raises(RepairError):
            get_repair("does-not-exist")


class TestAIUpscaleRepair:
    def test_scan_finds_enabled_clips(self, drp_file, clip_registry):
        project = load(drp_file, clip_registry)
        findings = AIUpscaleRepair().scan(project)
        # c-001 and c-003 have super_scale=1 in the fixture.
        assert {f.object_id for f in findings} == {"c-001", "c-003"}

    def test_repair_disables_and_validates(self, drp_file, clip_registry, tmp_path):
        project = load(drp_file, clip_registry)
        plugin = AIUpscaleRepair()
        patches = plugin.repair(project)
        assert len(patches) == 2
        assert plugin.validate(project) == []

        out = tmp_path / "fixed.drp"
        project.save(out)
        reopened = load(out, clip_registry)
        for uuid in ("c-001", "c-002", "c-003"):
            clip = reopened.find_clip(uuid=uuid)
            assert clip.fields_blob.get_field("super_scale") == 0

    def test_repair_touches_only_super_scale_byte(self, drp_file, clip_registry, tmp_path):
        original = load(drp_file, clip_registry)
        before = original.find_clip(uuid="c-001").fields_blob.raw_bytes

        project = load(drp_file, clip_registry)
        AIUpscaleRepair().repair(project)
        out = tmp_path / "fixed.drp"
        project.save(out)

        after = load(out, clip_registry).find_clip(uuid="c-001").fields_blob.raw_bytes
        assert after[SUPER_SCALE_OFFSET] == 0
        assert before[:SUPER_SCALE_OFFSET] == after[:SUPER_SCALE_OFFSET]
        assert before[SUPER_SCALE_OFFSET + 1 :] == after[SUPER_SCALE_OFFSET + 1 :]

    def test_unmapped_field_raises_helpful_error(self, drp_file):
        project = load(drp_file, BlobSchemaRegistry())  # no schema
        with pytest.raises(RepairError, match="signature database"):
            AIUpscaleRepair().scan(project)

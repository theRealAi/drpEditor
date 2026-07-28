"""CLI smoke tests using Typer's test runner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from drp_editor.cli import app

from .conftest import SUPER_SCALE_OFFSET, build_drp

runner = CliRunner()


@pytest.fixture()
def signatures(tmp_path: Path) -> Path:
    db = tmp_path / "signatures.json"
    db.write_text(
        json.dumps(
            {
                "clip": [
                    {
                        "name": "super_scale",
                        "offset": SUPER_SCALE_OFFSET,
                        "type": "uint8",
                        "description": "test super scale field",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return db


class TestReadOnlyCommands:
    def test_info(self, drp_file: Path):
        result = runner.invoke(app, ["info", str(drp_file)])
        assert result.exit_code == 0
        assert "Demo Project" in result.output

    def test_timelines(self, drp_file: Path):
        result = runner.invoke(app, ["timelines", str(drp_file)])
        assert result.exit_code == 0
        assert "Main Timeline" in result.output

    def test_clips(self, drp_file: Path):
        result = runner.invoke(app, ["clips", str(drp_file)])
        assert result.exit_code == 0
        assert "c-001" in result.output

    def test_clips_filtered_by_timeline(self, drp_file: Path):
        result = runner.invoke(app, ["clips", str(drp_file), "--timeline", "Second Timeline"])
        assert result.exit_code == 0
        assert "c-003" in result.output
        assert "c-001" not in result.output

    def test_media(self, drp_file: Path):
        result = runner.invoke(app, ["media", str(drp_file)])
        assert result.exit_code == 0
        assert "m-001" in result.output

    def test_search(self, drp_file: Path):
        result = runner.invoke(app, ["search", str(drp_file), "Camera001"])
        assert result.exit_code == 0
        assert "c-001" in result.output

    def test_validate_clean(self, drp_file: Path):
        result = runner.invoke(app, ["validate", str(drp_file)])
        assert result.exit_code == 0
        assert "no issues" in result.output

    def test_validate_broken_exits_nonzero(self, tmp_path: Path):
        from .conftest import SAMPLE_XML

        broken = build_drp(
            tmp_path / "broken.drp",
            xml=SAMPLE_XML.replace(b'Uuid="c-002"', b'Uuid="c-001"'),
        )
        result = runner.invoke(app, ["validate", str(broken)])
        assert result.exit_code == 1
        assert "duplicate-uuid" in result.output

    def test_dump_fields(self, drp_file: Path, signatures: Path):
        result = runner.invoke(
            app,
            ["--signatures", str(signatures), "dump-fields", str(drp_file), "--clip", "c-001"],
        )
        assert result.exit_code == 0
        assert "super_scale" in result.output
        assert "00000040" in result.output  # hex dump row containing offset 0x48

    def test_missing_file_is_clean_error(self, tmp_path: Path):
        result = runner.invoke(app, ["info", str(tmp_path / "ghost.drp")])
        assert result.exit_code != 0


class TestDiffCommand:
    def test_diff_identical(self, tmp_path: Path):
        a = build_drp(tmp_path / "a.drp")
        b = build_drp(tmp_path / "b.drp")
        result = runner.invoke(app, ["diff", str(a), str(b)])
        assert result.exit_code == 0
        assert "No changes" in result.output

    def test_diff_after_patch(self, drp_file: Path, tmp_path: Path):
        out = tmp_path / "patched.drp"
        patch_result = runner.invoke(
            app,
            ["patch", str(drp_file), str(out), "--set", "c-001.name=Renamed.mov"],
        )
        assert patch_result.exit_code == 0
        diff_result = runner.invoke(app, ["diff", str(drp_file), str(out)])
        assert diff_result.exit_code == 0
        assert "Renamed.mov" in diff_result.output


class TestPatchCommand:
    def test_patch_blob_field(self, drp_file: Path, tmp_path: Path, signatures: Path):
        out = tmp_path / "patched.drp"
        log = tmp_path / "patches.json"
        result = runner.invoke(
            app,
            [
                "--signatures",
                str(signatures),
                "patch",
                str(drp_file),
                str(out),
                "--set",
                "c-001.fields_blob.super_scale=0",
                "--log",
                str(log),
            ],
        )
        assert result.exit_code == 0
        assert out.exists()
        records = json.loads(log.read_text(encoding="utf-8"))
        assert records[0]["property"] == "fields_blob.super_scale"

    def test_malformed_set_fails_cleanly(self, drp_file: Path, tmp_path: Path):
        result = runner.invoke(
            app, ["patch", str(drp_file), str(tmp_path / "x.drp"), "--set", "nonsense"]
        )
        assert result.exit_code == 2


class TestJsonCommands:
    def test_export_json(self, drp_file: Path, tmp_path: Path):
        out = tmp_path / "export.json"
        result = runner.invoke(app, ["export-json", str(drp_file), "--output", str(out)])
        assert result.exit_code == 0
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["name"] == "Demo Project"
        assert len(data["timelines"]) == 2

    def test_export_import_round_trip(self, drp_file: Path, tmp_path: Path):
        export = tmp_path / "export.json"
        runner.invoke(app, ["export-json", str(drp_file), "--output", str(export)])
        data = json.loads(export.read_text(encoding="utf-8"))
        data["timelines"][0]["clips"][0]["name"] = "FromJson.mov"
        export.write_text(json.dumps(data), encoding="utf-8")

        out = tmp_path / "imported.drp"
        result = runner.invoke(app, ["import-json", str(drp_file), str(export), str(out)])
        assert result.exit_code == 0

        check = runner.invoke(app, ["clips", str(out)])
        assert "FromJson.mov" in check.output


class TestRepairCommands:
    def test_repair_list(self):
        result = runner.invoke(app, ["repair", "--list"])
        assert result.exit_code == 0
        assert "ai-upscale" in result.output

    def test_repair_ai_upscale_end_to_end(self, drp_file: Path, tmp_path: Path, signatures: Path):
        out = tmp_path / "fixed.drp"
        result = runner.invoke(
            app,
            ["--signatures", str(signatures), "repair-ai-upscale", str(drp_file), str(out)],
        )
        assert result.exit_code == 0, result.output
        assert out.exists()
        assert "applied 2 patch(es)" in result.output

    def test_repair_dry_run_writes_nothing(self, drp_file: Path, tmp_path: Path, signatures: Path):
        out = tmp_path / "never.drp"
        result = runner.invoke(
            app,
            [
                "--signatures",
                str(signatures),
                "repair",
                "ai-upscale",
                str(drp_file),
                str(out),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        assert not out.exists()
        assert "found:" in result.output

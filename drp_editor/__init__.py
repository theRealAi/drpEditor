"""drp_editor: a parser, editor, and reverse-engineering toolkit for
DaVinci Resolve ``.drp`` project files.

Quick start::

    import drp_editor

    project = drp_editor.open_project("MyProject.drp")
    for timeline in project.timelines:
        print(timeline.name, len(timeline.clips))
    project.save("MyProject.copy.drp")

Data-preservation guarantee: anything you do not explicitly modify is
written back byte-for-byte identical.
"""

from __future__ import annotations

from pathlib import Path

from .archive import DRPArchive
from .binary import BinaryReader, BinaryWriter
from .diff import diff_projects, format_project_diff
from .exceptions import (
    ArchiveError,
    BinaryDecodeError,
    DRPError,
    PatchError,
    RepairError,
    SaveError,
    ValidationError,
    XMLParseError,
)
from .fields_blob import (
    BlobSchema,
    BlobSchemaRegistry,
    FieldsBlob,
    FieldSpec,
    default_registry,
)
from .models import Clip, MediaItem, Project, Settings, Timeline
from .parser import DRPParser, ParserConfig
from .patch import Patch, PatchLog
from .validation import ValidationIssue, Validator

__version__ = "0.1.0"

__all__ = [
    "ArchiveError",
    "BinaryDecodeError",
    "BinaryReader",
    "BinaryWriter",
    "BlobSchema",
    "BlobSchemaRegistry",
    "Clip",
    "DRPArchive",
    "DRPError",
    "DRPParser",
    "FieldSpec",
    "FieldsBlob",
    "MediaItem",
    "ParserConfig",
    "Patch",
    "PatchError",
    "PatchLog",
    "Project",
    "RepairError",
    "SaveError",
    "Settings",
    "Timeline",
    "ValidationError",
    "ValidationIssue",
    "Validator",
    "XMLParseError",
    "default_registry",
    "diff_projects",
    "format_project_diff",
    "open_project",
]


def open_project(
    path: Path | str,
    *,
    config: ParserConfig | None = None,
    registry: BlobSchemaRegistry | None = None,
) -> Project:
    """Open and parse a .drp file in one call.

    Args:
        path: Path to the ``.drp`` file.
        config: Optional parser configuration for non-default schemas.
        registry: Optional blob schema registry (defaults to the
            process-wide :data:`default_registry`).

    Returns:
        The parsed :class:`Project`.
    """
    archive = DRPArchive.open(path)
    parser = DRPParser(
        config=config or ParserConfig(),
        registry=registry if registry is not None else default_registry,
    )
    return parser.parse(archive)

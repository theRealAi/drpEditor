"""Custom exception hierarchy for drp_editor.

All exceptions raised intentionally by this package derive from
:class:`DRPError`, so callers can catch a single base class.
"""

from __future__ import annotations

__all__ = [
    "ArchiveError",
    "BinaryDecodeError",
    "DRPError",
    "PatchError",
    "RepairError",
    "SaveError",
    "ValidationError",
    "XMLParseError",
]


class DRPError(Exception):
    """Base class for every error raised by drp_editor."""


class ArchiveError(DRPError):
    """Raised when a .drp archive cannot be opened, read, or verified."""


class XMLParseError(DRPError):
    """Raised when XML inside the archive cannot be parsed."""


class ValidationError(DRPError):
    """Raised when a project fails a hard validation check."""


class BinaryDecodeError(DRPError):
    """Raised when a binary blob cannot be decoded or encoded."""


class PatchError(DRPError):
    """Raised when a patch cannot be created, applied, or reverted."""


class SaveError(DRPError):
    """Raised when a project or archive cannot be written to disk."""


class RepairError(DRPError):
    """Raised when a repair plugin fails to scan or repair a project."""

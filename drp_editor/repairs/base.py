"""Repair plugin framework.

A *repair* is a self-contained scan/fix/verify unit targeting one class
of project damage. Plugins subclass :class:`Repair` and register
themselves with :func:`register`, which makes them discoverable by name
from the CLI (``drp repair <name> ...``).

Contract:

* :meth:`Repair.scan` is read-only and returns findings.
* :meth:`Repair.repair` fixes the findings through the normal patch
  machinery (so every change is auditable and undoable).
* :meth:`Repair.validate` re-checks the project after repair.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from ..exceptions import RepairError
from ..models import Project
from ..patch import Patch
from ..validation import ValidationIssue, Validator

__all__ = ["Repair", "RepairFinding", "available_repairs", "get_repair", "register"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RepairFinding:
    """One instance of damage located by a repair's scan."""

    object_id: str
    description: str
    data: dict[str, Any] = field(default_factory=dict)


class Repair(ABC):
    """Base class for all repair plugins."""

    #: CLI-facing identifier, e.g. ``"ai-upscale"``.
    name: ClassVar[str]
    #: One-line human description shown in ``drp repair --list``.
    description: ClassVar[str]

    @abstractmethod
    def scan(self, project: Project) -> list[RepairFinding]:
        """Locate damage without modifying anything."""

    @abstractmethod
    def repair(self, project: Project) -> list[Patch]:
        """Fix all damage found by :meth:`scan`, returning applied patches."""

    def validate(self, project: Project) -> list[ValidationIssue]:
        """Post-repair verification.

        Default implementation: the scan must come back clean and the
        standard validator must report no errors.
        """
        issues = [i for i in Validator(project).run() if i.severity == "error"]
        issues.extend(
            ValidationIssue(
                severity="error",
                code=f"{self.name}-still-present",
                message=finding.description,
                object_id=finding.object_id,
            )
            for finding in self.scan(project)
        )
        return issues


_REGISTRY: dict[str, type[Repair]] = {}


def register(cls: type[Repair]) -> type[Repair]:
    """Class decorator adding a repair to the global registry."""
    if cls.name in _REGISTRY:
        raise RepairError(f"repair {cls.name!r} registered twice")
    _REGISTRY[cls.name] = cls
    logger.debug("registered repair plugin %r", cls.name)
    return cls


def get_repair(name: str) -> Repair:
    """Instantiate a registered repair by name."""
    try:
        return _REGISTRY[name]()
    except KeyError as exc:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise RepairError(f"unknown repair {name!r}; available: {known}") from exc


def available_repairs() -> dict[str, type[Repair]]:
    """Copy of the registry: name -> plugin class."""
    return dict(_REGISTRY)

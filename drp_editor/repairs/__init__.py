"""Repair plugins for common .drp damage.

Importing this package registers all built-in plugins. Third-party
plugins register themselves by importing and applying
:func:`drp_editor.repairs.base.register`.
"""

from . import ai_upscale as _ai_upscale  # noqa: F401  (registers the plugin)
from .base import Repair, RepairFinding, available_repairs, get_repair, register

__all__ = ["Repair", "RepairFinding", "available_repairs", "get_repair", "register"]

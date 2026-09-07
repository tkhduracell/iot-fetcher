"""Registration for every read-only backend tool.

These are the agents' senses: what the house is measuring (VictoriaMetrics),
what its devices are doing right now (Home Assistant), what the user has
written down (Drive), what the rest of the world says (web), and what the
agent's own token budget looks like (usage). All of them read; none of them
act.
"""

from __future__ import annotations

from ai_brain.tools import ToolRegistry
from ai_brain.tools.drive import register_drive_tools
from ai_brain.tools.ha import register_ha_tools
from ai_brain.tools.usage import register_usage_tools
from ai_brain.tools.vm import register_vm_tools
from ai_brain.tools.web import register_web_tools


def register_backend_tools(registry: ToolRegistry) -> None:
    register_vm_tools(registry)
    register_ha_tools(registry)
    register_drive_tools(registry)
    register_web_tools(registry)
    register_usage_tools(registry)

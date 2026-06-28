"""Live-event seam — the layer-legal bridge from ``api/`` (SSE) to the bus.

The in-process fan-out itself is ``core.events`` infra. ``api/`` reaches it
through this thin ``services/`` re-export (api → services → core) rather than
importing a ``core`` internal directly, keeping the layer firewall intact (the
api layer may import only ``core.config`` + ``core.logging``).
"""

from __future__ import annotations

from core.events import subscribe

__all__ = ["subscribe"]

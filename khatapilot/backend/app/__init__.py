"""KhataPilot backend: a layered modular monolith with a guard in the middle.

Layers, top to bottom (dependencies only point down; see docs/ARCHITECTURE.md):
    interface -> agent -> guard -> modules -> infra -> core
"""

__version__ = "0.1.0"

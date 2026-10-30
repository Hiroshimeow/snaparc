# src/plugins/base.py
from __future__ import annotations
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.utils.DataPacket import DataPacket

class PluginBase:
    """Base class for all plugins in SnapArc."""
    
    def __init__(self):
        self.enabled = True
        self.name = self.__class__.__name__

    def on_load(self):
        """Called when the plugin is loaded."""
        pass

    def on_unload(self):
        """Called when the plugin is unloaded."""
        pass

    def process_data(self, data: "DataPacket") -> "DataPacket":
        """Process a DataPacket. Override this to modify data flow."""
        return data

    def get_ui_elements(self) -> list[Any]:
        """Return a list of UI elements (e.g., menu items) to be added to the main UI."""
        return []

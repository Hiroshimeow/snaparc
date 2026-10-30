# src/plugins/manager.py
import os
import importlib
import inspect
from typing import List, Dict
from src.plugins.base import PluginBase

class PluginManager:
    """Manages the discovery and lifecycle of plugins."""
    _instance = None
    _plugins: Dict[str, PluginBase] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(PluginManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self.plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self.load_all_plugins()
        self._initialized = True

    def load_all_plugins(self):
        """Dynamically load all Python files in the plugin directory as plugins."""
        for filename in os.listdir(self.plugin_dir):
            if filename.endswith(".py") and filename not in ["__init__.py", "base.py", "manager.py"]:
                module_name = f"src.plugins.{filename[:-3]}"
                try:
                    module = importlib.import_module(module_name)
                    importlib.reload(module)  # Ensure fresh reload
                    
                    for name, obj in inspect.getmembers(module):
                        if inspect.isclass(obj) and issubclass(obj, PluginBase) and obj is not PluginBase:
                            plugin_instance = obj()
                            self._plugins[plugin_instance.name] = plugin_instance
                            plugin_instance.on_load()
                            print(f"Plugin loaded: {plugin_instance.name}")
                except Exception as e:
                    print(f"Failed to load plugin from {filename}: {e}")

    def get_plugin(self, name: str) -> PluginBase:
        """Get a specific plugin by name."""
        return self._plugins.get(name)

    def list_plugins(self) -> List[str]:
        """List all loaded plugin names."""
        return list(self._plugins.keys())

    def broadcast_process_data(self, data):
        """Pass data through all enabled plugins."""
        for plugin in self._plugins.values():
            if plugin.enabled:
                data = plugin.process_data(data)
        return data

    def get_all_ui_elements(self) -> List:
        """Collect UI elements from all enabled plugins."""
        all_ui = []
        for plugin in self._plugins.values():
            if plugin.enabled:
                all_ui.extend(plugin.get_ui_elements())
        return all_ui

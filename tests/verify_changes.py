import os
import sys

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

print("--- 1. Testing Imports (v1.1) ---")
try:
    from src.utils.DataPacket import DataPacket
    print("✅ DataPacket imported")
    from src.core.model_state import ModelStateStore
    from src.services.radial_plugin_service import RadialPluginService
    from src.gui.quick_prompt_popup import QuickPromptPopup
    _ = (ModelStateStore, RadialPluginService, QuickPromptPopup)
    print("✅ v1.1 Core & Radial/Popup services imported")
    from src.models.Gemini import Gemini
    print("✅ Gemini imported")
    from src.models.new_api import NewApiProvider
    print("✅ NewApiProvider imported")
except ImportError as e:
    print(f"❌ Import failed: {e}")
    sys.exit(1)

print("\n--- 2. Testing DataPacket Factory ---")
try:
    p = DataPacket.from_gemini_result(
        result={"text": "Hello", "api_index": "1", "key_mask": "1234"},
        timestamp_str="20231027_120000",
        save_path="test.png",
        is_comparison_rerun=False,
        force_new_window=False,
        model_name="test-model",
        prompt_name="test-prompt",
        rerun_callback_provider=lambda: None,
    )
    print(f"✅ DataPacket created: {p}")
except Exception as e:
    print(f"❌ DataPacket factory failed: {e}")

print("\n--- 3. Testing Gemini Provider System & Caching ---")
try:
    g = Gemini()
    print(f"   Initial Provider: {g.provider}")
    print(f"   Initial Model: {g.model}")

    config1, _ = g._load_config()
    config2, _ = g._load_config()

    if config1 is config2:
        print("✅ Config caching WORKING (same object returned)")
    else:
        print("❌ Config caching FAILED (different objects)")

    print("   Switching to 'NewApi'...")
    g.set_provider("NewApi")
    if g.provider == "NewApi":
        print("✅ Provider switch to NewApi successful")
    else:
        print(f"❌ Provider switch failed: {g.provider}")

    print("   Resetting to 'Oath'...")
    g.set_provider("Oath")
    if g.provider == "Oath":
        print("✅ Provider reset to Oath successful")
except Exception as e:
    print(f"❌ Gemini test failed: {e}")
    import traceback
    traceback.print_exc()

print("\n--- 4. Testing NewApiProvider Init ---")
try:
    n = NewApiProvider()
    print(f"✅ NewApiProvider initialized. Base URL: {n.base_url}")
except Exception as e:
    print(f"❌ NewApiProvider init failed: {e}")

print("\n--- All Checks Finished ---")

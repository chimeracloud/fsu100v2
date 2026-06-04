"""Phase 2 — plugin loader + registry."""
import pytest

from core.config import replace_settings, reset_settings_for_test
from core.state import reset_state_for_test
from services import plugin_loader


@pytest.fixture(autouse=True)
def _isolated():
    reset_settings_for_test()
    reset_state_for_test()
    plugin_loader.reset_for_test()
    yield
    reset_settings_for_test()
    reset_state_for_test()
    plugin_loader.reset_for_test()


def test_load_mark_6_rules_v1():
    plugin_loader.load_all_plugins()
    summary = plugin_loader.get_registry_summary()
    assert len(summary) == 1
    row = summary[0]
    assert row["id"] == "mark_6_rules_v1"
    assert row["status"] == "loaded"
    assert row["version"] == "1.0.0"

    inst = plugin_loader.get_plugin("mark_6_rules_v1")
    assert inst is not None
    assert inst.id == "mark_6_rules_v1"


def test_get_registry_entry_for_unknown_returns_none():
    plugin_loader.load_all_plugins()
    assert plugin_loader.get_registry_entry("does_not_exist") is None


def test_loading_unknown_plugin_records_failure():
    # Force an unknown plugin into Settings.loaded_plugins.
    replace_settings(loaded_plugins=("ghost_plugin",))
    plugin_loader.load_all_plugins()
    entry = plugin_loader.get_registry_entry("ghost_plugin")
    assert entry is not None
    assert entry["status"] == "failed"
    assert entry["last_error"]


def test_active_plugins_returns_loaded_instances_only():
    replace_settings(loaded_plugins=("mark_6_rules_v1", "ghost_plugin"))
    plugin_loader.load_all_plugins()
    actives = plugin_loader.active_plugins()
    assert len(actives) == 1
    assert actives[0].id == "mark_6_rules_v1"

"""
Plugin loader.

Discovers plugins listed in `Settings.loaded_plugins`, instantiates
each, calls `plugin.load(host_context=...)`, hydrates the parameters
from per-plugin GCS config (falling back to schema defaults), calls
`plugin.configure(parameters)`, and registers the plugin instance in
the host's in-memory registry.

The registry is the source-of-truth for `/api/plugins*` endpoints and
for the SSE consumer (Phase 3) which fans every market event through
every loaded plugin.

A plugin that raises during load/configure is recorded with status
'failed' and a `plugin_failed` event is published. The engine
continues — one bad plugin must not blow up the host.
"""
from __future__ import annotations

import importlib
import logging
from datetime import datetime, timezone
from threading import RLock
from typing import Any

from core.config import get_settings
from core.state import app_state

from .plugin_config import load_plugin_config

logger = logging.getLogger(__name__)


_lock = RLock()
_registry: dict[str, dict[str, Any]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _import_plugin(plugin_id: str):
    """Locate the plugin class. Convention:

        plugins.{plugin_id}.plugin           module
        plugins.{plugin_id}.plugin.<Class>   any class implementing the Protocol

    We find the class by looking for the first attribute that has both
    `id` and `on_market_event`. Future versions may use entry-points.
    """
    module = importlib.import_module(f"plugins.{plugin_id}.plugin")
    for attr_name in dir(module):
        if attr_name.startswith("_"):
            continue
        obj = getattr(module, attr_name)
        if isinstance(obj, type):
            try:
                instance = obj()
            except Exception:  # noqa: BLE001
                continue
            if getattr(instance, "id", None) == plugin_id and hasattr(instance, "on_market_event"):
                return instance
    raise RuntimeError(
        f"plugin '{plugin_id}': no class with id='{plugin_id}' found in plugins.{plugin_id}.plugin"
    )


def _record(plugin_id: str, *, status: str, error: str = "", instance: Any | None = None) -> None:
    with _lock:
        _registry[plugin_id] = {
            "id": plugin_id,
            "instance": instance,
            "version": getattr(instance, "version", "") if instance else "",
            "status": status,
            "last_error": error,
            "loaded_at": _now_iso(),
        }
    if status == "loaded":
        app_state.add_activity("plugin_loaded", f"{plugin_id} v{getattr(instance, 'version', '')}")
    elif status == "failed":
        app_state.add_activity("plugin_failed", f"{plugin_id}: {error}")


def load_all_plugins() -> dict[str, dict[str, Any]]:
    """Load + configure every plugin in `Settings.loaded_plugins`."""
    settings = get_settings()
    with _lock:
        _registry.clear()

    for plugin_id in settings.loaded_plugins:
        try:
            instance = _import_plugin(plugin_id)
            instance.load(host_context={"sport": "horse-racing"})
            persisted = load_plugin_config(plugin_id)
            if persisted is not None:
                instance.configure(persisted)
            # else: __init__ already seeded params from parameter_schema defaults.
            _record(plugin_id, status="loaded", instance=instance)
            logger.info(
                "plugin loaded: %s v%s", plugin_id, getattr(instance, "version", "?"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("plugin load failed: %s", plugin_id)
            _record(plugin_id, status="failed", error=str(exc))


def get_plugin(plugin_id: str):
    with _lock:
        entry = _registry.get(plugin_id)
        return entry["instance"] if entry else None


def get_registry_summary() -> list[dict[str, Any]]:
    """One row per known plugin id (loaded or failed), for /api/plugins."""
    with _lock:
        return [
            {
                "id": e["id"],
                "version": e["version"],
                "status": e["status"],
                "last_error": e["last_error"] or None,
                "loaded_at": e["loaded_at"],
            }
            for e in _registry.values()
        ]


def get_registry_entry(plugin_id: str) -> dict[str, Any] | None:
    with _lock:
        entry = _registry.get(plugin_id)
        if entry is None:
            return None
        return {k: v for k, v in entry.items() if k != "instance"}


def active_plugins() -> list[Any]:
    """Return live plugin instances (status='loaded'). Used by the
    dispatcher in Phase 3 to fan each event through every active plugin."""
    with _lock:
        return [e["instance"] for e in _registry.values() if e["status"] == "loaded"]


def reset_for_test() -> None:
    with _lock:
        _registry.clear()

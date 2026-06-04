"""
Set 3 — CONTENT: plugin endpoints.

  GET  /api/plugins                          list summary
  GET  /api/plugins/{id}                     one plugin's registry entry
  GET  /api/plugins/{id}/config              {schema, values}
  PUT  /api/plugins/{id}/config              validate → configure → persist
  GET  /api/plugins/{id}/state               runtime state
  GET  /api/plugins/{id}/results             session results

The host validates PUT bodies against `plugin.parameter_schema` (JSON
Schema draft 2020-12) before calling `plugin.configure(...)`. On
validation failure: 422 with the jsonschema error list. On persistence
failure: 502 with `applied_in_memory=True, persisted_to_gcs=False`.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from services.plugin_config import save_plugin_config
from services.plugin_loader import (
    get_plugin,
    get_registry_entry,
    get_registry_summary,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/plugins", tags=["plugins"])


def _plugin_or_404(plugin_id: str):
    entry = get_registry_entry(plugin_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"plugin '{plugin_id}' unknown")
    instance = get_plugin(plugin_id)
    if instance is None:
        # Status was 'failed' — return 503 so the operator sees the load error.
        raise HTTPException(
            status_code=503,
            detail={
                "ok": False,
                "plugin": plugin_id,
                "status": entry["status"],
                "last_error": entry.get("last_error"),
            },
        )
    return instance


@router.get("")
def list_plugins() -> dict[str, Any]:
    return {"plugins": get_registry_summary()}


@router.get("/{plugin_id}")
def plugin_detail(plugin_id: str) -> dict[str, Any]:
    entry = get_registry_entry(plugin_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"plugin '{plugin_id}' unknown")
    return entry


@router.get("/{plugin_id}/config")
def plugin_config(plugin_id: str) -> dict[str, Any]:
    plugin = _plugin_or_404(plugin_id)
    return {
        "schema": plugin.parameter_schema,
        "values": plugin.get_parameters(),
    }


@router.put("/{plugin_id}/config")
def plugin_config_put(plugin_id: str, body: dict = Body(...)) -> dict[str, Any]:
    plugin = _plugin_or_404(plugin_id)

    # Validate against the plugin's declared JSON Schema.
    try:
        import jsonschema
        from jsonschema import Draft202012Validator
    except Exception as exc:  # noqa: BLE001 — should never happen in prod
        raise HTTPException(
            status_code=500,
            detail=f"jsonschema unavailable: {exc}",
        )

    validator = Draft202012Validator(plugin.parameter_schema)
    errors = sorted(validator.iter_errors(body), key=lambda e: list(e.absolute_path))
    if errors:
        raise HTTPException(
            status_code=422,
            detail={
                "ok": False,
                "errors": [
                    {
                        "path": list(e.absolute_path),
                        "message": e.message,
                    }
                    for e in errors
                ],
            },
        )

    plugin.configure(body)

    persisted = save_plugin_config(plugin_id, body)
    if not persisted:
        raise HTTPException(
            status_code=502,
            detail={
                "ok": False,
                "plugin": plugin_id,
                "applied_in_memory": True,
                "persisted_to_gcs": False,
                "note": "config updated in memory but did NOT persist to GCS — retry PUT",
            },
        )

    return {
        "schema": plugin.parameter_schema,
        "values": plugin.get_parameters(),
        "persisted_to_gcs": True,
    }


@router.get("/{plugin_id}/state")
def plugin_state(plugin_id: str) -> dict[str, Any]:
    plugin = _plugin_or_404(plugin_id)
    return {"plugin_id": plugin_id, "state": plugin.get_state()}


@router.get("/{plugin_id}/results")
def plugin_results(plugin_id: str) -> dict[str, Any]:
    plugin = _plugin_or_404(plugin_id)
    return {"plugin_id": plugin_id, "results": plugin.get_results()}

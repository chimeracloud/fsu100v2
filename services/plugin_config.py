"""
Per-plugin config persistence.

Each plugin's business parameters live in their own GCS blob under
`gs://chiops-fsu100v2-config/plugins/{plugin_id}.json`. Same kill-switch
pattern as core/gcs_config.py.

The host validates incoming PUT payloads against
`plugin.parameter_schema` BEFORE calling save_plugin_config — this
module does NOT re-validate; it only persists.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from core.config import get_settings

logger = logging.getLogger(__name__)


def _disabled() -> bool:
    return bool(os.environ.get("FSU100V2_DISABLE_GCP_IO"))


def _bucket_and_blob(plugin_id: str) -> tuple[str, str]:
    s = get_settings()
    return s.config_bucket, f"{s.plugin_config_prefix}{plugin_id}.json"


def load_plugin_config(plugin_id: str) -> dict[str, Any] | None:
    """Return persisted plugin config or None if absent / GCS unavailable.

    Caller (the loader) decides what to do with None — usually fall back
    to the plugin's parameter_schema defaults.
    """
    if _disabled():
        return None
    bucket_name, blob_name = _bucket_and_blob(plugin_id)
    try:
        from google.cloud import storage  # type: ignore[import-not-found]

        client = storage.Client()
        blob = client.bucket(bucket_name).blob(blob_name)
        if not blob.exists():
            return None
        return json.loads(blob.download_as_text())
    except Exception as exc:  # noqa: BLE001
        logger.warning("plugin config load failed for %s: %s", plugin_id, exc)
        return None


def save_plugin_config(plugin_id: str, params: dict[str, Any]) -> bool:
    """Persist plugin config. Returns True on success, False on failure."""
    if _disabled():
        logger.info(
            "FSU100V2_DISABLE_GCP_IO set — skipping plugin %s config save.", plugin_id,
        )
        return True
    bucket_name, blob_name = _bucket_and_blob(plugin_id)
    try:
        from google.cloud import storage  # type: ignore[import-not-found]

        client = storage.Client()
        blob = client.bucket(bucket_name).blob(blob_name)
        blob.upload_from_string(
            json.dumps(params, indent=2),
            content_type="application/json",
        )
        logger.info("plugin config persisted: gs://%s/%s", bucket_name, blob_name)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("plugin config save failed for %s: %s", plugin_id, exc)
        return False

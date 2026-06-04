"""
Source Manifest read + register-best-effort (Bible §21).

Manifest lives at:
  gs://chimera-portal-config/source_manifest.json

FSU100V2 does two things:
  - READS the manifest on startup to discover URLs for fsu1b
    (data source), live_betting_control (executor), fsu2a (events).
    Discovered URLs are propagated into `Settings.fallback_urls` so
    subsequent reads are local.
  - WRITES its own entry on every successful boot (best-effort).
    POST /admin/control/reregister_source forces a retry.

Pattern lifted from FSU1B's source_manifest.py.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from core.config import get_settings, replace_settings

logger = logging.getLogger(__name__)


def _disabled() -> bool:
    return bool(os.environ.get("FSU100V2_DISABLE_GCP_IO"))


ENDPOINTS: dict[str, str] = {
    "stream_evaluations": "/stream/evaluations",
    "instructions": "/api/instructions",
    "session": "/api/session",
    "markets": "/api/markets",
    "admin_status": "/admin/status",
    "admin_config": "/admin/config",
    "plugins": "/api/plugins",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_manifest_entry() -> dict[str, Any]:
    """Construct FSU100V2's entry. Uses current Settings (url, plugins)."""
    s = get_settings()
    return {
        "name": "Horse Racing Lay Engine (v2 — pure decision engine)",
        "type": "decision_engine",
        "url": s.service_url,
        "status": "active",
        "sport": "horse_racing",
        "consumes_from": [s.source_id],
        "outputs_to": ["live_betting_control", "fsu2a"],
        "plugins_loaded": list(s.loaded_plugins),
        "endpoints": dict(ENDPOINTS),
        "last_registered": _now_iso(),
    }


def _read_manifest() -> dict[str, Any]:
    """Return the current manifest as a dict, or {} if unavailable."""
    if _disabled():
        return {}

    s = get_settings()
    try:
        from google.cloud import storage  # type: ignore[import-not-found]

        client = storage.Client()
        bucket = client.bucket(s.manifest_bucket)
        blob = bucket.blob(s.manifest_blob)
        if not blob.exists():
            return {}
        return json.loads(blob.download_as_text())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Manifest read failed: %s", exc)
        return {}


def refresh_discovery() -> dict[str, str]:
    """Read the manifest, extract URLs of interest, update fallback_urls.

    Returns a dict of {target: url} for whatever was discovered.
    """
    manifest = _read_manifest()
    discovered: dict[str, str] = {}

    for target in ("fsu1b", "live_betting_control", "fsu2a"):
        entry = manifest.get(target)
        if isinstance(entry, dict):
            url = entry.get("url", "")
            if url:
                discovered[target] = url

    if discovered:
        replace_settings(fallback_urls={**discovered})
        logger.info("Source-manifest discovery: %s", discovered)
    else:
        logger.info("Source-manifest discovery returned no relevant entries.")
    return discovered


def register() -> dict[str, Any]:
    """Read, merge our entry, write back. Raises on GCS failure."""
    if _disabled():
        logger.info("FSU100V2_DISABLE_GCP_IO set — returning entry without GCS write.")
        return build_manifest_entry()

    s = get_settings()
    bucket_name = s.manifest_bucket
    blob_name = s.manifest_blob

    from google.cloud import storage  # type: ignore[import-not-found]

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)

    if blob.exists():
        try:
            manifest = json.loads(blob.download_as_text())
            if not isinstance(manifest, dict):
                logger.warning(
                    "Manifest at gs://%s/%s is not an object — replacing.",
                    bucket_name, blob_name,
                )
                manifest = {}
        except json.JSONDecodeError as exc:
            logger.warning("Manifest unreadable (%s) — starting fresh.", exc)
            manifest = {}
    else:
        manifest = {}

    entry = build_manifest_entry()
    manifest[s.service_name] = entry

    blob.upload_from_string(
        json.dumps(manifest, indent=2, sort_keys=True),
        content_type="application/json",
    )
    logger.info(
        "Source manifest updated: gs://%s/%s (fsu100v2 → url=%r)",
        bucket_name, blob_name, entry["url"],
    )
    return entry


def register_best_effort() -> dict[str, Any] | None:
    try:
        return register()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Source manifest registration failed: %s", exc)
        return None

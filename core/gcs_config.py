"""
GCS-backed host config persistence (Phase 1).

Per CHI-POL-006. Pattern lifted verbatim from FSU1B: in-memory defaults
hydrate from `gs://{config_bucket}/{config_blob}` on startup; every
PUT /admin/config persists immediately. GCS unreachable on boot →
in-memory defaults, service still serves the admin surface.

Tests + local dev set FSU100V2_DISABLE_GCP_IO=1 so the GCP client is
never instantiated — prevents accidental writes to real buckets.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from core.config import apply_dict, get_settings, settings_to_dict

logger = logging.getLogger(__name__)


def _disabled() -> bool:
    return bool(os.environ.get("FSU100V2_DISABLE_GCP_IO"))


def _bucket_and_blob():
    s = get_settings()
    return s.config_bucket, s.config_blob


def load_config_from_gcs() -> dict[str, Any]:
    """Hydrate settings from GCS. Returns the dict applied.

    Missing blob → write defaults. GCS unreachable → log warning + use defaults.
    """
    if _disabled():
        logger.info("FSU100V2_DISABLE_GCP_IO set — skipping GCS config load.")
        return settings_to_dict()

    bucket_name, blob_name = _bucket_and_blob()
    try:
        from google.cloud import storage  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        logger.warning("GCS client unavailable (%s) — using in-memory defaults.", exc)
        return settings_to_dict()

    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        if blob.exists():
            payload = json.loads(blob.download_as_text())
            apply_dict(payload)
            logger.info("FSU100V2 config loaded from gs://%s/%s", bucket_name, blob_name)
            return settings_to_dict()
        defaults = settings_to_dict()
        blob.upload_from_string(
            json.dumps(defaults, indent=2),
            content_type="application/json",
        )
        logger.info(
            "FSU100V2 config absent — wrote defaults to gs://%s/%s",
            bucket_name, blob_name,
        )
        return defaults
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "FSU100V2 config load failed (%s) — using in-memory defaults.", exc,
        )
        return settings_to_dict()


def save_config_to_gcs(payload: dict[str, Any]) -> bool:
    """Apply the payload to in-memory settings then persist to GCS.

    Returns True on persistence success. False means in-memory took
    effect but GCS write failed — caller surfaces as 502 so operator retries.
    """
    apply_dict(payload)

    if _disabled():
        logger.info("FSU100V2_DISABLE_GCP_IO set — skipping GCS config save.")
        return True

    bucket_name, blob_name = _bucket_and_blob()
    try:
        from google.cloud import storage  # type: ignore[import-not-found]

        client = storage.Client()
        blob = client.bucket(bucket_name).blob(blob_name)
        blob.upload_from_string(
            json.dumps(settings_to_dict(), indent=2),
            content_type="application/json",
        )
        logger.info("FSU100V2 config persisted to gs://%s/%s", bucket_name, blob_name)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("FSU100V2 config persistence failed: %s", exc)
        return False

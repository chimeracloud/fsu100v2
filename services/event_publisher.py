"""
Event envelope publisher (Bible §20).

FSU100V2 emits *infrastructure* events only — never business events.
Order placement / settlement events belong to LBCF / FSU2A.

Envelope (locked by Bible §20):

    {
      "envelope": {
        "source":     "fsu100v2",
        "event_type": "engine_started",
        "timestamp":  "<UTC iso>",
        "version":    "1.0"
      },
      "payload": { ...event-specific... }
    }

Seven event types Phase 1+ fires:

  engine_started          — service up + plugins loaded + source ready
  engine_stopped          — graceful stop
  source_disconnected     — SSE source dropped
  source_recovered        — SSE source re-established
  plugin_loaded           — a plugin successfully loaded
  plugin_failed           — plugin load/configure raised
  engine_daily_summary    — 00:00 UTC daily — eval count, instruction count
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Literal

from core.config import get_settings
from core.state import app_state

logger = logging.getLogger(__name__)

ENVELOPE_VERSION = "1.0"


def _disabled() -> bool:
    return bool(os.environ.get("FSU100V2_DISABLE_GCP_IO"))


EventType = Literal[
    "engine_started",
    "engine_stopped",
    "source_disconnected",
    "source_recovered",
    "plugin_loaded",
    "plugin_failed",
    "engine_daily_summary",
]

VALID_EVENT_TYPES: set[str] = {
    "engine_started",
    "engine_stopped",
    "source_disconnected",
    "source_recovered",
    "plugin_loaded",
    "plugin_failed",
    "engine_daily_summary",
}


_publisher = None
_topic_path: str | None = None
_publisher_disabled = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_envelope(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if event_type not in VALID_EVENT_TYPES:
        raise ValueError(
            f"unknown event_type={event_type!r}; allowed={sorted(VALID_EVENT_TYPES)}"
        )
    return {
        "envelope": {
            "source": "fsu100v2",
            "event_type": event_type,
            "timestamp": _now_iso(),
            "version": ENVELOPE_VERSION,
        },
        "payload": payload,
    }


def _get_publisher():
    global _publisher, _topic_path, _publisher_disabled
    if _disabled() or _publisher_disabled:
        return None
    if _publisher is not None and _topic_path is not None:
        return _publisher, _topic_path
    try:
        from google.cloud import pubsub_v1  # type: ignore[import-not-found]

        settings = get_settings()
        publisher = pubsub_v1.PublisherClient()
        topic_path = publisher.topic_path(settings.gcp_project, settings.events_topic)
        _publisher = publisher
        _topic_path = topic_path
        logger.info("Pub/Sub publisher ready: %s", topic_path)
        return _publisher, _topic_path
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Pub/Sub publisher init failed (%s) — switching to stub mode.", exc,
        )
        _publisher_disabled = True
        return None


async def publish(event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Publish an envelope. Returns the envelope (for logging/tests)."""
    env = build_envelope(event_type, payload or {})
    app_state.add_activity(f"event:{event_type}", _short(payload))

    pub = _get_publisher()
    if pub is None:
        # Stub mode: log + broadcast on admin SSE channel.
        logger.info("[event-stub] %s", json.dumps(env, default=str))
        await app_state.broadcast("all", {"event": "infra_event", **env})
        return env

    publisher, topic_path = pub
    data = json.dumps(env, default=str).encode("utf-8")
    try:
        future = publisher.publish(
            topic_path,
            data,
            event_type=event_type,
            source="fsu100v2",
        )
        loop = asyncio.get_running_loop()
        message_id = await loop.run_in_executor(None, future.result, 10)
        logger.info(
            "Published event_type=%s topic=%s message_id=%s",
            event_type, topic_path, message_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Pub/Sub publish failed (%s) — logging stub instead.", exc)
        logger.info("[event-stub-on-failure] %s", json.dumps(env, default=str))
    await app_state.broadcast("all", {"event": "infra_event", **env})
    return env


def _short(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""
    s = json.dumps(payload, default=str, separators=(",", ":"))
    return s if len(s) <= 200 else s[:197] + "..."


def reset_publisher_for_test() -> None:
    global _publisher, _topic_path, _publisher_disabled
    _publisher = None
    _topic_path = None
    _publisher_disabled = False

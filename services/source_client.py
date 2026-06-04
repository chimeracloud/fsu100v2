"""
FSU1B SSE consumer.

Pure async streaming client. One outer supervisor task (in
`stream_session.py`) wraps repeated calls to `run_connection()`,
backing off on errors. Watchdog (in `watchdog.py`) monitors message
age and force-disconnects when stale — same pattern FSU1B uses
internally.

For each `market_change` event:
  1. Build a host-level MarketSnapshot.
  2. Fan to every active plugin's `on_market_event`.
  3. Dispatch every returned EvaluationResult (LBCF / FSU2A / SSE).

For each `infra_event`:
  Record on the activity feed. The upstream (FSU1B) handles its own
  recovery; we just surface its state.

Bootstrap (one-shot at session start):
  GET /stream/snapshot?sport=horse-racing → process every market.
  Then attach to the live SSE stream.

Auth: service-to-service IAM ID tokens (Cloud Run metadata server).
Refresh roughly every 50 minutes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx
import httpx_sse

from core.config import get_settings
from core.plugin_api import MarketSnapshot
from core.state import app_state

from .dispatcher import dispatch
from .plugin_loader import active_plugins

logger = logging.getLogger(__name__)


def _disabled_io() -> bool:
    return bool(os.environ.get("FSU100V2_DISABLE_GCP_IO"))


def _fetch_id_token(target_url: str) -> str | None:
    """Mint an ID token for the source. Returns None if unavailable
    (e.g. tests or local-dev without ADC)."""
    if _disabled_io():
        return None
    try:
        from google.oauth2 import id_token  # type: ignore[import-not-found]
        from google.auth.transport import requests as g_requests  # type: ignore[import-not-found]

        return id_token.fetch_id_token(g_requests.Request(), target_url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ID-token fetch failed for %s: %s", target_url, exc)
        return None


def _source_url() -> str:
    """Effective source URL — manifest-discovered, otherwise fallback."""
    s = get_settings()
    return s.fallback_urls.fsu1b


def _source_id() -> str:
    return get_settings().source_id


def _source_type() -> str:
    return get_settings().source_type


# ── one connection cycle ────────────────────────────────────────────────


async def run_connection() -> None:
    """One live SSE connection. Raises on any error so the supervisor
    can back off and reconnect with a fresh token."""
    s = get_settings()
    base = _source_url()
    if not base:
        raise RuntimeError("source URL unknown — manifest discovery returned empty")

    snapshot_url = base.rstrip("/") + s.source_snapshot_endpoint
    sse_url = base.rstrip("/") + s.source_sport_endpoint

    headers = {"Accept": "text/event-stream"}
    token = _fetch_id_token(base)
    if token:
        headers["Authorization"] = f"Bearer {token}"

    app_state.source.state = "connecting"
    app_state.source.url = sse_url
    app_state.add_activity("source_connecting", sse_url)

    # ── Bootstrap from snapshot ───────────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            snap_headers = dict(headers)
            snap_headers["Accept"] = "application/json"
            r = await client.get(snapshot_url, headers=snap_headers)
            if r.status_code == 200:
                payload = r.json()
                markets = payload.get("markets") or []
                logger.info(
                    "source bootstrap: %d markets from %s", len(markets), snapshot_url,
                )
                for m in markets:
                    # The snapshot endpoint returns summary rows; treat as
                    # a market_change-like payload so the plugin can run
                    # its initial evaluation against the at-boot state.
                    synthetic_event = {
                        "event": "market_change",
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "source": "fsu1b",
                        "source_type": "live",
                        "sport": "horse-racing",
                        "event_type_id": "7",
                        "change_type": "BOOTSTRAP",
                        "market": m,
                        "runners": m.get("runners") or [],
                    }
                    await _handle_market_change(synthetic_event)
            else:
                logger.warning(
                    "source snapshot returned %s — proceeding without bootstrap",
                    r.status_code,
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning("source bootstrap failed (%s) — proceeding without it", exc)

    # ── Attach to live SSE ────────────────────────────────────────────
    timeout = httpx.Timeout(
        connect=10.0, read=None, write=10.0, pool=10.0,
    )
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with httpx_sse.aconnect_sse(
            client, "GET", sse_url, headers=headers,
        ) as event_source:
            app_state.source.state = "connected"
            app_state.source.connection_count += 1
            app_state.add_activity("source_connected", sse_url)

            async for sse_event in event_source.aiter_sse():
                try:
                    payload = json.loads(sse_event.data) if sse_event.data else None
                except json.JSONDecodeError as exc:
                    logger.warning("malformed SSE data: %s", exc)
                    continue
                if not payload:
                    continue

                app_state.source.last_message_at = datetime.now(timezone.utc)
                etype = payload.get("event")

                if etype == "market_change":
                    await _handle_market_change(payload)
                elif etype == "infra_event":
                    _handle_infra_event(payload)
                else:
                    # Unknown event type — log + continue.
                    logger.debug("unknown SSE event type: %r", etype)


# ── event handlers ──────────────────────────────────────────────────────


async def _handle_market_change(payload: dict[str, Any]) -> None:
    """Fan one market_change to every active plugin; dispatch results."""
    market = payload.get("market") or {}
    runners = payload.get("runners") or []

    snapshot = MarketSnapshot(
        ts=datetime.fromisoformat(
            (payload.get("ts") or datetime.now(timezone.utc).isoformat()).replace(
                "Z", "+00:00"
            )
        ),
        source=str(payload.get("source") or "fsu1b"),
        source_type=str(payload.get("source_type") or "live"),
        sport=str(payload.get("sport") or "horse-racing"),
        event_type_id=str(payload.get("event_type_id") or "7"),
        market=market,
        runners=runners,
        change_type=payload.get("change_type"),
        raw=payload,
    )

    # Maintain a small recent-markets cache for /api/markets.
    market_id = market.get("market_id")
    if market_id:
        app_state.recent_market_summaries[market_id] = market

    plugins = active_plugins()
    if not plugins:
        return

    for plugin in plugins:
        try:
            result = plugin.on_market_event(snapshot)
        except Exception as exc:  # noqa: BLE001 — plugin errors must not derail
            logger.exception("plugin %s on_market_event raised", plugin.id)
            app_state.add_activity(
                "plugin_error", f"{plugin.id}: {exc.__class__.__name__}: {exc}",
            )
            continue
        await dispatch(
            result,
            market_summary=market,
            source_id=_source_id(),
            source_type=_source_type(),
            snapshot_ts=payload.get("ts"),
        )


def _handle_infra_event(payload: dict[str, Any]) -> None:
    envelope = payload.get("envelope") or {}
    et = envelope.get("event_type", "?")
    app_state.add_activity(f"source_infra:{et}", json.dumps(payload.get("payload") or {}))


# ── lifecycle helpers (used by stream_session) ──────────────────────────


def note_drop(error: Exception | str | None = None) -> None:
    """Called when the supervisor catches a disconnect. Updates state + activity."""
    app_state.source.state = "reconnecting"
    app_state.source.reconnect_count += 1
    err = repr(error) if error is not None else ""
    if err:
        app_state.source.last_error = err
    app_state.add_activity("source_dropped", err or "(no exception)")


def note_disconnected() -> None:
    """Called on graceful stop."""
    app_state.source.state = "disconnected"
    app_state.add_activity("source_disconnected", "graceful stop")

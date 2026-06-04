"""
Source-freshness watchdog.

SC discipline: 30s check, 60s stale → force reconnect.

Same outer-safety pattern as FSU1B. The SSE library + httpx already
detect dead TCP within a few seconds; the watchdog catches the rarer
case where the socket appears alive but no useful traffic arrives.

When the watchdog trips it calls `session.force_disconnect()` —
the supervisor's current connection task is cancelled, supervisor
backs off, reconnects with a fresh ID token.

The watchdog stays passive while the session is idle / connecting /
reconnecting — only acts when state == 'connected'.
"""
from __future__ import annotations

import asyncio
import logging

from core.config import get_settings
from core.state import app_state

logger = logging.getLogger(__name__)


async def run_watchdog(session) -> None:
    while True:
        try:
            settings = get_settings()
            await asyncio.sleep(settings.stream_check_interval_s)
        except asyncio.CancelledError:
            return

        if not session.is_running:
            continue
        if app_state.source.state != "connected":
            continue

        age = app_state.source_age_s()
        if age is None:
            continue

        stale = settings.stream_stale_threshold_s
        if age > stale:
            logger.warning(
                "Watchdog: source stale (age=%.1fs > threshold=%ds) — forcing reconnect",
                age, stale,
            )
            app_state.add_activity(
                "watchdog_stale",
                f"age={age:.1f}s threshold={stale}s — forcing reconnect",
            )
            session.force_disconnect(reason=f"stale age={age:.1f}s threshold={stale}s")

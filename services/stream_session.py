"""
Stream session orchestrator.

Owns the SSE supervisor task, watchdog task, NDJSON flush-loop task.
Exposes start() / stop() / status() / force_disconnect().

Same shape as FSU1B's stream_session.py — supervisor wraps each
`run_connection()` call in its own asyncio Task. force_disconnect()
cancels only the inner conn task; the supervisor stays alive, catches
CancelledError, backs off, restarts.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from core.config import get_settings
from core.state import app_state

from . import source_client
from .event_publisher import publish
from .event_recorder import run_flush_loops
from .watchdog import run_watchdog

logger = logging.getLogger(__name__)


async def _publish_safe(event_type: str, payload: dict | None = None) -> None:
    try:
        await publish(event_type, payload or {})
    except Exception as exc:  # noqa: BLE001
        logger.warning("event publish failed (%s): %s", event_type, exc)


class StreamSession:
    """Singleton orchestrator for the FSU1B SSE consumer + workers."""

    def __init__(self) -> None:
        self._tasks: list[asyncio.Task] = []
        self._current_conn: Optional[asyncio.Task] = None
        self._running = False
        self._was_connected = False
        self._watchdog_drop = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> dict:
        if self._running:
            return {"accepted": False, "detail": "already running"}

        self._running = True
        self._was_connected = False
        self._watchdog_drop = False
        app_state.source.state = "connecting"
        app_state.add_activity("session_start", "starting source consumer + workers")

        loop = asyncio.get_running_loop()
        self._tasks = [
            loop.create_task(self._supervisor(), name="source-supervisor"),
            loop.create_task(run_watchdog(self), name="source-watchdog"),
            loop.create_task(run_flush_loops(), name="ndjson-flush"),
        ]
        return {"accepted": True, "detail": "started"}

    async def stop(self) -> dict:
        if not self._running:
            return {"accepted": False, "detail": "not running"}

        self._running = False
        if self._current_conn is not None and not self._current_conn.done():
            self._current_conn.cancel()
        for t in self._tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._current_conn = None
        source_client.note_disconnected()
        app_state.add_activity("session_stop", "consumer stopped")
        return {"accepted": True, "detail": "stopped"}

    def force_disconnect(self, reason: str = "") -> None:
        if self._current_conn is not None and not self._current_conn.done():
            self._current_conn.cancel()
            app_state.add_activity("force_disconnect", reason or "watchdog")
            if "watchdog" in reason.lower() or "stale" in reason.lower():
                self._watchdog_drop = True

    def status(self) -> dict:
        settings = get_settings()
        return {
            "running": self._running,
            "source_state": app_state.source.state,
            "source_url": app_state.source.url,
            "last_message_at": (
                app_state.source.last_message_at.isoformat()
                if app_state.source.last_message_at else None
            ),
            "source_age_s": app_state.source_age_s(),
            "reconnect_count": app_state.source.reconnect_count,
            "evaluation_count": app_state.evaluation_count,
            "instruction_count": app_state.instruction_count,
            "watchdog_stale_threshold_s": settings.stream_stale_threshold_s,
            "watchdog_check_interval_s": settings.stream_check_interval_s,
        }

    async def _supervisor(self) -> None:
        """Reconnect loop. Catches exceptions, publishes lifecycle events,
        backs off with exponential delay (capped)."""
        backoff = 1
        while self._running:
            settings = get_settings()
            max_b = settings.reconnect_max_backoff_s

            try:
                loop = asyncio.get_running_loop()
                self._current_conn = loop.create_task(
                    source_client.run_connection(), name="source-conn",
                )
                # Announce connected once the conn transitions there.
                watcher = loop.create_task(
                    self._announce_connected(), name="source-conn-announce",
                )
                try:
                    await self._current_conn
                finally:
                    if not watcher.done():
                        watcher.cancel()
                if not self._running:
                    return
            except asyncio.CancelledError:
                if not self._running:
                    return
                source_client.note_drop("cancelled (force_disconnect)")
                if self._was_connected:
                    await _publish_safe(
                        "source_disconnected",
                        {"cause": "cancelled", "reconnect_count": app_state.source.reconnect_count},
                    )
            except Exception as exc:  # noqa: BLE001
                source_client.note_drop(exc)
                if self._was_connected:
                    await _publish_safe(
                        "source_disconnected",
                        {
                            "cause": exc.__class__.__name__,
                            "message": str(exc),
                            "reconnect_count": app_state.source.reconnect_count,
                        },
                    )
            finally:
                self._current_conn = None

            wait = min(backoff, max_b)
            logger.info("Source reconnect in %ds (backoff=%d)", wait, backoff)
            try:
                await asyncio.sleep(wait)
            except asyncio.CancelledError:
                return
            backoff = min(backoff * 2, max_b)

    async def _announce_connected(self) -> None:
        """Poll-wait for source_state='connected', then publish the right event."""
        try:
            while self._running:
                if app_state.source.state == "connected":
                    if not self._was_connected:
                        self._was_connected = True
                        await _publish_safe(
                            "engine_started",
                            {
                                "source_url": app_state.source.url,
                                "reconnect_count": app_state.source.reconnect_count,
                            },
                        )
                        return
                    # Reconnect.
                    if self._watchdog_drop:
                        await _publish_safe(
                            "source_recovered",
                            {
                                "trigger": "watchdog",
                                "reconnect_count": app_state.source.reconnect_count,
                            },
                        )
                        self._watchdog_drop = False
                    else:
                        await _publish_safe(
                            "source_recovered",
                            {"reconnect_count": app_state.source.reconnect_count},
                        )
                    return
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            return


stream_session = StreamSession()

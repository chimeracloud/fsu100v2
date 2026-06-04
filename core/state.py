"""
Singleton application state.

Holds the runtime status of the source connection, the loaded plugins,
the per-sport / per-plugin counters, and the SSE pub/sub for the
portal-facing `/stream/evaluations`.

Dependency-free (apart from stdlib) so any module can import it
without cycles.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Optional

UTC = timezone.utc

SourceStatus = Literal[
    "disconnected", "connecting", "connected", "reconnecting", "failed"
]


@dataclass
class SourceInfo:
    state: SourceStatus = "disconnected"
    url: Optional[str] = None
    last_message_at: Optional[datetime] = None
    last_error: Optional[str] = None
    connection_count: int = 0
    reconnect_count: int = 0


class AppState:
    """Singleton, accessed via the module-level ``app_state``."""

    def __init__(self) -> None:
        # Source (FSU1B).
        self.source = SourceInfo()

        # Plugins.
        self.plugin_state: dict[str, dict] = {}   # plugin_id → state dict

        # Counters for /admin/stats.
        self.evaluation_count: int = 0
        self.instruction_count: int = 0
        self.skip_count: int = 0
        self.evaluations_by_plugin: dict[str, int] = {}
        self.instructions_by_plugin: dict[str, int] = {}

        # Per-endpoint call tracking (drives portal LEDs for downstream consumers).
        self.last_call_at_by_endpoint: dict[str, datetime] = {}
        self.call_count_by_endpoint: dict[str, int] = {}

        # Warnings — operator sees these in /admin/status.warnings.
        # E.g. "live_betting_control_unreachable: instructions queued to GCS"
        self.warnings: set[str] = set()

        # Activity ring buffer for /admin/activity.
        self._activity: deque[dict] = deque(maxlen=200)

        # Recent markets seen on the SSE — keyed by market_id, ordered by
        # last-seen, capped so memory stays bounded. Surfaced via /api/markets.
        self.recent_market_summaries: OrderedDict[str, dict] = OrderedDict()

        # Recent instructions — surfaced via /api/instructions.
        self.recent_instructions: deque[dict] = deque(maxlen=500)

        # SSE pub/sub. Channel "evaluations" carries every plugin verdict;
        # channel "all" mirrors the same. Subscribers held as asyncio.Queue.
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        self._sub_lock = asyncio.Lock()

        self.started_at: datetime = datetime.now(UTC)

    # ── Activity feed ─────────────────────────────────────────────────────

    def add_activity(self, kind: str, detail: str) -> None:
        self._activity.append(
            {
                "ts": datetime.now(UTC).isoformat(),
                "kind": kind,
                "detail": detail,
            }
        )

    def recent_activity(self, limit: int = 100) -> list[dict]:
        return list(self._activity)[-limit:]

    # ── Warnings ─────────────────────────────────────────────────────────

    def add_warning(self, warning: str) -> None:
        # Only log an activity row when the warning transitions from
        # absent → present. Re-raising the same warning on every
        # dispatch would drown the feed.
        if warning not in self.warnings:
            self.warnings.add(warning)
            self.add_activity("warning_raised", warning)

    def clear_warning(self, warning: str) -> None:
        if warning in self.warnings:
            self.warnings.discard(warning)
            self.add_activity("warning_cleared", warning)

    # ── Endpoint call tracking ───────────────────────────────────────────

    def note_endpoint_call(self, path: str) -> None:
        self.last_call_at_by_endpoint[path] = datetime.now(UTC)
        self.call_count_by_endpoint[path] = (
            self.call_count_by_endpoint.get(path, 0) + 1
        )

    # ── Source freshness ─────────────────────────────────────────────────

    def source_is_fresh(self, stale_threshold_s: int) -> bool:
        t = self.source.last_message_at
        if t is None:
            return False
        age = (datetime.now(UTC) - t).total_seconds()
        return age <= stale_threshold_s

    def source_age_s(self) -> Optional[float]:
        t = self.source.last_message_at
        if t is None:
            return None
        return (datetime.now(UTC) - t).total_seconds()

    # ── SSE pub/sub ──────────────────────────────────────────────────────

    async def subscribe(self, channel: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=512)
        async with self._sub_lock:
            self._subscribers.setdefault(channel, []).append(q)
        return q

    async def unsubscribe(self, channel: str, q: asyncio.Queue) -> None:
        async with self._sub_lock:
            subs = self._subscribers.get(channel, [])
            self._subscribers[channel] = [s for s in subs if s is not q]

    async def broadcast(self, channel: str, event: dict) -> None:
        """Publish to one channel; mirrors to 'all'."""
        async with self._sub_lock:
            for ch in (channel, "all"):
                for q in self._subscribers.get(ch, []):
                    try:
                        q.put_nowait(event)
                    except asyncio.QueueFull:
                        pass

    def subscriber_count(self) -> dict[str, int]:
        return {ch: len(subs) for ch, subs in self._subscribers.items()}


# Module-level singleton.
app_state = AppState()


def reset_state_for_test() -> None:
    """Test-only — reset the singleton's fields in place.

    Critical: must mutate the *existing* object rather than rebind the
    module-level name. Other modules import `app_state` by reference.
    """
    fresh = AppState()
    app_state.__dict__.update(fresh.__dict__)

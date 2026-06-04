"""
Set 3 — CONTENT: stream + session reads.

  GET  /stream/evaluations     SSE — every evaluation (decision or skip), per-plugin
  GET  /api/session            current session stats
  GET  /api/markets            recently-seen markets (cache)
  GET  /api/instructions       last N instructions issued
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from core.state import app_state

logger = logging.getLogger(__name__)

router = APIRouter(tags=["stream"])


@router.get("/stream/evaluations", response_class=StreamingResponse)
async def stream_evaluations(request: Request) -> StreamingResponse:
    """Per-consumer SSE feed of evaluations. Heartbeats every 15s."""
    queue = await app_state.subscribe("evaluations")

    async def _gen():
        last_heartbeat = time.monotonic()
        try:
            yield ": fsu100v2 sse — channel=evaluations\n\n"
            while True:
                if await request.is_disconnected():
                    return
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    if time.monotonic() - last_heartbeat >= 15:
                        yield ": heartbeat\n\n"
                        last_heartbeat = time.monotonic()
                    continue
                yield f"event: {msg.get('event', 'message')}\n"
                yield f"data: {json.dumps(msg, default=str)}\n\n"
                last_heartbeat = time.monotonic()
        finally:
            await app_state.unsubscribe("evaluations", queue)

    return StreamingResponse(_gen(), media_type="text/event-stream")


@router.get("/api/session")
def session() -> dict[str, Any]:
    return {
        "started_at": app_state.started_at.isoformat(),
        "source_state": app_state.source.state,
        "source_age_s": app_state.source_age_s(),
        "reconnect_count": app_state.source.reconnect_count,
        "evaluation_count": app_state.evaluation_count,
        "instruction_count": app_state.instruction_count,
        "skip_count": app_state.skip_count,
        "evaluations_by_plugin": dict(app_state.evaluations_by_plugin),
        "instructions_by_plugin": dict(app_state.instructions_by_plugin),
        "warnings": sorted(app_state.warnings),
    }


@router.get("/api/markets")
def markets(limit: int = Query(default=200, ge=1, le=1000)) -> dict[str, Any]:
    items = list(app_state.recent_market_summaries.values())[-limit:]
    return {"count": len(items), "markets": items}


@router.get("/api/instructions")
def instructions(limit: int = Query(default=50, ge=1, le=500)) -> dict[str, Any]:
    items = list(app_state.recent_instructions)[-limit:]
    return {"count": len(items), "instructions": items}

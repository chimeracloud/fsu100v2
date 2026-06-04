"""
Set 1 — PARAMETERS (admin endpoints).

Identical across every Chimera FSU per CHI-ADR-010. Phase 1 returns
placeholder shapes that match the eventual contract; Phase 2+ wires
plugin state, Phase 3+ wires source state, etc.

Control verbs supported in Phase 1:
  start              — accepted; real source/plugin wiring lands in Phase 3
  stop               — accepted; symmetric with start
  reconnect_source   — accepted; wires in Phase 3
  rediscover         — re-read the Source Manifest (refresh fallback URLs)
  reregister_source  — re-write FSU100V2's manifest entry
  reload_plugins     — accepted; wires when first plugin lands (Phase 2)
  test               — sanity poke; verifies admin surface
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Request

from core.config import get_settings
from core.gcs_config import save_config_to_gcs
from core.state import app_state
from core.version import PHASE, SERVICE_NAME, VERSION
from models.admin import (
    ActivityEvent,
    AdminActivityResponse,
    AdminConfigResponse,
    AdminConfigUpdate,
    AdminStatsResponse,
    AdminStatusResponse,
    ControlActionResponse,
    PluginSummary,
    SourceState,
)
from services.plugin_loader import get_registry_summary

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/status", response_model=AdminStatusResponse)
def status(request: Request) -> AdminStatusResponse:
    """Composite operator status."""
    return AdminStatusResponse(
        service=SERVICE_NAME,
        version=VERSION,
        phase=PHASE,
        source=SourceState(
            state=app_state.source.state,
            url=app_state.source.url,
            last_message_at=app_state.source.last_message_at,
            last_error=app_state.source.last_error,
            connection_count=app_state.source.connection_count,
            reconnect_count=app_state.source.reconnect_count,
        ),
        plugins=[PluginSummary(**p) for p in get_registry_summary()],
        warnings=sorted(app_state.warnings),
        now=datetime.now(timezone.utc),
    )


@router.get("/config", response_model=AdminConfigResponse)
def get_config() -> AdminConfigResponse:
    s = get_settings()
    return AdminConfigResponse(
        source_id=s.source_id,
        source_type=s.source_type,
        source_sport_endpoint=s.source_sport_endpoint,
        source_snapshot_endpoint=s.source_snapshot_endpoint,
        stream_check_interval_s=s.stream_check_interval_s,
        stream_stale_threshold_s=s.stream_stale_threshold_s,
        reconnect_max_backoff_s=s.reconnect_max_backoff_s,
        loaded_plugins=list(s.loaded_plugins),
        auto_start=s.auto_start,
        dry_run=s.dry_run,
        log_level=s.log_level,
        market_hours_start_utc=s.market_hours_start_utc,
        market_hours_end_utc=s.market_hours_end_utc,
        fallback_urls={
            "fsu1b": s.fallback_urls.fsu1b,
            "live_betting_control": s.fallback_urls.live_betting_control,
            "fsu2a": s.fallback_urls.fsu2a,
        },
    )


@router.put("/config", response_model=AdminConfigResponse)
def put_config(update: AdminConfigUpdate) -> AdminConfigResponse:
    """Update host settings and persist to GCS."""
    changes = {
        k: v for k, v in update.model_dump(exclude_unset=True).items() if v is not None
    }
    if not changes:
        return get_config()

    persisted = save_config_to_gcs(changes)
    if not persisted:
        raise HTTPException(
            status_code=502,
            detail={
                "ok": False,
                "applied_in_memory": True,
                "persisted_to_gcs": False,
                "note": "settings changed but did NOT persist to GCS — retry PUT",
            },
        )
    return get_config()


@router.get("/stats", response_model=AdminStatsResponse)
def stats() -> AdminStatsResponse:
    return AdminStatsResponse(
        evaluation_count=app_state.evaluation_count,
        instruction_count=app_state.instruction_count,
        skip_count=app_state.skip_count,
        evaluations_by_plugin=dict(app_state.evaluations_by_plugin),
        instructions_by_plugin=dict(app_state.instructions_by_plugin),
        source_reconnects=app_state.source.reconnect_count,
        source_age_s=app_state.source_age_s(),
        last_call_at_by_endpoint=dict(app_state.last_call_at_by_endpoint),
        call_count_by_endpoint=dict(app_state.call_count_by_endpoint),
    )


@router.get("/activity", response_model=AdminActivityResponse)
def activity() -> AdminActivityResponse:
    events = [ActivityEvent(**e) for e in app_state.recent_activity(limit=100)]
    return AdminActivityResponse(events=events)


@router.post("/control/{action}", response_model=ControlActionResponse)
async def control(
    action: Literal[
        "start",
        "stop",
        "reconnect_source",
        "rediscover",
        "reregister_source",
        "reload_plugins",
        "test",
    ],
) -> ControlActionResponse:
    """Phase 1: most verbs accept but defer to Phase 2/3 for real wiring."""
    now = datetime.now(timezone.utc)

    if action == "rediscover":
        # Re-read the Source Manifest. Phase 1 — best effort.
        from services.source_manifest import refresh_discovery

        try:
            discovered = refresh_discovery()
            app_state.add_activity(
                "rediscovered",
                f"discovered={sorted(discovered.keys())}",
            )
            return ControlActionResponse(
                action=action,
                accepted=True,
                executed=True,
                note=f"refreshed manifest — discovered {sorted(discovered.keys())}",
                at=now,
            )
        except Exception as exc:  # noqa: BLE001
            return ControlActionResponse(
                action=action,
                accepted=True,
                executed=False,
                note=f"rediscover failed: {exc}",
                at=now,
            )

    if action == "reregister_source":
        from services.source_manifest import register

        try:
            entry = register()
            app_state.add_activity("source_reregistered", f"url={entry.get('url')!r}")
            return ControlActionResponse(
                action=action,
                accepted=True,
                executed=True,
                note=f"manifest entry written (url={entry.get('url')!r})",
                at=now,
            )
        except Exception as exc:  # noqa: BLE001
            return ControlActionResponse(
                action=action,
                accepted=True,
                executed=False,
                note=f"manifest registration failed: {exc}",
                at=now,
            )

    if action == "test":
        return ControlActionResponse(
            action=action,
            accepted=True,
            executed=True,
            note="ok",
            at=now,
        )

    if action == "start":
        from services.stream_session import stream_session
        result = await stream_session.start()
        return ControlActionResponse(
            action=action,
            accepted=bool(result.get("accepted")),
            executed=bool(result.get("accepted")),
            note=result.get("detail", ""),
            at=now,
        )

    if action == "stop":
        from services.stream_session import stream_session
        result = await stream_session.stop()
        return ControlActionResponse(
            action=action,
            accepted=True,
            executed=bool(result.get("accepted")),
            note=result.get("detail", ""),
            at=now,
        )

    if action == "reconnect_source":
        from services.stream_session import stream_session
        if not stream_session.is_running:
            raise HTTPException(status_code=409, detail="source not running")
        stream_session.force_disconnect(reason="manual reconnect_source")
        return ControlActionResponse(
            action=action,
            accepted=True,
            executed=True,
            note="forced disconnect; supervisor will reconnect",
            at=now,
        )

    # reload_plugins — Phase 4 wiring (live config edit + hot-reload).
    return ControlActionResponse(
        action=action,
        accepted=True,
        executed=False,
        note=f"{action} — accepted; wires up in a later phase",
        at=now,
    )


@router.get("/events")
async def events_sse():
    """Admin SSE feed — Phase 4 streams real lifecycle events; Phase 1 heartbeat."""
    from fastapi.responses import StreamingResponse

    async def _gen():
        yield ": fsu100v2 admin event stream — phase 1\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream")

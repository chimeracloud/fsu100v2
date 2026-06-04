"""
Standard observability endpoints.

Identical shape across every Chimera FSU per CHI-POL-008 §5.2.

Phase 1: `/ready` returns 200 with mode='idle' when the source is not
yet started. Phase 3 will gate it on source freshness + at-least-one
plugin loaded.
"""
from datetime import datetime, timezone
from time import time
from typing import Any

from fastapi import APIRouter, Response

from core.config import get_settings
from core.state import app_state
from core.version import PHASE, SERVICE_NAME, VERSION

router = APIRouter(tags=["observability"])

_START_TS = time()


@router.get("/health")
def health() -> dict[str, Any]:
    """Liveness — process is up."""
    return {"status": "ok"}


@router.get("/ready")
def ready(response: Response) -> dict[str, Any]:
    """Readiness.

    Phase 1: shell-only. The source isn't wired yet, so we return 200
    with mode='idle'. Phase 3 will require:
      - source SSE connected within `stream_stale_threshold_s`
      - at least one plugin loaded successfully
    """
    settings = get_settings()
    # Phase 1 — no source yet. Always ready for admin traffic.
    if app_state.source.state == "disconnected":
        return {
            "ready": True,
            "phase": PHASE,
            "mode": "idle",
            "source_state": "disconnected",
            "note": "source not started — POST /admin/control/start to connect",
        }

    # Once Phase 3 wires the source, the freshness gate kicks in here.
    fresh = app_state.source_is_fresh(settings.stream_stale_threshold_s)
    if app_state.source.state == "connected" and fresh:
        return {
            "ready": True,
            "phase": PHASE,
            "mode": "running",
            "source_state": "connected",
            "source_age_s": app_state.source_age_s(),
        }

    response.status_code = 503
    return {
        "ready": False,
        "phase": PHASE,
        "mode": "running",
        "source_state": app_state.source.state,
        "source_age_s": app_state.source_age_s(),
        "stale_threshold_s": settings.stream_stale_threshold_s,
    }


@router.get("/info")
def info() -> dict[str, Any]:
    return {
        "service": SERVICE_NAME,
        "version": VERSION,
        "phase": PHASE,
        "description": "Horse Racing Lay Engine (pure decision engine)",
    }


@router.get("/metrics")
def metrics() -> Response:
    """Prometheus-format metrics."""
    body_lines = [
        "# HELP fsu100v2_uptime_seconds Seconds since the service started.",
        "# TYPE fsu100v2_uptime_seconds counter",
        f"fsu100v2_uptime_seconds {time() - _START_TS:.3f}",
        "# HELP fsu100v2_evaluation_total Total evaluations performed.",
        "# TYPE fsu100v2_evaluation_total counter",
        f"fsu100v2_evaluation_total {app_state.evaluation_count}",
        "# HELP fsu100v2_instruction_total Total instructions issued.",
        "# TYPE fsu100v2_instruction_total counter",
        f"fsu100v2_instruction_total {app_state.instruction_count}",
        "# HELP fsu100v2_skip_total Total evaluations that resulted in a skip.",
        "# TYPE fsu100v2_skip_total counter",
        f"fsu100v2_skip_total {app_state.skip_count}",
        "# HELP fsu100v2_source_reconnects_total Total source-stream reconnects.",
        "# TYPE fsu100v2_source_reconnects_total counter",
        f"fsu100v2_source_reconnects_total {app_state.source.reconnect_count}",
    ]
    age = app_state.source_age_s()
    if age is not None:
        body_lines += [
            "# HELP fsu100v2_source_age_seconds Seconds since the last source message.",
            "# TYPE fsu100v2_source_age_seconds gauge",
            f"fsu100v2_source_age_seconds {age:.3f}",
        ]
    body = "\n".join(body_lines) + "\n"
    return Response(content=body, media_type="text/plain; version=0.0.4")


@router.get("/status")
def status() -> dict[str, Any]:
    return {
        "service": SERVICE_NAME,
        "version": VERSION,
        "phase": PHASE,
        "uptime_s": round(time() - _START_TS, 3),
        "now": datetime.now(timezone.utc).isoformat(),
        "source_state": app_state.source.state,
        "source_age_s": app_state.source_age_s(),
        "evaluation_count": app_state.evaluation_count,
        "instruction_count": app_state.instruction_count,
    }

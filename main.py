"""
FSU100V2 — Horse Racing Lay Engine (pure decision engine).

Phase 1 — shell only. No source consumer. No plugins. No dispatcher.
Standard FSU admin + observability surface, GCS-backed config,
Source-Manifest read + register, Pub/Sub event envelopes.

NO Betfair imports. NO Betfair credentials. NO Secret Manager reads.

This engine consumes market data from FSU1B (Phase 3) and emits
instructions to LBCF / FSU2A (Phase 3, fallback to GCS NDJSON).

References:
  - CHI-POL-005  FSU Build Workflow
  - CHI-POL-006  Portal as Single Auth Boundary (GCS config, no env vars)
  - CHI-POL-008  Shell-First Build Policy
  - CHI-ADR-010  Three Endpoint Sets
  - CHI-ADR-013  One task, one job
  - CHI-ADR-014  Portal Proxy Pattern
  - Bible §20    Event Envelope + Courier
  - Bible §21    Source Manifest
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from core.config import get_settings, replace_settings
from core.gcs_config import load_config_from_gcs
from core.logging import configure_logging
from core.state import app_state
from core.version import SERVICE_DESCRIPTION, SERVICE_NAME, VERSION
from services import admin, observability, plugin_routes, stream_routes
from services.event_publisher import publish
from services.plugin_loader import load_all_plugins
from services.source_manifest import refresh_discovery, register_best_effort
from services.stream_session import stream_session

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings_initial = get_settings()
    configure_logging(settings_initial.log_level)

    # Phase 1: hydrate config from GCS. Falls back to in-memory defaults
    # if the bucket is unreachable so the admin surface still serves.
    load_config_from_gcs()

    # If the deploy injected $SERVICE_URL, propagate into Settings so the
    # Source Manifest entry advertises the right URL. (Deploy-time
    # identity only — not a tunable setting.)
    if os.environ.get("SERVICE_URL"):
        replace_settings(service_url=os.environ["SERVICE_URL"])

    settings = get_settings()
    app.state.settings = settings

    # Source Manifest: read what's there (discover sibling FSUs) +
    # register ourselves (best effort).
    refresh_discovery()
    register_best_effort()

    # Plugins (Phase 2). Load every plugin in Settings.loaded_plugins;
    # bad plugins log + emit `plugin_failed` but don't block boot.
    load_all_plugins()

    # Infrastructure event: we're up.
    await publish("engine_started", {
        "version": VERSION,
        "phase": 2,
        "auto_start": settings.auto_start,
        "service_url": settings.service_url,
        "loaded_plugins": list(settings.loaded_plugins),
    })

    if settings.auto_start:
        logger.info("auto_start=True — starting source consumer in background")
        asyncio.create_task(stream_session.start(), name="auto-start")
    else:
        logger.info(
            "auto_start=False — engine idle. POST /admin/control/start to begin."
        )

    try:
        yield
    finally:
        try:
            await stream_session.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            await publish("engine_stopped", {"version": VERSION})
        except Exception:  # noqa: BLE001
            pass
        logger.info("FSU100V2 shut down.")


app = FastAPI(
    title=SERVICE_NAME,
    description=SERVICE_DESCRIPTION,
    version=VERSION,
    docs_url="/admin/docs",
    redoc_url=None,
    lifespan=lifespan,
)


@app.middleware("http")
async def _record_endpoint_call(request: Request, call_next):
    """Stamp every inbound non-observability request for DATA OUT LEDs."""
    path = request.url.path
    if path not in {"/health", "/ready", "/metrics", "/info", "/status"}:
        try:
            app_state.note_endpoint_call(path)
        except Exception:  # noqa: BLE001
            pass
    return await call_next(request)


app.include_router(observability.router)
app.include_router(admin.router)
app.include_router(plugin_routes.router)
app.include_router(stream_routes.router)

"""
Configuration.

Host-level settings. Per CHI-POL-006: no env vars for tunable settings.
Only SERVICE_URL (deploy-time identity) and FSU100V2_DISABLE_GCP_IO
(test kill-switch) are read from env.

Plugin business config lives separately, owned by each plugin and
persisted under `gs://chiops-fsu100v2-config/plugins/{plugin_id}.json`.
The host knows nothing about plugin internals.
"""
from dataclasses import asdict, dataclass, field, fields, replace
from threading import RLock
from typing import Any


@dataclass(frozen=True)
class FallbackUrls:
    """Used if the Source Manifest is unreachable on startup."""

    fsu1b: str = ""                  # market data source (SSE + REST)
    live_betting_control: str = ""   # instruction executor
    fsu2a: str = ""                  # events recorder


@dataclass(frozen=True)
class Settings:
    # Identity.
    service_name: str = "fsu100v2"
    region: str = "europe-west2"
    gcp_project: str = "chiops"

    # Source — where market data comes from. URL discovered via manifest
    # at boot; fallback values stay as a safety net.
    source_id: str = "fsu1b"
    source_type: str = "live"               # live | historic | paper
    source_sport_endpoint: str = "/stream/horse-racing"
    source_snapshot_endpoint: str = "/stream/snapshot?sport=horse-racing"

    # Stream health.
    stream_check_interval_s: int = 30
    stream_stale_threshold_s: int = 60
    reconnect_max_backoff_s: int = 300

    # Plugins to load on boot.
    loaded_plugins: tuple[str, ...] = ("mark_6_rules_v1",)

    # Lifecycle.
    auto_start: bool = False  # Container boots idle; operator starts via /admin/control/start.

    # Operational flags.
    dry_run: bool = False  # When True, dispatcher logs payload, does not POST to LBCF.
    log_level: str = "INFO"

    # Market hours (UTC). Decision engine doesn't gate on these in Phase 1,
    # but they're surfaced for the operator and plugins to consult.
    market_hours_start_utc: str = "08:00"
    market_hours_end_utc: str = "23:00"

    # Public URL for Source Manifest registration. Set at deploy time
    # via $SERVICE_URL env. Empty until first deploy.
    service_url: str = ""

    # GCS buckets.
    config_bucket: str = "chiops-fsu100v2-config"
    config_blob: str = "fsu100v2.json"
    plugin_config_prefix: str = "plugins/"      # blob prefix under config_bucket
    events_bucket: str = "chiops-fsu100v2-events"
    manifest_bucket: str = "chimera-portal-config"
    manifest_blob: str = "source_manifest.json"

    # Pub/Sub topic for engine infra events.
    events_topic: str = "chimera-fsu100v2-events"

    # Fallback URLs if manifest discovery fails.
    fallback_urls: FallbackUrls = field(default_factory=FallbackUrls)


_lock = RLock()
_current: Settings = Settings()


def get_settings() -> Settings:
    with _lock:
        return _current


def replace_settings(**changes) -> Settings:
    global _current
    with _lock:
        if "fallback_urls" in changes and isinstance(changes["fallback_urls"], dict):
            changes["fallback_urls"] = replace(_current.fallback_urls, **changes["fallback_urls"])
        _current = replace(_current, **changes)
        return _current


def reset_settings_for_test() -> None:
    global _current
    with _lock:
        _current = Settings()


def settings_to_dict() -> dict[str, Any]:
    with _lock:
        d = asdict(_current)
    for k, v in list(d.items()):
        if isinstance(v, tuple):
            d[k] = list(v)
    return d


def apply_dict(payload: dict[str, Any]) -> Settings:
    """Hydrate settings from a (possibly partial) dict. Unknown keys ignored."""
    global _current
    with _lock:
        allowed = {f.name for f in fields(Settings)}
        fallback_allowed = {f.name for f in fields(FallbackUrls)}

        changes: dict[str, Any] = {}
        for k, v in payload.items():
            if k not in allowed:
                continue
            if k == "fallback_urls" and isinstance(v, dict):
                sub = {kk: vv for kk, vv in v.items() if kk in fallback_allowed}
                changes["fallback_urls"] = replace(_current.fallback_urls, **sub)
                continue
            current_value = getattr(_current, k)
            if isinstance(current_value, tuple) and isinstance(v, list):
                changes[k] = tuple(v)
            else:
                changes[k] = v

        _current = replace(_current, **changes)
        return _current

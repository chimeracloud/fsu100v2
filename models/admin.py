"""
Pydantic v2 response models for Set 1 admin endpoints.

Phase 1: shapes reflect a pre-source, no-plugin state. Phase 2+ adds
plugin-state sub-objects; Phase 3 adds real source state + counters.
"""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

SourceStateLiteral = Literal[
    "disconnected", "connecting", "connected", "reconnecting", "failed"
]


class SourceState(BaseModel):
    state: SourceStateLiteral
    url: str | None = None
    last_message_at: datetime | None = None
    last_error: str | None = None
    connection_count: int = 0
    reconnect_count: int = 0


class PluginSummary(BaseModel):
    id: str
    version: str
    status: str        # "loaded" | "failed" | "configuring"
    last_error: str | None = None


class AdminStatusResponse(BaseModel):
    service: str
    version: str
    phase: int
    source: SourceState
    plugins: list[PluginSummary] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    now: datetime


class AdminConfigResponse(BaseModel):
    source_id: str
    source_type: str
    source_sport_endpoint: str
    source_snapshot_endpoint: str
    stream_check_interval_s: int
    stream_stale_threshold_s: int
    reconnect_max_backoff_s: int
    loaded_plugins: list[str]
    auto_start: bool
    dry_run: bool
    log_level: str
    market_hours_start_utc: str
    market_hours_end_utc: str
    fallback_urls: dict[str, str]


class AdminConfigUpdate(BaseModel):
    source_id: str | None = None
    source_type: str | None = None
    source_sport_endpoint: str | None = None
    source_snapshot_endpoint: str | None = None
    stream_check_interval_s: int | None = None
    stream_stale_threshold_s: int | None = None
    reconnect_max_backoff_s: int | None = None
    loaded_plugins: list[str] | None = None
    auto_start: bool | None = None
    dry_run: bool | None = None
    log_level: str | None = None
    market_hours_start_utc: str | None = None
    market_hours_end_utc: str | None = None
    fallback_urls: dict[str, str] | None = None


class AdminStatsResponse(BaseModel):
    evaluation_count: int
    instruction_count: int
    skip_count: int
    evaluations_by_plugin: dict[str, int] = Field(default_factory=dict)
    instructions_by_plugin: dict[str, int] = Field(default_factory=dict)
    source_reconnects: int
    source_age_s: float | None = None
    last_call_at_by_endpoint: dict[str, datetime | None] = Field(default_factory=dict)
    call_count_by_endpoint: dict[str, int] = Field(default_factory=dict)


class ActivityEvent(BaseModel):
    ts: datetime
    kind: str
    detail: str


class AdminActivityResponse(BaseModel):
    events: list[ActivityEvent] = Field(default_factory=list)


class ControlActionResponse(BaseModel):
    action: str
    accepted: bool
    executed: bool
    note: str
    at: datetime

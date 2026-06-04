"""
Strategy Plugin contract — the heart of the engine's extensibility.

Every strategy ships as a plugin under `plugins/<plugin_id>/`. The host
(FSU100V2) is sport-agnostic and strategy-agnostic; the plugin owns the
business logic.

Three things the plugin always exposes:

  1. Lifecycle hooks  — load, configure, on_market_event, on_market_closed, unload.
  2. Declarative schemas — parameter_schema (JSON Schema), result_schema.
  3. Operational reads — get_state, get_results, get_parameters.

The plugin emits data. The portal owns rendering. Plugin authors never
write UI; portal authors never dictate plugin internals.

Phase 1 ships ONLY this contract. Phase 2 ships the first plugin
(plugins/mark_6_rules_v1) implementing it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class MarketSnapshot:
    """The shape every plugin receives. Maps 1:1 to FSU1B's SSE payload.

    Snapshot-per-event semantics: this is the FULL state of the market
    at `ts`. No delta reconstruction needed in the plugin.
    """
    ts: datetime
    source: str                       # "fsu1b" | "fsu1a" | "paper" | ...
    source_type: str                  # "live" | "historic" | "paper"
    sport: str                        # "horse-racing"
    event_type_id: str                # "7"
    market: dict[str, Any]            # market summary (id, status, name, venue, ...)
    runners: list[dict[str, Any]]     # full runner ladders
    change_type: str | None = None    # SUB_IMAGE | RESUB_DELTA | HEARTBEAT | None
    raw: dict[str, Any] = field(default_factory=dict)  # untouched SSE payload


@dataclass(frozen=True)
class Instruction:
    """One bet instruction. Multiple may be returned per evaluation."""
    action: str                  # "PLACE" | "CANCEL" | "REPLACE"
    side: str                    # "LAY" | "BACK"
    selection_id: int
    selection_name: str
    price: float
    size: float
    persistence_type: str = "LAPSE"   # LAPSE | PERSIST | MARKET_ON_CLOSE
    customer_order_ref: str | None = None
    customer_strategy_ref: str | None = None
    decision: dict[str, Any] = field(default_factory=dict)  # why


@dataclass(frozen=True)
class EvaluationResult:
    """One plugin's verdict on one market snapshot."""
    market_id: str
    plugin_id: str
    plugin_version: str
    ts: datetime
    instructions: list[Instruction] = field(default_factory=list)
    skip_reason: str | None = None        # populated iff instructions == []
    evidence: dict[str, Any] = field(default_factory=dict)


class StrategyPlugin(Protocol):
    """The contract every plugin implements.

    The host calls these methods. The plugin's internal layout is its
    own concern — it can keep state, hold sub-components, anything —
    provided the externally-visible behaviour matches the Protocol.
    """

    # ── Identity ─────────────────────────────────────────────────────

    @property
    def id(self) -> str: ...                # e.g. "mark_6_rules_v1"

    @property
    def version(self) -> str: ...           # e.g. "1.0.0"

    @property
    def description(self) -> str: ...

    # ── Declarative schemas (data, not UI) ───────────────────────────

    @property
    def parameter_schema(self) -> dict:
        """JSON Schema (draft-2020-12) describing the editable parameters.

        The CST portal renders the form from this. Plugin authors do
        NOT decide whether a field becomes a slider, checkbox, or
        select — the portal applies house styling and accessibility.
        """
        ...

    @property
    def result_schema(self) -> dict:
        """JSON Schema describing the shape of `EvaluationResult.evidence`
        and `Instruction.decision` produced by this plugin."""
        ...

    # ── Lifecycle ─────────────────────────────────────────────────────

    def load(self, *, host_context: dict) -> None:
        """Called once at FSU100V2 boot after the plugin is instantiated.

        host_context exposes references the plugin may need: a GCS client
        (for read-only resources), logger, current sport, etc.
        Plugin may raise; the host catches and emits `plugin_failed`.
        """
        ...

    def configure(self, parameters: dict) -> None:
        """Apply / hot-update the plugin's business parameters.

        Called once at boot with persisted-or-default values, then on
        every successful PUT /api/plugins/{id}/config. The host validates
        the body against parameter_schema BEFORE calling — plugins
        can assume input is schema-valid.
        """
        ...

    def get_parameters(self) -> dict:
        """Return the current parameter values for GET /api/plugins/{id}/config."""
        ...

    # ── Per-event hooks ───────────────────────────────────────────────

    def on_market_event(self, snapshot: MarketSnapshot) -> EvaluationResult:
        """The heart. Pure function over (snapshot, plugin state).

        Must NOT do I/O. Must NOT call external services. Must NOT mutate
        anything other than the plugin's own private state. The host
        dispatches the result.
        """
        ...

    def on_market_closed(self, market_id: str, final_snapshot: MarketSnapshot) -> None:
        """Optional cleanup hook. Called when a market goes CLOSED."""
        ...

    # ── Operational reads ─────────────────────────────────────────────

    def get_state(self) -> dict:
        """Runtime state snapshot for /api/plugins/{id}/state."""
        ...

    def get_results(self) -> dict:
        """Session results for /api/plugins/{id}/results."""
        ...

    def unload(self) -> None:
        """Optional. Called on graceful shutdown or plugin reload."""
        ...

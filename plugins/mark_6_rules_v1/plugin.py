"""
`mark_6_rules_v1` — first plugin for FSU100V2.

Wraps the ported CLEv2 evaluator (in `.evaluator`) behind the
host-facing `StrategyPlugin` Protocol. The evaluator code is
**unchanged from CLEv2 production** — only its imports were
relativised to fit the plugin package.

On every `on_market_event`:

  1. Adapt the SSE payload → ClevSnapshot (via `.adapter`).
  2. Build a runtime `clev2_settings.Settings` from the plugin's
     current parameters dict.
  3. Call `evaluator.evaluate(snapshot, settings)` — the proven
     13-step pipeline.
  4. Translate the CLEv2 result back into the host's
     `EvaluationResult` shape.

No mutation of evaluator logic. No new business rules. This plugin
is a one-way membrane.
"""
from __future__ import annotations

import copy
import logging
from datetime import datetime, timezone
from typing import Any

from core.plugin_api import EvaluationResult, Instruction, MarketSnapshot

from . import evaluator
from . import parameter_schema as ps_module
from . import result_schema as rs_module
from .adapter import snapshot_to_plugin_snapshot
from .clev2_settings import (
    BaseRules,
    Controls,
    GeneralSettings,
    Mode,
    RiskOverlay,
    Settings,
    SignalToggles,
)

logger = logging.getLogger(__name__)


def _defaults_from_schema(schema: dict) -> dict:
    """Walk the parameter_schema and return a dict of defaults.

    Used at first boot when no GCS blob has been written yet.
    """
    out: dict = {}
    for k, sub in schema.get("properties", {}).items():
        if sub.get("type") == "object" and "properties" in sub:
            out[k] = _defaults_from_schema(sub)
        elif "default" in sub:
            out[k] = sub["default"]
    return out


def _build_clev2_settings(params: dict) -> Settings:
    """Map the flat-ish JSON params → the nested CLEv2 Settings dataclass.

    Any field missing in `params` falls back to the dataclass default,
    which itself matches CLEv2's production default.
    """
    g = params.get("general") or {}
    r = params.get("rules") or {}
    c = params.get("controls") or {}
    s = params.get("signals") or {}
    k = params.get("risk") or {}

    general = GeneralSettings(
        point_value=float(g.get("point_value", 1.0)),
        countries=list(g.get("countries", ["GB", "IE"])),
        process_window_mins=int(g.get("process_window_mins", 5)),
        mode=Mode.DRY_RUN,  # plugin doesn't decide mode — host gates execution
    )
    rules = BaseRules(
        rule1_enabled=bool(r.get("rule1_enabled", True)),
        rule1_stake=float(r.get("rule1_stake", 3.0)),
        rule2a_enabled=bool(r.get("rule2a_enabled", True)),
        rule2a_stake=float(r.get("rule2a_stake", 0.0)),
        rule2b_enabled=bool(r.get("rule2b_enabled", True)),
        rule2b_stake=float(r.get("rule2b_stake", 1.0)),
        rule2c_enabled=bool(r.get("rule2c_enabled", True)),
        rule2c_stake=float(r.get("rule2c_stake", 2.0)),
        rule3a_enabled=bool(r.get("rule3a_enabled", True)),
        rule3a_stake=float(r.get("rule3a_stake", 1.0)),
        rule3b_enabled=bool(r.get("rule3b_enabled", True)),
        rule3b_stake=float(r.get("rule3b_stake", 1.0)),
        rule2_split1=float(r.get("rule2_split1", 3.0)),
        rule2_split2=float(r.get("rule2_split2", 4.0)),
        rule3_gap_threshold=float(r.get("rule3_gap_threshold", 2.0)),
    )
    controls = Controls(
        spread_control_enabled=bool(c.get("spread_control_enabled", True)),
        jofs_enabled=bool(c.get("jofs_enabled", True)),
        mark_ceiling_enabled=bool(c.get("mark_ceiling_enabled", False)),
        mark_ceiling_value=float(c.get("mark_ceiling_value", 8.0)),
        mark_floor_enabled=bool(c.get("mark_floor_enabled", False)),
        mark_floor_value=float(c.get("mark_floor_value", 1.5)),
        mark_uplift_enabled=bool(c.get("mark_uplift_enabled", False)),
        mark_uplift_stake=float(c.get("mark_uplift_stake", 3.0)),
    )
    signals = SignalToggles(
        signal_overround_enabled=bool(s.get("signal_overround_enabled", False)),
        overround_soft_threshold=float(s.get("overround_soft_threshold", 1.15)),
        overround_hard_threshold=float(s.get("overround_hard_threshold", 1.20)),
        signal_field_size_enabled=bool(s.get("signal_field_size_enabled", False)),
        field_size_max_runners=int(s.get("field_size_max_runners", 16)),
        field_size_odds_min=float(s.get("field_size_odds_min", 1.5)),
        field_size_stake_cap=float(s.get("field_size_stake_cap", 2.0)),
        signal_steam_gate_enabled=bool(s.get("signal_steam_gate_enabled", False)),
        steam_gate_odds_min=float(s.get("steam_gate_odds_min", 2.0)),
        steam_shortening_pct=float(s.get("steam_shortening_pct", 10.0)),
        signal_band_perf_enabled=bool(s.get("signal_band_perf_enabled", False)),
        band_perf_lookback_days=int(s.get("band_perf_lookback_days", 30)),
        band_perf_min_win_rate=float(s.get("band_perf_min_win_rate", 0.55)),
        band_perf_min_sample=int(s.get("band_perf_min_sample", 50)),
        band_perf_reduced_stake=float(s.get("band_perf_reduced_stake", 0.5)),
    )
    risk = RiskOverlay(
        top2_concentration_enabled=bool(k.get("top2_concentration_enabled", False)),
        market_overlay_enabled=bool(k.get("market_overlay_enabled", False)),
    )

    return Settings(
        general=general,
        rules=rules,
        controls=controls,
        signals=signals,
        risk=risk,
    )


class MarkSixRulesV1:
    """First plugin — ports CLEv2's 13-step pipeline behind the
    StrategyPlugin Protocol. Logic unchanged."""

    # ── identity ──────────────────────────────────────────────────────

    id: str = "mark_6_rules_v1"
    version: str = "1.0.0"
    description: str = (
        "Mark's 6-rule horse-racing lay strategy, ported verbatim "
        "from CLEv2's evaluator (300-line 13-step pipeline)."
    )

    def __init__(self) -> None:
        # `_params` mirrors the JSON dict the host sends in via configure().
        # Initialised to the schema defaults so the plugin works even if
        # configure() has never been called.
        self._params: dict[str, Any] = _defaults_from_schema(self.parameter_schema)
        # Session counters — surfaced via get_state / get_results.
        self._eval_count = 0
        self._skip_count = 0
        self._instr_count = 0
        self._by_rule: dict[str, int] = {}

    # ── declarative schemas (data) ────────────────────────────────────

    @property
    def parameter_schema(self) -> dict:
        return copy.deepcopy(ps_module.PARAMETER_SCHEMA)

    @property
    def result_schema(self) -> dict:
        return copy.deepcopy(rs_module.RESULT_SCHEMA)

    # ── lifecycle ─────────────────────────────────────────────────────

    def load(self, *, host_context: dict) -> None:
        """No-op at load — adapter has no startup side effects."""
        logger.info("plugin.mark_6_rules_v1 loaded (version=%s)", self.version)

    def configure(self, parameters: dict) -> None:
        """Apply new parameters. The host validates against
        parameter_schema BEFORE calling, so trust the input shape."""
        self._params = parameters
        logger.info("plugin.mark_6_rules_v1 reconfigured")

    def get_parameters(self) -> dict:
        return copy.deepcopy(self._params)

    # ── per-event hooks ───────────────────────────────────────────────

    def on_market_event(self, snapshot: MarketSnapshot) -> EvaluationResult:
        """Adapt SSE → ClevSnapshot, run the 13-step pipeline, translate back."""
        self._eval_count += 1

        # `snapshot.raw` is the original SSE payload — we use it because
        # the adapter needs `market_definition` (if present) for runner names.
        _, clev_snapshot = snapshot_to_plugin_snapshot(snapshot.raw or {})
        clev_settings = _build_clev2_settings(self._params)

        clev_result = evaluator.evaluate(clev_snapshot, clev_settings)

        # Translate CLEv2's LayInstruction → host Instruction.
        host_instructions: list[Instruction] = []
        for li in clev_result.instructions:
            host_instructions.append(
                Instruction(
                    action="PLACE",
                    side="LAY",
                    selection_id=int(li.selection_id),
                    selection_name=str(li.runner_name),
                    price=float(li.price),
                    size=float(li.size),
                    persistence_type="LAPSE",
                    customer_strategy_ref=self.id,
                    decision={
                        "rule_applied": li.rule_applied,
                        "liability": float(li.liability),
                    },
                )
            )

        # Counters.
        if clev_result.skipped or not host_instructions:
            self._skip_count += 1
        else:
            self._instr_count += len(host_instructions)
            if clev_result.rule_applied:
                self._by_rule[clev_result.rule_applied] = (
                    self._by_rule.get(clev_result.rule_applied, 0) + 1
                )

        return EvaluationResult(
            market_id=str(clev_result.market_id),
            plugin_id=self.id,
            plugin_version=self.version,
            ts=datetime.now(timezone.utc),
            instructions=host_instructions,
            skip_reason=clev_result.skip_reason if clev_result.skipped else None,
            evidence={
                "pipeline": list(clev_result.pipeline),
                "favourite": clev_result.favourite,
                "second_favourite": clev_result.second_favourite,
                "rule_applied": clev_result.rule_applied,
            },
        )

    def on_market_closed(self, market_id: str, final_snapshot: MarketSnapshot) -> None:
        # No special handling — the 13-step pipeline is stateless across markets.
        return None

    # ── operational reads ─────────────────────────────────────────────

    def get_state(self) -> dict:
        return {
            "version": self.version,
            "eval_count": self._eval_count,
            "instr_count": self._instr_count,
            "skip_count": self._skip_count,
            "by_rule": dict(self._by_rule),
        }

    def get_results(self) -> dict:
        # Same as state for now — Phase 5 may differentiate (session
        # results = P&L; state = runtime counters).
        return self.get_state()

    def unload(self) -> None:
        return None

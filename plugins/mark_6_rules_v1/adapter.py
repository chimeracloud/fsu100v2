"""
Adapter — FSU1B SSE payload → CLEv2 MarketSnapshot.

The CLEv2 evaluator (ported verbatim into this plugin) expects its own
shape (clev2_models.MarketSnapshot + clev2_models.Runner — actually
Runner lives in rules.py). This adapter does the one-way translation
WITHOUT touching the evaluator's logic.

Approach A from the build brief (§9). Safer than rewriting the
evaluator because zero risk of changing decisions.

Gap noted: FSU1B's SSE `runners[]` doesn't currently include
runner_name (names live in market_definition / catalogue REST).
Until Phase 3 wires a catalogue lookup or FSU1B's SSE is enriched,
we fall back to f"Selection {selection_id}". The evaluator doesn't
use runner_name in decisions — only for display — so this is safe
for parity. Document, don't paper over.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.plugin_api import MarketSnapshot as PluginSnapshot

from .clev2_models import MarketSnapshot as ClevSnapshot
from .rules import Runner


def _parse_iso(s: str | None) -> str:
    """Pass through ISO strings unchanged; None becomes empty string.

    The evaluator stores race_time / snapshot_at as ISO 8601 strings,
    not datetime objects, so we just pass through.
    """
    return s or ""


def _runner_name_from_market_def(market: dict, selection_id: int) -> str:
    """Try to find a runner name in market_definition.runners.

    The FSU1B SSE payload normally has `market.name` (display string
    like "14:15 Newmarket") but `runners[]` rows omit runner names —
    those live in market_definition. If market_definition is present
    on the payload we use it; otherwise fall back to a placeholder.
    """
    md = market.get("market_definition") or {}
    md_runners = md.get("runners") or []
    for r in md_runners:
        if int(r.get("id", -1)) == selection_id:
            name = r.get("name") or r.get("runner_name")
            if name:
                return str(name)
    return f"Selection {selection_id}"


def _best_price(runner_payload: dict, key: str) -> float | None:
    """Extract the best price from `back` or `lay` ladder.

    FSU1B's SSE has `back: [[price, size], ...]` already sorted best-first.
    """
    ladder = runner_payload.get(key) or []
    if not ladder:
        return None
    try:
        return float(ladder[0][0])
    except (IndexError, TypeError, ValueError):
        return None


def sse_event_to_snapshot(sse_payload: dict[str, Any]) -> ClevSnapshot:
    """Convert one FSU1B SSE `market_change` payload → CLEv2 MarketSnapshot.

    Args:
        sse_payload: The dict that arrived on /stream/horse-racing.
            Must have `market` and `runners` keys. `ts` is optional.

    Returns:
        A CLEv2 MarketSnapshot that the unchanged evaluator can consume.
    """
    market = sse_payload.get("market") or {}
    runners_raw = sse_payload.get("runners") or []

    runners: list[Runner] = []
    for r in runners_raw:
        try:
            sid = int(r.get("selection_id"))
        except (TypeError, ValueError):
            continue
        runners.append(
            Runner(
                selection_id=sid,
                runner_name=_runner_name_from_market_def(market, sid),
                handicap=float(r.get("handicap") or 0.0),
                best_available_to_lay=_best_price(r, "lay"),
                best_available_to_back=_best_price(r, "back"),
                status=str(r.get("status") or "ACTIVE"),
            )
        )

    return ClevSnapshot(
        market_id=str(market.get("market_id") or ""),
        market_name=str(market.get("name") or ""),
        venue=str(market.get("venue") or ""),
        country=str(market.get("country_code") or ""),
        race_time=_parse_iso(market.get("market_time")),
        snapshot_at=str(sse_payload.get("ts") or datetime.now(timezone.utc).isoformat()),
        runners=runners,
        # previous_prices + band_stats arrive via separate mechanisms
        # (Steam Gate fixtures, Band Performance rolling stats). Phase 3
        # may inject these via plugin state; for now an empty dict —
        # the signal filters skip with reason "no prev price" / "below
        # min sample" which is the correct behaviour.
        previous_prices={},
        band_stats={},
    )


def snapshot_to_plugin_snapshot(
    sse_payload: dict[str, Any]
) -> tuple[PluginSnapshot, ClevSnapshot]:
    """Return both shapes — the PluginSnapshot (used for `evidence` /
    portal display) AND the ClevSnapshot (consumed by the evaluator)."""

    clev = sse_event_to_snapshot(sse_payload)

    market = sse_payload.get("market") or {}
    plugin_snapshot = PluginSnapshot(
        ts=datetime.fromisoformat(
            (sse_payload.get("ts") or datetime.now(timezone.utc).isoformat()).replace("Z", "+00:00")
        ),
        source=str(sse_payload.get("source") or ""),
        source_type=str(sse_payload.get("source_type") or ""),
        sport=str(sse_payload.get("sport") or "horse-racing"),
        event_type_id=str(sse_payload.get("event_type_id") or "7"),
        market=market,
        runners=sse_payload.get("runners") or [],
        change_type=sse_payload.get("change_type"),
        raw=sse_payload,
    )
    return plugin_snapshot, clev

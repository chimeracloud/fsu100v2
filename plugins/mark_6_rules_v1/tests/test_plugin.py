"""End-to-end plugin test against synthetic FSU1B-shaped SSE payloads.

Rule reference (from CLEv2 rules.py):
  RULE 1   fav < 2.0                       default stake 3pt
  RULE 2A  2.0 ≤ fav < split1 (3.0)        default stake 0 (skip band)
  RULE 2B  split1 (3.0) ≤ fav < split2 (4.0)   default stake 1pt
  RULE 2C  split2 (4.0) ≤ fav < 5.0        default stake 2pt
  RULE 3A  fav > 5.0, gap < threshold (2.0)    split between fav + 2nd
  RULE 3B  fav > 5.0, gap ≥ threshold      fav only

Spread bands (rejects when spread = lay-back > threshold for the band):
  1.0–2.0:  0.05
  2.0–3.0:  0.15
  3.0–5.0:  0.30
  5.0–8.0:  0.50
  8.0+:     REJECT (always)
"""
from datetime import datetime, timezone

from core.plugin_api import MarketSnapshot
from plugins.mark_6_rules_v1.plugin import MarkSixRulesV1


def _ms(sse: dict) -> MarketSnapshot:
    return MarketSnapshot(
        ts=datetime.now(timezone.utc),
        source=sse.get("source", "fsu1b"),
        source_type=sse.get("source_type", "live"),
        sport=sse.get("sport", "horse-racing"),
        event_type_id=sse.get("event_type_id", "7"),
        market=sse["market"],
        runners=sse["runners"],
        change_type=sse.get("change_type"),
        raw=sse,
    )


def _market(market_id: str, name: str, venue: str = "Test") -> dict:
    return {
        "market_id": market_id,
        "name": name,
        "venue": venue,
        "country_code": "GB",
        "market_time": "2026-06-04T14:15:00Z",
        "market_type": "WIN",
        "status": "OPEN",
        "in_play": False,
        "event_type_id": "7",
    }


def _runner(sid: int, lay: float | None, back: float | None, status: str = "ACTIVE") -> dict:
    r: dict = {"selection_id": sid, "handicap": 0.0, "status": status}
    if lay is not None:
        r["lay"] = [[lay, 100]]
    if back is not None:
        r["back"] = [[back, 100]]
    return r


# ── tests ───────────────────────────────────────────────────────────────


def test_plugin_identity():
    p = MarkSixRulesV1()
    assert p.id == "mark_6_rules_v1"
    assert p.version == "1.0.0"
    assert "Mark" in p.parameter_schema["title"]


def test_rule_1_fires_when_fav_below_2():
    """Rule 1 = favourite < 2.0 → £3 LAY. Tight spread (≤ 0.05) for the 1.0–2.0 band."""
    p = MarkSixRulesV1()
    sse = {
        "market": _market("1.r1", "14:15 Newmarket"),
        "runners": [
            _runner(101, lay=1.80, back=1.82),   # favourite — Rule 1 band
            _runner(102, lay=5.50, back=5.70),
            _runner(103, lay=8.00, back=8.50),
        ],
    }
    result = p.on_market_event(_ms(sse))
    assert result.skip_reason is None, f"unexpected skip: {result.skip_reason}"
    assert len(result.instructions) == 1
    instr = result.instructions[0]
    assert instr.side == "LAY"
    assert instr.selection_id == 101
    assert "RULE_1" in (instr.decision.get("rule_applied") or "")


def test_rule_2b_fires_in_band():
    """Rule 2B = 3.0 ≤ fav < 4.0 → £1 LAY."""
    p = MarkSixRulesV1()
    sse = {
        "market": _market("1.r2b", "14:25 Newmarket"),
        "runners": [
            _runner(201, lay=3.50, back=3.60),   # favourite mid 2B band
            _runner(202, lay=5.00, back=5.20),
            _runner(203, lay=8.00, back=8.50),
        ],
    }
    result = p.on_market_event(_ms(sse))
    assert result.skip_reason is None
    assert len(result.instructions) == 1
    assert result.instructions[0].selection_id == 201
    assert "RULE_2B" in (result.instructions[0].decision.get("rule_applied") or "")


def test_rule_2a_default_skip_band():
    """Default rule2a_stake=0 means 'skip band' — fav in 2.0–3.0 produces no bet."""
    p = MarkSixRulesV1()
    sse = {
        "market": _market("1.r2a", "14:35 Test"),
        "runners": [
            _runner(301, lay=2.50, back=2.55),   # fav in 2A skip-band
            _runner(302, lay=5.00, back=5.20),
        ],
    }
    result = p.on_market_event(_ms(sse))
    assert result.skip_reason is not None
    assert "skip band" in result.skip_reason
    assert result.instructions == []


def test_spread_control_blocks_when_fav_above_8():
    """Spread threshold returns None for lay ≥ 8.0 → REJECT outright."""
    p = MarkSixRulesV1()
    sse = {
        "market": _market("1.spread", "14:45 Test"),
        "runners": [
            _runner(401, lay=9.00, back=9.10),   # spread tiny but band REJECTS
            _runner(402, lay=12.00, back=12.50),
        ],
    }
    result = p.on_market_event(_ms(sse))
    assert result.skip_reason is not None
    assert "Spread" in result.skip_reason
    assert result.instructions == []


def test_spread_control_blocks_wide_spread_within_band():
    """Lay 5.5 / Back 5.0 → spread 0.5 > 0.30 max for the 3.0–5.0 band → REJECT."""
    p = MarkSixRulesV1()
    sse = {
        "market": _market("1.spread2", "14:55 Test"),
        "runners": [
            _runner(501, lay=4.50, back=4.00),   # spread = 0.5, band 3.0–5.0 max 0.30
            _runner(502, lay=7.00, back=7.20),
        ],
    }
    result = p.on_market_event(_ms(sse))
    assert result.skip_reason is not None
    assert "Spread" in result.skip_reason
    assert result.instructions == []


def test_evidence_carries_full_pipeline_trace():
    """A Rule-2B-fires case runs the WHOLE pipeline; trace has all six steps."""
    p = MarkSixRulesV1()
    sse = {
        "market": _market("1.trace", "15:05 Test"),
        "runners": [
            _runner(601, lay=3.50, back=3.60),   # Rule 2B fires
            _runner(602, lay=5.00, back=5.20),
        ],
    }
    result = p.on_market_event(_ms(sse))
    assert "pipeline" in result.evidence
    steps = [s["step"] for s in result.evidence["pipeline"]]
    for expected in ("spread_control", "core_rules", "point_value", "top2", "market_overlay"):
        assert expected in steps, f"missing step '{expected}' in {steps}"


def test_session_counters_increment_on_real_instructions():
    """Two Rule-2B markets → eval_count=2, instr_count=2."""
    p = MarkSixRulesV1()
    sse = {
        "market": _market("1.count", "15:15 Test"),
        "runners": [
            _runner(701, lay=3.50, back=3.60),
            _runner(702, lay=5.00, back=5.20),
        ],
    }
    p.on_market_event(_ms(sse))
    p.on_market_event(_ms(sse))
    state = p.get_state()
    assert state["eval_count"] == 2
    assert state["instr_count"] == 2

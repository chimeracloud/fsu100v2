"""Phase 3 — dispatcher: three outputs, fallback when destinations absent, DRY_RUN."""
import asyncio
from datetime import datetime, timezone

import pytest

from core.config import replace_settings, reset_settings_for_test
from core.plugin_api import EvaluationResult, Instruction
from core.state import app_state, reset_state_for_test
from services import dispatcher, event_recorder


@pytest.fixture(autouse=True)
def _isolated():
    reset_settings_for_test()
    reset_state_for_test()
    event_recorder.reset_for_test()
    dispatcher.reset_token_cache_for_test()
    yield
    reset_settings_for_test()
    reset_state_for_test()
    event_recorder.reset_for_test()


def _evaluation_with_one_instruction() -> EvaluationResult:
    return EvaluationResult(
        market_id="1.test",
        plugin_id="mark_6_rules_v1",
        plugin_version="1.0.0",
        ts=datetime.now(timezone.utc),
        instructions=[
            Instruction(
                action="PLACE",
                side="LAY",
                selection_id=42,
                selection_name="Test Runner",
                price=4.5,
                size=2.0,
                persistence_type="LAPSE",
                customer_strategy_ref="mark_6_rules_v1",
                decision={"rule_applied": "RULE_2C: test"},
            )
        ],
        evidence={"pipeline": [], "rule_applied": "RULE_2C"},
    )


@pytest.mark.asyncio
async def test_dispatch_fallback_to_ndjson_when_lbcf_absent():
    # No fallback URLs configured → LBCF + FSU2A both absent → NDJSON fallback.
    result = _evaluation_with_one_instruction()
    summary = await dispatcher.dispatch(
        result,
        market_summary={"market_id": "1.test", "name": "Test", "venue": "Test"},
        source_id="fsu1b", source_type="live", snapshot_ts=None,
    )
    assert summary["instructions_emitted"] == 1
    assert summary["lbcf_posted"] == 0
    assert summary["fsu2a_posted"] == 0
    assert summary["broadcast"] == 1

    # Warnings raised.
    assert "live_betting_control_unreachable: instructions queued to GCS" in app_state.warnings
    assert "fsu2a_unreachable: evaluation events queued to GCS" in app_state.warnings

    # NDJSON test ring buffer captured the rows.
    assert len(event_recorder.instructions_writer.test_buffer()) == 1
    assert len(event_recorder.evaluations_writer.test_buffer()) == 1


@pytest.mark.asyncio
async def test_dispatch_dry_run_skips_real_posts_but_records_ndjson():
    replace_settings(dry_run=True)
    result = _evaluation_with_one_instruction()
    summary = await dispatcher.dispatch(
        result,
        market_summary={"market_id": "1.test", "name": "Test", "venue": "Test"},
        source_id="fsu1b", source_type="live", snapshot_ts=None,
    )
    assert summary["dry_run"] is True
    assert summary["lbcf_posted"] == 0
    assert summary["fsu2a_posted"] == 0
    # NDJSON still got the rows so the parity test can read them later.
    assert len(event_recorder.instructions_writer.test_buffer()) == 1

    # DRY_RUN should also flag in activity.
    kinds = [e["kind"] for e in app_state.recent_activity()]
    assert "dry_run_dispatch_skipped" in kinds


@pytest.mark.asyncio
async def test_dispatch_counters_increment():
    result = _evaluation_with_one_instruction()
    await dispatcher.dispatch(
        result,
        market_summary={"market_id": "1.test", "name": "Test", "venue": "Test"},
        source_id="fsu1b", source_type="live", snapshot_ts=None,
    )
    assert app_state.evaluation_count == 1
    assert app_state.instruction_count == 1
    assert app_state.evaluations_by_plugin["mark_6_rules_v1"] == 1
    assert app_state.instructions_by_plugin["mark_6_rules_v1"] == 1
    # Recent instructions cache.
    assert len(app_state.recent_instructions) == 1


@pytest.mark.asyncio
async def test_dispatch_skip_increments_skip_count():
    result = EvaluationResult(
        market_id="1.skip", plugin_id="mark_6_rules_v1", plugin_version="1.0.0",
        ts=datetime.now(timezone.utc),
        instructions=[], skip_reason="Test skip",
        evidence={"pipeline": []},
    )
    await dispatcher.dispatch(
        result,
        market_summary={"market_id": "1.skip", "name": "Test", "venue": "Test"},
        source_id="fsu1b", source_type="live", snapshot_ts=None,
    )
    assert app_state.evaluation_count == 1
    assert app_state.skip_count == 1
    assert app_state.instruction_count == 0


@pytest.mark.asyncio
async def test_dispatch_broadcasts_to_evaluations_channel():
    queue = await app_state.subscribe("evaluations")
    try:
        result = _evaluation_with_one_instruction()
        await dispatcher.dispatch(
            result,
            market_summary={"market_id": "1.b", "name": "Test", "venue": "Test"},
            source_id="fsu1b", source_type="live", snapshot_ts=None,
        )
        msg = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert msg["event"] == "evaluation"
        assert msg["market_id"] == "1.test"
        assert msg["plugin_id"] == "mark_6_rules_v1"
        assert len(msg["instructions"]) == 1
    finally:
        await app_state.unsubscribe("evaluations", queue)

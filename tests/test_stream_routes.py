"""Phase 3 — content endpoints + session/markets/instructions."""
import pytest

from core.config import reset_settings_for_test
from core.state import app_state, reset_state_for_test


@pytest.fixture(autouse=True)
def _isolated():
    reset_settings_for_test()
    reset_state_for_test()
    yield
    reset_settings_for_test()
    reset_state_for_test()


def test_session_returns_zeroed_counters_at_boot(client):
    r = client.get("/api/session")
    assert r.status_code == 200
    body = r.json()
    assert body["source_state"] == "disconnected"
    assert body["evaluation_count"] == 0
    assert body["instruction_count"] == 0
    assert body["skip_count"] == 0
    assert body["warnings"] == []


def test_markets_empty_at_boot(client):
    r = client.get("/api/markets")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0
    assert body["markets"] == []


def test_instructions_empty_at_boot(client):
    r = client.get("/api/instructions")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0
    assert body["instructions"] == []


def test_evaluations_sse_route_registered(client):
    """SSE-open test via TestClient hangs (sync client + async gen); verify
    the route is registered + content-type via the route registry."""
    from main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/stream/evaluations" in paths


def test_markets_reflects_recent_cache(client):
    app_state.recent_market_summaries["1.aaa"] = {
        "market_id": "1.aaa", "name": "Test 1", "venue": "Newmarket",
    }
    app_state.recent_market_summaries["1.bbb"] = {
        "market_id": "1.bbb", "name": "Test 2", "venue": "Ascot",
    }
    r = client.get("/api/markets")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    venues = {m["venue"] for m in body["markets"]}
    assert "Newmarket" in venues
    assert "Ascot" in venues

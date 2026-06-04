"""Phase 1 — verify the standard observability shell is wired."""

EXPECTED_PHASE = 3


def test_health_returns_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_ready_idle_when_source_not_started(client):
    r = client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["ready"] is True
    assert body["mode"] == "idle"
    assert body["phase"] == EXPECTED_PHASE


def test_ready_connected_idle_when_source_connected_but_quiet(client):
    """Source connected but no recent market_change events (e.g. between
    races) is a normal state — must return 200 not 503."""
    from datetime import datetime, timedelta, timezone
    from core.state import app_state

    app_state.source.state = "connected"
    app_state.source.last_message_at = (
        datetime.now(timezone.utc) - timedelta(seconds=600)
    )
    try:
        r = client.get("/ready")
        assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"
        body = r.json()
        assert body["ready"] is True
        assert body["mode"] == "connected_idle"
        assert body["source_state"] == "connected"
    finally:
        app_state.source.state = "disconnected"
        app_state.source.last_message_at = None


def test_ready_503_only_when_reconnecting_stale(client):
    """503 must be reserved for actively-reconnecting + stale states."""
    from datetime import datetime, timedelta, timezone
    from core.state import app_state

    app_state.source.state = "reconnecting"
    app_state.source.last_message_at = (
        datetime.now(timezone.utc) - timedelta(seconds=600)
    )
    try:
        r = client.get("/ready")
        assert r.status_code == 503
        body = r.json()
        assert body["ready"] is False
        assert body["mode"] == "reconnecting"
    finally:
        app_state.source.state = "disconnected"
        app_state.source.last_message_at = None


def test_info_identifies_service(client):
    r = client.get("/info")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "fsu100v2"
    assert body["phase"] == EXPECTED_PHASE
    assert "version" in body


def test_metrics_is_prometheus_plaintext(client):
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    assert "fsu100v2_uptime_seconds" in r.text
    assert "fsu100v2_evaluation_total" in r.text
    assert "fsu100v2_instruction_total" in r.text


def test_status_shape(client):
    r = client.get("/status")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "fsu100v2"
    assert body["phase"] == EXPECTED_PHASE
    assert "uptime_s" in body
    assert "now" in body
    assert "source_state" in body
    assert "evaluation_count" in body

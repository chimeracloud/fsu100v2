"""Phase 1 — verify the standard observability shell is wired."""

EXPECTED_PHASE = 2


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

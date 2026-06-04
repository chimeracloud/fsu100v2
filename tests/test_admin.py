"""Phase 1 + 2 — verify the Set 1 admin shell is wired and shapes are stable."""


def test_admin_status_shape(client):
    r = client.get("/admin/status")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "fsu100v2"
    assert body["phase"] == 3
    assert body["source"]["state"] == "disconnected"
    # Phase 2: mark_6_rules_v1 is loaded on boot.
    plugin_ids = {p["id"] for p in body["plugins"]}
    assert "mark_6_rules_v1" in plugin_ids
    assert body["warnings"] == []


def test_admin_config_returns_defaults(client):
    r = client.get("/admin/config")
    assert r.status_code == 200
    body = r.json()
    assert body["source_id"] == "fsu1b"
    assert body["source_type"] == "live"
    assert body["source_sport_endpoint"] == "/stream/horse-racing"
    assert body["loaded_plugins"] == ["mark_6_rules_v1"]
    assert body["auto_start"] is False
    assert body["dry_run"] is False
    assert body["log_level"] == "INFO"
    assert body["stream_check_interval_s"] == 30
    assert body["stream_stale_threshold_s"] == 60
    assert "fallback_urls" in body


def test_admin_config_put_updates_and_persists(client, monkeypatch):
    """PUT applies in-memory AND persists to GCS. Test mocks the persistence."""
    persisted = []

    def fake_save(payload):
        from core.config import apply_dict
        apply_dict(payload)
        persisted.append(payload)
        return True

    from services import admin as admin_module
    monkeypatch.setattr(admin_module, "save_config_to_gcs", fake_save)

    r = client.put("/admin/config", json={"dry_run": True})
    assert r.status_code == 200
    assert r.json()["dry_run"] is True
    assert persisted == [{"dry_run": True}]

    client.put("/admin/config", json={"dry_run": False})


def test_admin_config_put_returns_502_on_persistence_failure(client, monkeypatch):
    def fake_save(payload):
        from core.config import apply_dict
        apply_dict(payload)
        return False  # GCS failed

    from services import admin as admin_module
    monkeypatch.setattr(admin_module, "save_config_to_gcs", fake_save)

    r = client.put("/admin/config", json={"dry_run": True})
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert detail["ok"] is False
    assert detail["applied_in_memory"] is True
    assert detail["persisted_to_gcs"] is False

    monkeypatch.undo()
    from core.config import apply_dict
    apply_dict({"dry_run": False})


def test_admin_stats_shape(client):
    r = client.get("/admin/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["evaluation_count"] == 0
    assert body["instruction_count"] == 0
    assert body["skip_count"] == 0
    assert body["evaluations_by_plugin"] == {}
    assert "last_call_at_by_endpoint" in body


def test_admin_stats_records_real_endpoint_calls(client):
    """Middleware records every non-observability call."""
    client.get("/admin/config")
    client.get("/admin/status")

    r = client.get("/admin/stats")
    body = r.json()
    assert "/admin/config" in body["last_call_at_by_endpoint"]
    assert "/admin/status" in body["last_call_at_by_endpoint"]
    assert body["call_count_by_endpoint"]["/admin/config"] >= 1


def test_admin_stats_excludes_observability_paths(client):
    client.get("/health")
    client.get("/ready")
    client.get("/metrics")
    r = client.get("/admin/stats")
    body = r.json()
    for p in ("/health", "/ready", "/metrics", "/info", "/status"):
        assert p not in body["last_call_at_by_endpoint"]


def test_admin_activity_records_boot_events(client):
    r = client.get("/admin/activity")
    assert r.status_code == 200
    events = r.json()["events"]
    kinds = {e["kind"] for e in events}
    assert any(k.startswith("event:engine_") for k in kinds), (
        f"no engine event in activity feed; kinds={kinds!r}"
    )


def test_admin_control_test_verb(client):
    r = client.post("/admin/control/test")
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] is True
    assert body["executed"] is True


def test_admin_control_rejects_unknown_verb(client):
    r = client.post("/admin/control/blow_up")
    assert r.status_code == 422


def test_admin_control_start_now_wires(client):
    """Phase 3: start is wired through stream_session.start()."""
    r = client.post("/admin/control/start")
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] is True
    assert body["executed"] is True

    # Tear down so other tests aren't affected.
    client.post("/admin/control/stop")


def test_admin_control_reload_plugins_still_stub(client):
    r = client.post("/admin/control/reload_plugins")
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] is True
    assert body["executed"] is False
    assert "later phase" in body["note"]


def test_admin_events_sse_opens(client):
    with client.stream("GET", "/admin/events") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")

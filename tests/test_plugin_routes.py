"""Phase 2 — /api/plugins/* endpoints."""
import pytest

from core.config import reset_settings_for_test
from core.state import reset_state_for_test
from services import plugin_loader


@pytest.fixture(autouse=True)
def _isolated():
    reset_settings_for_test()
    reset_state_for_test()
    plugin_loader.reset_for_test()
    yield
    reset_settings_for_test()
    reset_state_for_test()
    plugin_loader.reset_for_test()


def test_list_plugins_includes_mark_6_rules_v1(client):
    r = client.get("/api/plugins")
    assert r.status_code == 200
    plugins = r.json()["plugins"]
    ids = {p["id"] for p in plugins}
    assert "mark_6_rules_v1" in ids


def test_plugin_detail_404_for_unknown(client):
    r = client.get("/api/plugins/does_not_exist")
    assert r.status_code == 404


def test_plugin_config_returns_schema_and_values(client):
    r = client.get("/api/plugins/mark_6_rules_v1/config")
    assert r.status_code == 200
    body = r.json()
    assert "schema" in body
    assert "values" in body
    assert body["schema"]["title"].startswith("Mark 6 Rules")
    assert body["values"]["rules"]["rule1_stake"] == 3.0


def test_plugin_config_put_persists_with_valid_body(client, monkeypatch):
    persisted = {}

    def fake_save(plugin_id, params):
        persisted[plugin_id] = params
        return True

    from services import plugin_routes
    monkeypatch.setattr(plugin_routes, "save_plugin_config", fake_save)

    # Bring current values, tweak one field, PUT back.
    current = client.get("/api/plugins/mark_6_rules_v1/config").json()["values"]
    current["rules"]["rule1_stake"] = 5.0
    r = client.put("/api/plugins/mark_6_rules_v1/config", json=current)
    assert r.status_code == 200
    assert r.json()["values"]["rules"]["rule1_stake"] == 5.0
    assert persisted["mark_6_rules_v1"]["rules"]["rule1_stake"] == 5.0


def test_plugin_config_put_rejects_invalid_body(client):
    bad = {"rules": {"rule1_stake": "not-a-number"}}
    r = client.put("/api/plugins/mark_6_rules_v1/config", json=bad)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["ok"] is False
    assert detail["errors"]


def test_plugin_config_put_returns_502_when_persistence_fails(client, monkeypatch):
    from services import plugin_routes

    def fake_save(plugin_id, params):
        return False

    monkeypatch.setattr(plugin_routes, "save_plugin_config", fake_save)

    current = client.get("/api/plugins/mark_6_rules_v1/config").json()["values"]
    r = client.put("/api/plugins/mark_6_rules_v1/config", json=current)
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert detail["applied_in_memory"] is True
    assert detail["persisted_to_gcs"] is False


def test_plugin_state_and_results(client):
    r = client.get("/api/plugins/mark_6_rules_v1/state")
    assert r.status_code == 200
    state = r.json()["state"]
    assert state["version"] == "1.0.0"
    assert state["eval_count"] == 0

    r = client.get("/api/plugins/mark_6_rules_v1/results")
    assert r.status_code == 200

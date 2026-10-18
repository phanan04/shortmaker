import pytest

from conftest import create_manual
from shorts.app import create_app
from shorts.config import Settings
from fastapi.testclient import TestClient


def test_create_validation_and_batch(backend):
    app, client = backend
    assert client.get("/health").json()["paid_apis"] is False
    assert client.post("/v1/jobs", json={"character": ""}).status_code == 422
    assert client.post("/v1/jobs", json={"character": "A", "preset": {"width": 181}}).status_code == 422
    response = client.post("/v1/jobs/batch", json=[{"character": "A", "auto_start": False}, {"character": "B", "auto_start": False}])
    assert response.status_code == 201
    assert len(response.json()) == 2
    assert len(client.get("/v1/jobs").json()) == 2
    assert client.get("/v1/jobs/missing").status_code == 404


def test_state_controls_and_event_cursor(backend):
    app, client = backend
    job = client.post("/v1/jobs", json={"character": "A"}).json()
    key = job["id"]
    assert job["status"] == "queued"
    assert client.patch(f"/v1/jobs/{key}", json={"angle": "Another"}).status_code == 409
    assert client.post(f"/v1/jobs/{key}/pause").json()["status"] == "paused"
    assert client.patch(f"/v1/jobs/{key}", json={"angle": "Another"}).status_code == 200
    assert client.post(f"/v1/jobs/{key}/cancel").json()["status"] == "cancelled"
    first = client.get(f"/v1/jobs/{key}/events?limit=1").json()
    rest = client.get(f'/v1/jobs/{key}/events?after={first[0]["id"]}').json()
    assert all(event["id"] > first[0]["id"] for event in rest)


def test_auth_and_cross_origin(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, api_token="x" * 32), start_worker=False)
    with TestClient(app) as client:
        assert client.get("/v1/jobs").status_code == 401
        assert client.get("/v1/jobs", headers={"Authorization": "Bearer " + "x"*32}).status_code == 200
        assert client.post("/v1/jobs", headers={"Origin": "https://evil.example"}, json={"character": "A"}).status_code == 403
        assert client.get("/health", headers={"Host": "evil.example"}).status_code == 403


def test_no_paid_endpoints():
    with pytest.raises(ValueError):
        Settings(ollama_url="https://api.example.com")
    with pytest.raises(ValueError):
        Settings(host="0.0.0.0")
    with pytest.raises(ValueError):
        Settings(tts_url="http://localhost:9000/?api_key=secret")


def test_asset_validation_and_file_access(backend):
    app, client = backend
    assert client.post("/v1/assets", files={"file": ("bad.png", b"not an image")}).status_code == 422
    assert client.get("/v1/assets/missing/file").status_code == 404
    job = client.post("/v1/jobs", json={"character": "A", "auto_start": False}).json()
    assert client.get(f'/v1/jobs/{job["id"]}/files/metadata.json').status_code == 404
    assert client.post(f'/v1/jobs/{job["id"]}/approve', json={"content_verified": True, "identity_verified": True, "rights_confirmed": True}).status_code == 409


def test_override_validation(backend):
    app, client = backend
    job = client.post("/v1/jobs", json={"character": "A", "auto_start": False}).json()
    url = f'/v1/jobs/{job["id"]}/stages'
    assert client.put(url + "/quality", json={"data": {}}).status_code == 422
    assert client.put(url + "/captions", json={"data": {"cues": [{"start": 2, "end": 1, "text": "x"}]}}).status_code == 422
    assert client.put(url + "/captions", json={"data": {"cues": [{"start": 0, "end": 2, "text": "x"}, {"start": 1, "end": 3, "text": "y"}]}}).status_code == 422


def test_minimal_invalidation_and_locked_edits(backend, plan, media_assets):
    app, client = backend
    job = create_manual(client, plan, media_assets)
    key = job["id"]
    response = client.patch(f"/v1/jobs/{key}", json={"preset": {"width": 180, "height": 320, "accent": "#FFFFFF"}})
    edited = response.json()
    assert edited["steps"]["speech"]["status"] == "completed"
    assert edited["steps"]["captions"]["status"] == "completed"
    assert edited["steps"]["timeline"]["status"] == "pending"
    updated_plan = plan | {"title": "Thay đổi"}
    edited = client.put(f"/v1/jobs/{key}/stages/plan", json={"data": updated_plan}).json()
    assert edited["steps"]["speech"]["status"] == "stale"
    assert edited["steps"]["speech"]["output"]["asset_id"] == media_assets[1]["id"]
    assert client.post(f"/v1/jobs/{key}/stages/speech/unlock").json()["steps"]["speech"]["output"] is None


def test_asset_meta_edit_invalidates_approval(backend, plan, media_assets):
    app, client = backend
    job = create_manual(client, plan, media_assets)
    key = job["id"]
    result = client.patch(f'/v1/assets/{media_assets[0]["id"]}', json={"rights": "owned", "identity_verified": True})
    assert result.status_code == 200
    assert client.get(f"/v1/jobs/{key}").json()["approval"] is None
    client.post(f"/v1/jobs/{key}/run")
    assert client.patch(f'/v1/assets/{media_assets[0]["id"]}', json={"rights": "licensed"}).status_code == 409

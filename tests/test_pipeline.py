import time

import pytest

from conftest import create_manual, execute, wav_bytes
from shorts.config import Settings
from shorts.errors import NeedsInput, ProviderError
from shorts.models import JobCreate
from shorts.pipeline import Pipeline, Worker
from shorts.store import ProcessLock, Store


def test_real_manual_render_review_and_export(backend, plan, media_assets):
    app, client = backend
    job = create_manual(client, plan, media_assets)
    key = job["id"]
    job = execute(app, client, key)
    assert job["status"] == "needs_review", job["error"]
    assert job["steps"]["quality"]["output"]["technical_pass"]
    preview = client.get(f"/v1/jobs/{key}/files/preview.mp4")
    assert preview.status_code == 200 and len(preview.content) > 1000
    assert client.get(f"/v1/jobs/{key}/files/final.mp4").status_code == 404
    # Range requests support scrubbing in a future frontend.
    assert client.get(f"/v1/jobs/{key}/files/preview.mp4", headers={"Range": "bytes=0-99"}).status_code == 206
    assert client.post(f"/v1/jobs/{key}/approve", json={"content_verified": True, "identity_verified": True, "rights_confirmed": False}).status_code == 422
    result = client.post(f"/v1/jobs/{key}/approve", json={"content_verified": True, "identity_verified": True, "rights_confirmed": True})
    assert result.status_code == 202
    app.state.store.claim()
    app.state.pipeline.execute(key)
    job = app.state.store.get(key)
    assert job["status"] == "completed", job["error"]
    assert client.get(f"/v1/jobs/{key}/files/final.mp4").status_code == 200
    assert "tiếng Việt" in client.get(f"/v1/jobs/{key}/files/subtitles.srt").text
    before = {name: step["attempts"] for name, step in job["steps"].items()}
    rerun = execute(app, client, key)
    assert {name: step["attempts"] for name, step in rerun["steps"].items()} == before
    # Editing captions revokes approval and removes access to the obsolete final.
    client.put(f"/v1/jobs/{key}/stages/captions", json={"data": {"cues": [{"start": 0, "end": 1.5, "text": "Đã sửa"}]}})
    assert client.get(f"/v1/jobs/{key}/files/final.mp4").status_code == 404
    assert app.state.store.get(key)["approval"] is None


def test_auto_orchestration_with_local_provider_boundary(backend, plan, media_assets, monkeypatch):
    app, client = backend
    image = media_assets[0]
    client.patch(f'/v1/assets/{image["id"]}', json={"character": plan["character"], "work": plan["work"], "rights": "owned", "identity_verified": True})
    engine = app.state.pipeline
    monkeypatch.setattr(engine.providers, "research", lambda config, cached: dict(character=plan["character"], work=plan["work"], sources=[dict(id="s1", title="Test evidence", text="Đây là bằng chứng kiểm thử.")], warnings=[]))
    monkeypatch.setattr(engine.providers, "plan", lambda *args: plan)
    calls = []
    def fake_inference(job, operation, payload):
        calls.append(operation)
        folder = __import__("pathlib").Path(payload["output"])
        for i in range(len(payload["texts"])):
            (folder / f"{i:03}.wav").write_bytes(wav_bytes())
    monkeypatch.setattr(engine, "inference", fake_inference)
    job = client.post("/v1/jobs", json={"character": plan["character"], "work": plan["work"], "auto_start": False,
        "precise_captions": False, "mode": "auto", "target_seconds": 5,
        "preset": {"width": 180, "height": 320, "fps": 15, "particles": False}}).json()
    job = execute(app, client, job["id"])
    assert job["status"] == "needs_review", job["error"]
    assert calls == ["speech"]
    assert job["steps"]["captions"]["output"]["method"] == "tts_sections"


def test_missing_local_service_is_actionable(backend):
    app, client = backend
    job = client.post("/v1/jobs", json={"character": "A", "auto_start": False}).json()
    job = execute(app, client, job["id"])
    assert job["status"] == "needs_input"
    assert "sources" in job["error"].lower() or "search" in job["error"].lower()


def test_retry_is_bounded_and_manual_mode_never_searches(backend, monkeypatch):
    app, client = backend
    calls = []
    def fail(*args):
        calls.append(1)
        raise ProviderError("temporary")
    monkeypatch.setattr(app.state.pipeline.providers, "research", fail)
    job = client.post("/v1/jobs", json={"character": "A", "auto_start": False}).json()
    failed = execute(app, client, job["id"])
    assert failed["status"] == "failed"
    assert len(calls) == app.state.pipeline.settings.max_stage_attempts
    manual = client.post("/v1/jobs", json={"character": "B", "mode": "manual", "auto_start": False}).json()
    result = execute(app, client, manual["id"])
    assert result["status"] == "needs_input"
    assert len(calls) == 2


def test_cancel_and_restart_recovery(backend):
    app, client = backend
    job = client.post("/v1/jobs", json={"character": "A"}).json()
    key = job["id"]
    app.state.store.claim()
    client.post(f"/v1/jobs/{key}/cancel")
    app.state.pipeline.execute(key)
    assert app.state.store.get(key)["status"] == "cancelled"
    client.post(f"/v1/jobs/{key}/run")
    app.state.store.claim()
    app.state.store.recover()
    assert app.state.store.get(key)["status"] == "paused"


def test_worker_persistence_and_single_process_lock(tmp_path):
    settings = Settings(data_dir=tmp_path, search_enabled=False)
    store = Store(tmp_path)
    job = store.create(JobCreate(character="A").model_dump())
    store.enqueue(job["id"])
    worker = Worker(Pipeline(settings, store))
    worker.start()
    try:
        second = ProcessLock(tmp_path)
        with pytest.raises(RuntimeError):
            second.acquire()
        deadline = time.monotonic() + 10
        while store.get(job["id"])["status"] in ("queued", "running") and time.monotonic() < deadline:
            time.sleep(0.05)
        assert store.get(job["id"])["status"] == "needs_input"
    finally:
        worker.close()
    reopened = Store(tmp_path)
    assert reopened.get(job["id"])["status"] == "needs_input"


def test_stale_manual_output_blocks_instead_of_overwrite(backend, plan, media_assets):
    app, client = backend
    job = create_manual(client, plan, media_assets)
    client.put(f'/v1/jobs/{job["id"]}/stages/plan', json={"data": plan | {"title": "New title"}})
    job = execute(app, client, job["id"])
    assert job["status"] == "needs_input"
    assert "preserved" in job["error"]
    again = execute(app, client, job["id"])
    assert again["status"] == "needs_input"
    assert again["steps"]["assets"]["status"] == "stale"
    assert again["steps"]["assets"]["output"]["selected"][0]["asset_id"] == media_assets[0]["id"]


def test_multiple_scenes_music_and_alpha_overlay(backend, plan, media_assets):
    from PIL import Image, ImageDraw
    app, client = backend
    engine = app.state.pipeline
    image, audio = media_assets
    directory = app.state.store.root
    layer = Image.new("RGBA", (180, 320))
    ImageDraw.Draw(layer).rectangle((5, 5, 174, 314), outline=(255, 220, 50, 120), width=3)
    layer.save(directory / "overlay.png")
    engine.media.run(["-loop", "1", "-i", str(directory / "overlay.png"), "-t", "0.5", "-r", "15", "-c:v", "qtrle", str(directory / "overlay.mov")], directory)
    response = client.post("/v1/assets?kind=overlay", files={"file": ("overlay.mov", (directory / "overlay.mov").read_bytes(), "video/quicktime")})
    assert response.status_code == 201, response.text
    overlay = response.json()
    two_sections = plan | {"sections": plan["sections"] * 2}
    job = create_manual(client, two_sections, media_assets)
    key = job["id"]
    assert client.put(f"/v1/jobs/{key}/stages/assets", json={"data": {
        "selected": [{"asset_id": image["id"], "section": 0}, {"asset_id": image["id"], "section": 1}],
        "music_id": audio["id"], "overlay_id": overlay["id"]}}).status_code == 200
    assert client.put(f"/v1/jobs/{key}/stages/speech", json={"data": {
        "asset_id": audio["id"], "duration": 2, "parts": [{"section": 0, "start": 0, "end": 1}, {"section": 1, "start": 1, "end": 2}]}}).status_code == 200
    assert client.put(f"/v1/jobs/{key}/stages/captions", json={"data": {"cues": [{"start": 0, "end": 1.9, "text": "Nhạc và lớp phủ alpha"}]}}).status_code == 200
    job = execute(app, client, key)
    assert job["status"] == "needs_review", job["error"]
    assert len(job["steps"]["timeline"]["output"]["scenes"]) == 2
    assert job["steps"]["quality"]["output"]["technical_pass"]

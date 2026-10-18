import io
import math
import struct
import wave

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from shorts.app import create_app
from shorts.config import Settings


def wav_bytes(seconds=2):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"".join(struct.pack("<h", int(3000 * math.sin(2 * math.pi * 220 * i / 16000))) for i in range(round(seconds * 16000))))
    return output.getvalue()


@pytest.fixture
def backend(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, search_enabled=False), start_worker=False)
    with TestClient(app) as client:
        yield app, client


@pytest.fixture
def plan():
    return dict(character="Nhân vật thử", work="Truyện thử", title="Một khởi đầu", angle="Xuất thân",
                sections=[dict(text="Đây là nội dung kiểm thử tiếng Việt.", source_ids=[], image_query="")],
                pronunciation={}, warnings=[])


@pytest.fixture
def media_assets(backend):
    app, client = backend
    if not app.state.pipeline.settings.binary("ffmpeg"):
        pytest.skip("FFmpeg not installed")
    image = io.BytesIO()
    Image.new("RGB", (360, 640), "#213D54").save(image, "PNG")
    img = client.post("/v1/assets", files={"file": ("test.png", image.getvalue(), "image/png")})
    assert img.status_code == 201, img.text
    audio = client.post("/v1/assets?kind=audio", files={"file": ("test.wav", wav_bytes(), "audio/wav")})
    assert audio.status_code == 201, audio.text
    return img.json(), audio.json()


def create_manual(client, plan, assets, **extra):
    image, audio = assets
    response = client.post("/v1/jobs", json=dict(character=plan["character"], work=plan["work"], auto_start=False,
        mode="manual", target_seconds=5, preset=dict(width=180, height=320, fps=15, particles=True), **extra))
    assert response.status_code == 201
    job = response.json()
    for stage, data in {
        "plan": plan,
        "assets": {"selected": [{"asset_id": image["id"]}]},
        "speech": {"asset_id": audio["id"], "duration": 99, "parts": []},
        "captions": {"cues": [{"start": 0.05, "end": 1.95, "text": "Đây là kiểm thử tiếng Việt."}]},
    }.items():
        result = client.put(f'/v1/jobs/{job["id"]}/stages/{stage}', json={"data": data})
        assert result.status_code == 200, result.text
    return client.get(f'/v1/jobs/{job["id"]}').json()


def execute(app, client, job_id):
    response = client.post(f"/v1/jobs/{job_id}/run")
    assert response.status_code == 202
    claimed = app.state.store.claim()
    assert claimed["id"] == job_id
    app.state.pipeline.execute(job_id)
    return app.state.store.get(job_id)

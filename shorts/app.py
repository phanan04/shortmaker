from __future__ import annotations

import importlib.util
import secrets
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

import httpx
from fastapi import Body, Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBearer
from pydantic import ValidationError

from . import __version__
from .config import Settings, load_settings
from .errors import NeedsInput
from .models import Approval, AssetMeta, JobCreate, Override
from .pipeline import CONFIG_KEYS, Pipeline, Worker
from .store import ACTIVE, STAGES, Store, invalidate, now


def create_app(settings: Settings | None = None, start_worker=True):
    settings = settings or load_settings()
    settings.data_dir = settings.data_dir.resolve()
    store = Store(settings.data_dir)
    pipeline = Pipeline(settings, store)
    worker = Worker(pipeline)

    @asynccontextmanager
    async def lifespan(app):
        if start_worker:
            worker.start()
        try:
            yield
        finally:
            if start_worker:
                worker.close()

    bearer = HTTPBearer(auto_error=False)

    def authenticate(credentials=Depends(bearer)):
        if settings.api_token and (not credentials or not secrets.compare_digest(credentials.credentials, settings.api_token)):
            raise HTTPException(401, "A valid Bearer token is required", headers={"WWW-Authenticate": "Bearer"})

    app = FastAPI(title="Tu Tiên Shorts Backend", version=__version__, lifespan=lifespan,
                  description="Local-only AI, persistent sequential pipeline, manual edits and review before export.")
    app.state.store, app.state.pipeline, app.state.worker = store, pipeline, worker

    @app.middleware("http")
    async def local_browser_guard(request: Request, call_next):
        host = request.headers.get("host", "").split(":")[0].strip("[]")
        if settings.host in ("127.0.0.1", "localhost", "::1") and host not in ("127.0.0.1", "localhost", "testserver", ""):
            return JSONResponse({"detail": "Unrecognized host"}, status_code=403)
        origin = request.headers.get("origin")
        if origin:
            parsed = urlparse(origin)
            if parsed.scheme not in ("http", "https") or parsed.netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Cross-origin browser requests are disabled"}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "Cross-site browser requests are disabled"}, status_code=403)
        length = request.headers.get("content-length")
        if length and int(length) > (settings.max_upload_mb + 1) * 1024 * 1024:
            return JSONResponse({"detail": "Request too large"}, status_code=413)
        return await call_next(request)

    @app.exception_handler(KeyError)
    async def not_found(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(NeedsInput)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    auth = [Depends(authenticate)]

    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__, "paid_apis": False,
                "worker_alive": bool(worker.thread and worker.thread.is_alive())}

    @app.get("/v1/capabilities", dependencies=auth)
    def capabilities():
        ollama = {"available": False, "models": []}
        try:
            with httpx.Client(timeout=2, trust_env=False) as client:
                response = client.get(settings.ollama_url.rstrip("/") + "/api/tags")
                response.raise_for_status()
                ollama = {"available": True, "models": [m["name"] for m in response.json().get("models", [])]}
        except (httpx.HTTPError, ValueError, KeyError):
            pass
        return dict(ffmpeg=settings.binary("ffmpeg"), ffprobe=settings.binary("ffprobe"),
                    ollama=ollama | {"configured_model": settings.ollama_model},
                    vieneu_installed=importlib.util.find_spec("vieneu") is not None,
                    whisper_installed=importlib.util.find_spec("faster_whisper") is not None,
                    tts_server=settings.tts_url or None, downloads_allowed=settings.allow_model_downloads,
                    model_cache=str(store.root / "models"), concurrency=1, paid_apis=False)

    @app.post("/v1/jobs", status_code=201, dependencies=auth)
    def create_job(body: JobCreate):
        job = store.create(body.model_dump())
        if body.auto_start:
            job = store.enqueue(job["id"])
            worker.wake.set()
        return job

    @app.post("/v1/jobs/batch", status_code=201, dependencies=auth)
    def create_batch(body: Annotated[list[JobCreate], Body(min_length=1, max_length=50)]):
        # Validation completes for the whole batch before the first mutation.
        return [create_job(item) for item in body]

    @app.get("/v1/jobs", dependencies=auth)
    def list_jobs(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        return [dict(id=j["id"], status=j["status"], character=j["config"]["character"],
                     current_stage=j["current_stage"], error=j["error"], created_at=j["created_at"],
                     updated_at=j["updated_at"], revision=j["revision"],
                     progress={k: v["status"] for k, v in j["steps"].items()}) for j in store.jobs(limit, offset)]

    @app.get("/v1/jobs/{job_id}", dependencies=auth)
    def get_job(job_id: str):
        return store.get(job_id)

    @app.patch("/v1/jobs/{job_id}", dependencies=auth)
    def edit_job(job_id: str, body: dict = Body(...)):
        def change(job):
            store.editable(job)
            updated = JobCreate.model_validate(job["config"] | body).model_dump()
            changed = {key for key in updated if updated[key] != job["config"].get(key)}
            if not changed:
                return
            for name in STAGES:
                if changed.intersection(CONFIG_KEYS[name]):
                    invalidate(job, name)
            job["config"] = updated
            job.update(status="draft", approval=None, error=None, control=None)
        return store.modify(job_id, change, "config_updated")

    @app.post("/v1/jobs/{job_id}/run", status_code=202, dependencies=auth)
    def run_job(job_id: str):
        job = store.enqueue(job_id)
        worker.wake.set()
        return job

    def control(job_id, action):
        def change(job):
            if job["status"] == "running":
                job["control"] = action
            elif job["status"] != "completed":
                job.update(status="cancelled" if action == "cancel" else "paused", control=None)
            else:
                raise NeedsInput("Completed jobs cannot be paused or cancelled; edit or rerun instead")
        return store.modify(job_id, change, action + "_requested")

    @app.post("/v1/jobs/{job_id}/pause", dependencies=auth)
    def pause(job_id: str):
        return control(job_id, "pause")

    @app.post("/v1/jobs/{job_id}/cancel", dependencies=auth)
    def cancel(job_id: str):
        return control(job_id, "cancel")

    @app.put("/v1/jobs/{job_id}/stages/{stage}", dependencies=auth)
    def override_stage(job_id: str, stage: str, body: Override):
        return pipeline.override(job_id, stage, body.data, body.locked)

    @app.post("/v1/jobs/{job_id}/stages/{stage}/unlock", dependencies=auth)
    def unlock(job_id: str, stage: str):
        if stage not in STAGES:
            raise HTTPException(404, "Unknown stage")
        def change(job):
            store.editable(job)
            job["steps"][stage].update(locked=False, origin="automatic")
            invalidate(job, stage)
        return store.modify(job_id, change, "stage_unlocked")

    @app.post("/v1/jobs/{job_id}/approve", status_code=202, dependencies=auth)
    def approve(job_id: str, body: Approval):
        if not all((body.content_verified, body.identity_verified, body.rights_confirmed)):
            raise HTTPException(422, "Confirm content, character identity and asset rights before export")
        def change(job):
            store.editable(job)
            if job["steps"]["quality"]["status"] != "completed" or not job["steps"]["quality"]["output"]["technical_pass"]:
                raise NeedsInput("A current preview and successful technical checks are required")
            job.update(approval=body.model_dump() | {"at": now()}, status="queued", error=None, control=None, queued_at=now())
        job = store.modify(job_id, change, "approved")
        worker.wake.set()
        return job

    @app.get("/v1/jobs/{job_id}/events", dependencies=auth)
    def events(job_id: str, after: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500)):
        return store.events(job_id, after, limit)

    @app.get("/v1/jobs/{job_id}/files/{filename}", dependencies=auth)
    def job_file(job_id: str, filename: str):
        job = store.get(job_id)
        allowed = {"preview.mp4": "preview", "final.mp4": "export", "subtitles.srt": "export", "metadata.json": "export"}
        if filename not in allowed or job["steps"][allowed[filename]]["status"] != "completed":
            raise HTTPException(404, "File is not available for the current job revision")
        # job_id comes from an existing DB record; filenames are an exact allowlist.
        path = store.root / "jobs" / job["id"] / filename
        if not path.is_file():
            raise HTTPException(404, "Output file missing; rerun the job")
        return FileResponse(path, filename=filename)

    @app.post("/v1/assets", status_code=201, dependencies=auth)
    def upload_asset(file: UploadFile = File(...), kind: str = Query("image", pattern="^(image|audio|overlay)$")):
        suffix = Path(file.filename or "upload").suffix[:12]
        with tempfile.NamedTemporaryFile(dir=store.root, suffix=suffix, delete=False) as temp:
            path = Path(temp.name)
            try:
                total = 0
                while chunk := file.file.read(1024 * 1024):
                    total += len(chunk)
                    if total > settings.max_upload_mb * 1024 * 1024:
                        raise HTTPException(413, "Upload exceeds configured limit")
                    temp.write(chunk)
                temp.close()
                return pipeline.media.import_file(path, kind, AssetMeta().model_dump())
            finally:
                temp.close()
                path.unlink(missing_ok=True)
                file.file.close()

    @app.get("/v1/assets", dependencies=auth)
    def assets(character: str | None = Query(None, max_length=200)):
        return store.assets(character)

    @app.get("/v1/assets/{asset_id}", dependencies=auth)
    def asset(asset_id: str):
        return store.asset(asset_id)

    @app.patch("/v1/assets/{asset_id}", dependencies=auth)
    def edit_asset(asset_id: str, body: dict = Body(...)):
        with store.lock:
            item = store.asset(asset_id)
            meta = AssetMeta.model_validate({key: item.get(key) for key in AssetMeta.model_fields} | body).model_dump()
            affected = [job for job in store.jobs(limit=1000000) if asset_id in __import__("json").dumps(job["steps"])]
            if any(job["status"] in ACTIVE for job in affected):
                raise NeedsInput("Pause affected jobs before changing asset metadata")
            for job in affected:
                def change(record):
                    invalidate(record, "quality")
                store.modify(job["id"], change, "asset_metadata_changed")
            return store.put_asset(item | meta)

    @app.get("/v1/assets/{asset_id}/file", dependencies=auth)
    def asset_file(asset_id: str):
        return FileResponse(store.asset_path(asset_id))

    return app

from __future__ import annotations

import copy
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .errors import NeedsInput

STAGES = ("research", "plan", "assets", "speech", "captions", "timeline", "preview", "quality", "export")
DEPS = {
    "research": (), "plan": ("research",), "assets": ("plan",), "speech": ("plan",),
    "captions": ("plan", "speech"), "timeline": ("plan", "assets", "speech", "captions"),
    "preview": ("timeline",), "quality": ("preview",), "export": ("quality",),
}
ACTIVE = {"queued", "running"}


def now():
    return datetime.now(timezone.utc).isoformat()


def descendants(stage):
    result = {stage}
    for name in STAGES:
        if any(dep in result for dep in DEPS[name]):
            result.add(name)
    return result


def invalidate(job, stage, include=True):
    for name in descendants(stage) - (set() if include else {stage}):
        step = job["steps"][name]
        if step["locked"] and step.get("output") is not None:
            step["status"] = "stale"
        else:
            step.update(status="pending", output=None, fingerprint=None, error=None)
    job.update(approval=None, status="draft", current_stage=None, error=None, control=None)


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "assets").mkdir(exist_ok=True)
        (self.root / "jobs").mkdir(exist_ok=True)
        (self.root / "models").mkdir(exist_ok=True)
        self.path = self.root / "pipeline.sqlite3"
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL, at TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS event_job ON events(job_id, id);
            """)

    @contextmanager
    def connect(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=30)
            try:
                db.execute("PRAGMA busy_timeout=30000")
                db.execute("BEGIN IMMEDIATE")
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    @staticmethod
    def event_db(db, job_id, kind, payload):
        db.execute("INSERT INTO events(job_id,at,kind,payload) VALUES(?,?,?,?)",
                   (job_id, now(), kind, json.dumps(payload, ensure_ascii=False)))

    def create(self, config):
        job_id = uuid.uuid4().hex
        job = dict(id=job_id, config=config, created_at=now(), updated_at=now(), status="draft",
                   current_stage=None, error=None, approval=None, control=None, revision=1,
                   steps={name: dict(status="pending", locked=False, attempts=0,
                                     output=None, fingerprint=None, error=None) for name in STAGES})
        (self.root / "jobs" / job_id).mkdir()
        with self.connect() as db:
            db.execute("INSERT INTO jobs VALUES(?,?)", (job_id, json.dumps(job, ensure_ascii=False)))
            self.event_db(db, job_id, "created", {})
        return job

    def get(self, job_id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError("Job not found")
        return json.loads(row[0])

    def jobs(self, limit=50, offset=0):
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM jobs ORDER BY rowid DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def modify(self, job_id, fn, kind="updated"):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError("Job not found")
            job = json.loads(row[0])
            fn(job)
            job["updated_at"] = now()
            job["revision"] += 1
            db.execute("UPDATE jobs SET payload=? WHERE id=?", (json.dumps(job, ensure_ascii=False), job_id))
            self.event_db(db, job_id, kind, {"status": job["status"], "stage": job["current_stage"], "revision": job["revision"]})
        return job

    def editable(self, job):
        if job["status"] in ACTIVE:
            raise NeedsInput("Pause the job and wait for status=paused before editing")

    def enqueue(self, job_id):
        def change(job):
            if job["status"] in ACTIVE:
                return
            job.update(status="queued", control=None, error=None, queued_at=now())
        return self.modify(job_id, change, "queued")

    def claim(self):
        with self.connect() as db:
            rows = db.execute("SELECT id,payload FROM jobs ORDER BY rowid").fetchall()
            queued = [(key, json.loads(value)) for key, value in rows if json.loads(value)["status"] == "queued"]
            if not queued:
                return None
            key, job = min(queued, key=lambda pair: pair[1].get("queued_at", pair[1]["created_at"]))
            job.update(status="running", control=None, updated_at=now())
            db.execute("UPDATE jobs SET payload=? WHERE id=?", (json.dumps(job, ensure_ascii=False), key))
            self.event_db(db, key, "started", {})
        return job

    def recover(self):
        # Only call after acquiring the process lock: no active worker may be reset.
        for job in self.jobs(limit=1_000_000):
            if job["status"] == "running":
                def change(item):
                    item.update(status="paused", control=None, error="Backend restarted; resume to continue from checkpoints")
                    for step in item["steps"].values():
                        if step["status"] == "running":
                            step["status"] = "pending"
                self.modify(job["id"], change, "recovered")

    def events(self, job_id, after=0, limit=200):
        self.get(job_id)
        with self.connect() as db:
            rows = db.execute("SELECT id,at,kind,payload FROM events WHERE job_id=? AND id>? ORDER BY id LIMIT ?",
                              (job_id, after, limit)).fetchall()
        return [dict(id=r[0], at=r[1], kind=r[2], data=json.loads(r[3])) for r in rows]

    def put_asset(self, asset):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO assets VALUES(?,?)", (asset["id"], json.dumps(asset, ensure_ascii=False)))
        return asset

    def asset(self, key):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM assets WHERE id=?", (key,)).fetchone()
        if not row:
            raise KeyError("Asset not found")
        return json.loads(row[0])

    def assets(self, character=None):
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM assets ORDER BY rowid DESC").fetchall()
        items = [json.loads(row[0]) for row in rows]
        return [a for a in items if not character or a.get("character", "").casefold() == character.casefold()]

    def asset_path(self, key):
        asset = self.asset(key)
        path = (self.root / "assets" / asset["filename"]).resolve()
        if path.parent != self.root / "assets" or not path.is_file():
            raise KeyError("Asset file missing")
        return path

    def previous_angles(self, character):
        result = []
        for job in self.jobs(limit=10000):
            plan = job["steps"]["plan"].get("output")
            if plan and plan["character"].casefold() == character.casefold():
                result.append(plan["angle"])
        return result[-50:]


class ProcessLock:
    """One backend process per data directory (works on Windows and POSIX)."""
    def __init__(self, root):
        self.path = Path(root) / "backend.lock"
        self.file = None

    def acquire(self):
        self.file = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if __import__("os").name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            self.file = None
            raise RuntimeError("Another backend is already using this data directory; use one Uvicorn worker") from None

    def release(self):
        if self.file:
            self.file.close()
            self.file = None

from __future__ import annotations

import difflib
import hashlib
import importlib.util
import json
import re
import sys
import threading
import time
import uuid
from pathlib import Path

import httpx

from .errors import Interrupted, NeedsInput, ProviderError
from .media import Media, run_process, split_cues, write_srt
from .models import AssetMeta, Assets, Captions, Plan, Research, Speech
from .net import fetch
from .providers import Providers, normalized
from .store import DEPS, STAGES, ProcessLock, invalidate, now

EDITABLE_MODELS = {"research": Research, "plan": Plan, "assets": Assets, "speech": Speech, "captions": Captions}
CONFIG_KEYS = {
    "research": ("character", "work", "sources"), "plan": ("angle", "period", "target_seconds"),
    "assets": (), "speech": ("voice",), "captions": ("precise_captions",),
    "timeline": ("preset",), "preview": (), "quality": ("target_seconds",), "export": ("require_review",),
}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class Pipeline:
    def __init__(self, settings, store, providers=None):
        self.settings = settings
        self.store = store
        self.providers = providers or Providers(settings)
        self.media = Media(settings, store)
        self.stop = threading.Event()

    def check(self, job_id):
        job = self.store.get(job_id)
        if self.stop.is_set() or job.get("control") in ("pause", "cancel"):
            raise Interrupted()

    def override(self, job_id, name, data, locked=True):
        if name not in EDITABLE_MODELS:
            raise ValueError("Only research, plan, assets, speech and captions support manual overrides")
        output = EDITABLE_MODELS[name].model_validate(data).model_dump()
        if name == "assets":
            for selected in output["selected"]:
                if self.store.asset(selected["asset_id"])["kind"] != "image":
                    raise ValueError("Selected visual must be an image")
            for field, kind in (("music_id", "audio"), ("overlay_id", "overlay")):
                if output[field] and self.store.asset(output[field])["kind"] != kind:
                    raise ValueError(f"Invalid {field} type")
        if name == "speech":
            asset = self.store.asset(output["asset_id"])
            if asset["kind"] != "audio":
                raise ValueError("Speech requires an audio asset")
            output["duration"] = self.media.probe(self.store.asset_path(asset["id"]))["duration"]
            previous_end = 0
            for part in output["parts"]:
                if part["end"] <= part["start"] or part["start"] < previous_end or part["end"] > output["duration"] + 0.05:
                    raise ValueError("Invalid speech section timing")
                previous_end = part["end"]

        def change(job):
            self.store.editable(job)
            invalidate(job, name, include=False)
            if name == "plan" and job["steps"]["research"]["output"] is None:
                # Manual scripts are user input, not fabricated web research.
                research = Research(character=output["character"], work=output["work"],
                                    sources=[dict(id="user_script", title="Kịch bản người dùng cung cấp",
                                                  text=" ".join(s["text"] for s in output["sections"]))],
                                    warnings=["Chưa tra cứu web: nội dung do người dùng cung cấp."]).model_dump()
                job["steps"]["research"].update(status="completed", output=research, locked=True, error=None)
            job["steps"][name].update(status="completed", output=output, locked=locked,
                                      error=None, fingerprint=None, origin="manual")
            job["steps"][name]["fingerprint"] = self.dependency_hash(job, name)
        return self.store.modify(job_id, change, f"override:{name}")

    def dependency_hash(self, job, name):
        relevant = {key: job["config"][key] for key in CONFIG_KEYS[name]}
        providers = {"model": self.settings.ollama_model} if name in ("research", "plan") else {}
        if name == "speech":
            providers = {"voice": self.settings.tts_voice, "model": self.settings.tts_model}
        return fingerprint(dict(config=relevant, providers=providers,
                                dependencies={dep: job["steps"][dep]["output"] for dep in DEPS[name]}))

    def execute(self, job_id):
        stage = None
        try:
            for stage in STAGES:
                self.check(job_id)
                job = self.store.get(job_id)
                step = job["steps"][stage]
                if step["status"] == "stale":
                    raise NeedsInput(f"Manual {stage} is preserved but outdated; resubmit it or unlock it")
                stamp = self.dependency_hash(job, stage)
                cached = step["status"] == "completed" and step.get("output") is not None
                if cached and stage in ("preview", "export"):
                    cached = (self.store.root / "jobs" / job_id / step["output"]["filename"]).is_file()
                if cached and stage == "speech":
                    try:
                        self.store.asset_path(step["output"]["asset_id"])
                    except KeyError:
                        cached = False
                if cached and (step["locked"] or step["fingerprint"] == stamp):
                    continue
                if stage == "export":
                    quality = job["steps"]["quality"]["output"]
                    if (job["config"]["require_review"] or quality["review_reasons"]) and not job.get("approval"):
                        self.store.modify(job_id, lambda j: j.update(status="needs_review", current_stage="export",
                                                                   error="Review preview, sources and image rights, then approve"), "review_required")
                        return
                for attempt in range(self.settings.max_stage_attempts):
                    self.check(job_id)
                    def started(item):
                        item.update(current_stage=stage, error=None)
                        item["steps"][stage].update(status="running", error=None, started_at=now())
                        item["steps"][stage]["attempts"] += 1
                    self.store.modify(job_id, started, "stage_started")
                    try:
                        output = self.dispatch(stage, self.store.get(job_id))
                        self.check(job_id)
                        break
                    except ProviderError:
                        if attempt + 1 == self.settings.max_stage_attempts:
                            raise
                        for _ in range(10):
                            self.check(job_id)
                            time.sleep(0.1)
                def completed(item):
                    item["steps"][stage].update(status="completed", output=output, fingerprint=stamp,
                                                error=None, completed_at=now(), origin="automatic")
                self.store.modify(job_id, completed, "stage_completed")
                directory = self.store.root / "jobs" / job_id
                tmp = directory / f"{stage}.tmp.json"
                tmp.write_text(json.dumps(output, ensure_ascii=False, indent=2), "utf-8")
                tmp.replace(directory / f"{stage}.json")
            self.store.modify(job_id, lambda j: j.update(status="completed", current_stage=None, error=None), "completed")
        except Interrupted:
            def interrupted(job):
                status = "cancelled" if job.get("control") == "cancel" else "paused"
                job.update(status=status, control=None, error=None)
                if stage and job["steps"][stage]["status"] == "running":
                    job["steps"][stage]["status"] = "pending"
            self.store.modify(job_id, interrupted, "interrupted")
        except Exception as exc:
            def failed(job):
                job.update(status="needs_input" if isinstance(exc, NeedsInput) else "failed",
                           current_stage=stage, error=str(exc)[:3000])
                if stage:
                    # Preserve stale locks across repeated retries; never regenerate a manual edit silently.
                    step = job["steps"][stage]
                    if step["status"] != "stale":
                        step["status"] = "needs_input" if isinstance(exc, NeedsInput) else "failed"
                    step["error"] = str(exc)[:3000]
            self.store.modify(job_id, failed, "stage_failed")

    def dispatch(self, stage, job):
        if job["config"]["mode"] == "manual" and stage in EDITABLE_MODELS:
            raise NeedsInput(f"Manual mode: provide a {stage} override")
        return getattr(self, "do_" + stage)(job)

    def do_research(self, job):
        cached = None
        matches = []
        for previous in self.store.jobs(limit=1000):
            if previous["id"] == job["id"]:
                continue
            output = previous["steps"]["research"]["output"] if previous["steps"]["research"]["status"] == "completed" else None
            names = [output["character"], *output.get("aliases", [])] if output else []
            if output and normalized(job["config"]["character"]) in {normalized(n) for n in names}:
                if not job["config"]["work"] or normalized(output["work"]) == normalized(job["config"]["work"]):
                    matches.append(output)
        if matches:
            if not job["config"]["work"] and len({normalized(m["work"]) for m in matches}) > 1:
                raise NeedsInput("Multiple works match this character in the library; specify the work")
            cached = matches[0]
        return self.providers.research(job["config"], cached)

    def do_plan(self, job):
        return self.providers.plan(job["config"], job["steps"]["research"]["output"],
                                   self.store.previous_angles(job["config"]["character"]))

    def do_assets(self, job):
        plan = job["steps"]["plan"]["output"]
        library = [a for a in self.store.assets(plan["character"]) if a["kind"] == "image" and
                   normalized(a.get("work", "")) == normalized(plan["work"]) and a["identity_verified"]]
        library.sort(key=lambda a: a["rights"] == "unknown")
        if library:
            return Assets(selected=[dict(asset_id=library[0]["id"])]).model_dump()
        results = self.providers.search(f'{plan["character"]} {plan["work"]} character', images=True)
        candidates = []
        directory = self.store.root / "jobs" / job["id"]
        for result in results:
            self.check(job["id"])
            url = result.get("image")
            if not url:
                continue
            try:
                data, content_type, final = fetch(url, max_bytes=12_000_000)
                if not content_type.startswith("image/"):
                    continue
                temporary = directory / "download.image"
                temporary.write_bytes(data)
                asset = self.media.import_file(temporary, "image", AssetMeta(
                    label=result.get("title", "")[:200], character=plan["character"], work=plan["work"],
                    source_url=result.get("url") or final).model_dump())
                candidates.append(asset["id"])
            except (NeedsInput, ProviderError, ValueError, OSError):
                continue
            if len(candidates) >= 3:
                break
        (directory / "image-candidates.json").write_text(json.dumps(candidates), "utf-8")
        if not candidates:
            raise NeedsInput("No suitable images found; upload an image and override assets")
        return Assets(selected=[dict(asset_id=candidates[0])],
                      warnings=["Ảnh tìm tự động chưa xác nhận đúng nhân vật hoặc quyền sử dụng; cần duyệt."]).model_dump()

    def inference(self, job, operation, payload):
        module = "vieneu" if operation == "speech" else "faster_whisper"
        if importlib.util.find_spec(module) is None:
            raise NeedsInput(f"Missing local {module}; install the optional dependency or supply {operation} manually")
        directory = self.store.root / "jobs" / job["id"]
        request = directory / f"inference-{operation}.json"
        request.write_text(json.dumps(payload | {"cache": str(self.store.root / "models"),
                                                "downloads": self.settings.allow_model_downloads}, ensure_ascii=False), "utf-8")
        # Keep a fully-qualified project path when running from the per-job directory.
        import os
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
        try:
            run_process([sys.executable, "-m", "shorts.local_ai", operation, str(request)], directory,
                        lambda: self.check(job["id"]), self.settings.render_timeout, f"{operation}.log", env)
        except RuntimeError as exc:
            raise NeedsInput(f"Local {operation} inference failed; check cached models/dependencies and {operation}.log. "
                             "Set allow_model_downloads=true to permit first-use downloads, or use a manual override.") from exc

    def do_speech(self, job):
        directory = self.store.root / "jobs" / job["id"]
        folder = directory / "speech-parts"
        folder.mkdir(exist_ok=True)
        plan = job["steps"]["plan"]["output"]
        texts = []
        for section in plan["sections"]:
            text = section["text"]
            for original, spoken in sorted(plan["pronunciation"].items(), key=lambda item: -len(item[0])):
                if original:
                    text = text.replace(original, spoken)
            texts.append(text)
        voice = job["config"]["voice"] or self.settings.tts_voice
        if self.settings.tts_url:
            for i, text in enumerate(texts):
                self.check(job["id"])
                try:
                    with httpx.Client(timeout=self.settings.provider_timeout, trust_env=False) as client:
                        response = client.post(self.settings.tts_url.rstrip("/") + "/v1/audio/speech",
                                               json={"model": self.settings.tts_model, "input": text,
                                                     "voice": voice, "response_format": "wav"})
                        response.raise_for_status()
                        if len(response.content) > 30_000_000:
                            raise NeedsInput("Local TTS response exceeds 30 MB")
                        (folder / f"{i:03}.wav").write_bytes(response.content)
                except httpx.ConnectError:
                    raise NeedsInput("The configured local TTS server is not running") from None
                except httpx.HTTPError as exc:
                    raise ProviderError("Local TTS server returned an error") from exc
        else:
            self.inference(job, "speech", dict(texts=texts, voice=voice, output=str(folder)))
        cursor = 0.0
        parts = []
        filenames = []
        for i in range(len(texts)):
            self.check(job["id"])
            source = folder / f"{i:03}.wav"
            normalized_path = folder / f"part-{i:03}.wav"
            self.media.run(["-i", str(source), "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(normalized_path)],
                           directory, lambda: self.check(job["id"]), "normalize.log")
            duration = self.media.probe(normalized_path)["duration"]
            if duration <= 0:
                raise ProviderError("TTS returned empty speech")
            parts.append(dict(section=i, start=cursor, end=cursor + duration))
            cursor += duration
            filenames.append(normalized_path.name)
        if cursor > 180:
            raise NeedsInput("Narration exceeds 180 seconds; shorten the script")
        manifest = folder / "parts.txt"
        manifest.write_text("\n".join(f"file '{f}'" for f in filenames), "utf-8")
        output = directory / "narration.wav"
        self.media.run(["-f", "concat", "-safe", "0", "-i", str(manifest), "-c:a", "pcm_s16le", str(output)],
                       directory, lambda: self.check(job["id"]), "concat-speech.log")
        asset = self.media.import_file(output, "audio", AssetMeta(label="Generated narration", rights="owned").model_dump())
        actual = self.media.probe(self.store.asset_path(asset["id"]))["duration"]
        return Speech(asset_id=asset["id"], duration=actual, parts=parts).model_dump()

    def do_captions(self, job):
        plan = job["steps"]["plan"]["output"]
        speech = job["steps"]["speech"]["output"]
        duration = speech["duration"]
        if job["config"]["precise_captions"]:
            directory = self.store.root / "jobs" / job["id"]
            output = directory / "words.json"
            self.inference(job, "captions", dict(model=self.settings.whisper_model,
                audio=str(self.store.asset_path(speech["asset_id"])), prompt=" ".join(s["text"] for s in plan["sections"])[:1500],
                output=str(output)))
            words = json.loads(output.read_text("utf-8"))
            if not words:
                raise NeedsInput("Speech recognition found no spoken words; check the narration")
            # Keep timestamps from ASR and compare to the script; do not silently invent an alignment.
            transcript = " ".join(w["text"] for w in words)
            script = " ".join(s["text"] for s in plan["sections"])
            similarity = difflib.SequenceMatcher(None, normalized(script), normalized(transcript), autojunk=False).ratio()
            if similarity < 0.55:
                raise NeedsInput("Narration and script differ substantially; correct speech/script or provide manual captions")
            cues = []
            chunk = []
            for word in words:
                if chunk and (len(" ".join(w["text"] for w in chunk + [word])) > 52 or word["end"] - chunk[0]["start"] > 4):
                    cues.extend(split_cues(" ".join(w["text"] for w in chunk), chunk[0]["start"], chunk[-1]["end"]))
                    chunk = []
                chunk.append(word)
            if chunk:
                cues.extend(split_cues(" ".join(w["text"] for w in chunk), chunk[0]["start"], chunk[-1]["end"]))
            result = Captions(cues=cues, method="whisper", warnings=["Phụ đề nhận dạng từ giọng: kiểm tra lại tên riêng."])
        else:
            if not speech["parts"]:
                raise NeedsInput("Imported audio needs manual captions or precise_captions=true; no approximate timing is assumed")
            cues = []
            for part in speech["parts"]:
                if part["section"] >= len(plan["sections"]):
                    raise NeedsInput("Speech sections do not match the current script")
                cues.extend(split_cues(plan["sections"][part["section"]]["text"], part["start"], part["end"]))
            result = Captions(cues=cues, method="tts_sections", warnings=["Thời gian từng cụm chữ được ước lượng trong đoạn TTS; chưa căn từng từ."])
        if result.cues[-1].end > duration + 0.1:
            raise NeedsInput("Captions extend beyond the narration")
        return result.model_dump()

    def do_timeline(self, job):
        steps = {name: value["output"] for name, value in job["steps"].items()}
        plan, speech, assets, captions = (steps[k] for k in ("plan", "speech", "assets", "captions"))
        duration = speech["duration"]
        if duration > 180:
            raise NeedsInput("Narration exceeds the maximum short duration of 180 seconds")
        if captions["cues"][-1]["end"] > duration + 0.05:
            raise NeedsInput("Captions extend past the narration")
        scenes = []
        if all(s["section"] is None for s in assets["selected"]):
            count = len(assets["selected"])
            scenes = [dict(asset_id=s["asset_id"], start=duration*i/count, end=duration*(i+1)/count)
                      for i, s in enumerate(assets["selected"])]
        else:
            timing = {p["section"]: p for p in speech["parts"]}
            selected = {s["section"]: s["asset_id"] for s in assets["selected"]}
            if len(timing) != len(plan["sections"]) or set(selected) != set(range(len(plan["sections"]))):
                raise NeedsInput("Per-section images require exactly one image and speech timing per script section")
            cursor = 0
            for i in range(len(plan["sections"])):
                end = timing[i]["end"] if i + 1 < len(plan["sections"]) else duration
                scenes.append(dict(asset_id=selected[i], start=cursor, end=end))
                cursor = end
        for scene in scenes:
            self.store.asset_path(scene["asset_id"])
            if scene["end"] <= scene["start"]:
                raise NeedsInput("Empty image scene")
        return dict(duration=duration, scenes=scenes, voice_id=speech["asset_id"], music_id=assets["music_id"],
                    overlay_id=assets["overlay_id"], captions=captions["cues"], preset=job["config"]["preset"])

    def do_preview(self, job):
        directory = self.store.root / "jobs" / job["id"]
        timeline = job["steps"]["timeline"]["output"]
        path = self.media.render(timeline, directory, True, lambda: self.check(job["id"]))
        return dict(filename=path.name, bytes=path.stat().st_size, duration=timeline["duration"])

    def do_quality(self, job):
        directory = self.store.root / "jobs" / job["id"]
        timeline = job["steps"]["timeline"]["output"]
        report = self.media.verify(directory / "preview.mp4", timeline["duration"], check=lambda: self.check(job["id"]))
        reasons = []
        for name in ("research", "plan", "assets", "captions"):
            reasons.extend(job["steps"][name]["output"].get("warnings", []))
        for selected in job["steps"]["assets"]["output"]["selected"]:
            asset = self.store.asset(selected["asset_id"])
            if asset["rights"] == "unknown":
                reasons.append(f'Chưa xác nhận quyền ảnh: {asset["id"]}')
            if not asset["identity_verified"]:
                reasons.append(f'Chưa xác nhận nhân vật trong ảnh: {asset["id"]}')
        for field in ("music_id", "overlay_id"):
            key = timeline.get(field)
            if key and self.store.asset(key)["rights"] == "unknown":
                reasons.append(f"Chưa xác nhận quyền {field}: {key}")
        if abs(timeline["duration"] - job["config"]["target_seconds"]) > max(3, job["config"]["target_seconds"] * 0.2):
            reasons.append("Thời lượng lời đọc lệch mục tiêu; cần sửa kịch bản hoặc chấp nhận thời lượng hiện tại.")
        return dict(technical_pass=True, media=report, review_reasons=list(dict.fromkeys(reasons)))

    def do_export(self, job):
        directory = self.store.root / "jobs" / job["id"]
        timeline = job["steps"]["timeline"]["output"]
        path = self.media.render(timeline, directory, False, lambda: self.check(job["id"]))
        preset = timeline["preset"]
        report = self.media.verify(path, timeline["duration"], preset["width"], preset["height"], lambda: self.check(job["id"]))
        write_srt(timeline["captions"], directory / "subtitles.srt")
        plan = job["steps"]["plan"]["output"]
        metadata = dict(title=plan["title"], description="\n".join(s["text"] for s in plan["sections"]),
                        sources=job["steps"]["research"]["output"]["sources"], approval=job["approval"],
                        asset_attribution=[self.store.asset(s["asset_id"]) for s in job["steps"]["assets"]["output"]["selected"]])
        (directory / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), "utf-8")
        return dict(filename=path.name, bytes=path.stat().st_size, media=report,
                    subtitles="subtitles.srt", metadata="metadata.json")


class Worker:
    def __init__(self, pipeline):
        self.pipeline = pipeline
        self.lock = ProcessLock(pipeline.store.root)
        self.thread = None
        self.wake = threading.Event()

    def start(self):
        self.lock.acquire()
        try:
            self.pipeline.store.recover()
            self.pipeline.stop.clear()
            self.thread = threading.Thread(target=self.loop, name="shorts-worker", daemon=True)
            self.thread.start()
        except BaseException:
            self.lock.release()
            raise

    def loop(self):
        while not self.pipeline.stop.is_set():
            job = self.pipeline.store.claim()
            if job:
                self.pipeline.execute(job["id"])
            else:
                self.wake.wait(0.5)
                self.wake.clear()

    def close(self):
        self.pipeline.stop.set()
        self.wake.set()
        if self.thread:
            # Network provider calls have bounded timeouts; don't release ownership while still active.
            self.thread.join(self.pipeline.settings.provider_timeout + 30)
            if self.thread.is_alive():
                raise RuntimeError("Worker is still stopping; process lock retained")
        self.lock.release()

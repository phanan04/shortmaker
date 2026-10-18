from __future__ import annotations

import json
import math
import os
import random
import re
import shutil
import subprocess
import textwrap
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from .errors import Interrupted, NeedsInput
from .store import now


def run_process(args, cwd, check=lambda: None, timeout=1200, log="process.log", env=None):
    path = Path(cwd) / log
    started = time.monotonic()
    with path.open("wb") as stream:
        process = subprocess.Popen([str(a) for a in args], cwd=cwd, stdin=subprocess.DEVNULL,
                                   stdout=stream, stderr=subprocess.STDOUT, env=env,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            while process.poll() is None:
                check()
                if time.monotonic() - started > timeout:
                    raise RuntimeError(f"Process timed out after {timeout}s; see {path.name}")
                time.sleep(0.1)
            if process.returncode:
                tail = path.read_text("utf-8", errors="replace")[-2000:]
                raise RuntimeError(f"Process failed ({process.returncode}): {tail}")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    return path.read_text("utf-8", errors="replace")


def wrap_caption(text, width=30):
    lines = textwrap.wrap(" ".join(text.split()), width=width, break_long_words=True, break_on_hyphens=False)
    return ["\n".join(lines[i:i + 2]) for i in range(0, len(lines), 2)]


def split_cues(text, start, end):
    chunks = wrap_caption(text)
    weights = [len(c.replace("\n", " ").split()) for c in chunks]
    total = sum(weights)
    cursor = start
    result = []
    for chunk, weight in zip(chunks, weights):
        stop = cursor + (end - start) * weight / total
        result.append(dict(start=round(cursor, 3), end=round(stop, 3), text=chunk))
        cursor = stop
    if result:
        result[-1]["end"] = round(end, 3)
    return result


def srt_time(seconds):
    milliseconds = round(seconds * 1000)
    hours, milliseconds = divmod(milliseconds, 3600000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{milliseconds:03}"


def write_srt(cues, path):
    path.write_text("\n\n".join(f'{i}\n{srt_time(c["start"])} --> {srt_time(c["end"])}\n{c["text"]}'
                                    for i, c in enumerate(cues, 1)) + "\n", "utf-8")


class Media:
    def __init__(self, settings, store):
        self.settings = settings
        self.store = store

    def ffmpeg(self):
        path = self.settings.binary("ffmpeg")
        if not path:
            raise NeedsInput("FFmpeg missing; install it or configure ffmpeg in config.toml")
        return path

    def run(self, args, cwd, check=lambda: None, log="ffmpeg.log"):
        return run_process([self.ffmpeg(), "-hide_banner", "-nostdin", "-y", *args], cwd, check,
                           self.settings.render_timeout, log)

    def probe(self, path):
        probe = self.settings.binary("ffprobe")
        if probe:
            proc = subprocess.run([probe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
                                  creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            if proc.returncode:
                raise ValueError("Invalid media file")
            data = json.loads(proc.stdout)
            streams = data.get("streams", [])
            video = next((s for s in streams if s.get("codec_type") == "video"), {})
            audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
            return dict(duration=float(data["format"].get("duration", 0)), width=video.get("width"),
                        height=video.get("height"), audio=bool(audio), video=bool(video))
        proc = subprocess.run([self.ffmpeg(), "-hide_banner", "-i", str(path)], capture_output=True,
                              encoding="utf-8", errors="replace", timeout=30,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        report = proc.stderr
        duration = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", report)
        resolution = re.search(r"Video:.*?\b(\d{2,5})x(\d{2,5})\b", report)
        if not duration:
            raise ValueError("Media has no readable duration")
        h, m, s = map(float, duration.groups())
        return dict(duration=h * 3600 + m * 60 + s,
                    width=int(resolution[1]) if resolution else None,
                    height=int(resolution[2]) if resolution else None,
                    audio="Audio:" in report, video="Video:" in report)

    def import_file(self, path, kind, meta, asset_id=None):
        import uuid
        asset_id = asset_id or uuid.uuid4().hex
        destination = self.store.root / "assets" / (asset_id + {"image": ".jpg", "audio": ".wav", "overlay": ".mov"}[kind])
        info = {}
        try:
            if kind == "image":
                with Image.open(path) as image:
                    if image.width * image.height > 25_000_000 or min(image.size) < 100:
                        raise ValueError("Image must be at least 100px per side and at most 25 megapixels")
                    from PIL import ImageOps
                    image = ImageOps.exif_transpose(image).convert("RGB")
                    image.save(destination, quality=94)
                    info = {"width": image.width, "height": image.height}
            else:
                info = self.probe(path)
                if info["duration"] <= 0 or info["duration"] > 600:
                    raise ValueError("Media duration must be between 0 and 600 seconds")
                if kind == "audio":
                    if not info["audio"]:
                        raise ValueError("Audio stream missing")
                    self.run(["-i", str(path), "-vn", "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(destination)],
                             self.store.root, log=f"import-{asset_id}.log")
                else:
                    if not info["video"]:
                        raise ValueError("Video stream missing")
                    # Upload overlays must be real alpha videos (e.g. qtrle/ProRes). Preserve codec.
                    shutil.copyfile(path, destination)
            return self.store.put_asset(dict(id=asset_id, filename=destination.name, kind=kind,
                                             created_at=now(), **meta, **info))
        except (UnidentifiedImageError, Image.DecompressionBombError) as exc:
            destination.unlink(missing_ok=True)
            raise ValueError("Invalid or excessively large image") from exc
        except BaseException:
            destination.unlink(missing_ok=True)
            raise

    def font(self, name, size):
        candidates = [name, "arialbd.ttf", "DejaVuSans-Bold.ttf",
                      "C:/Windows/Fonts/arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
        for candidate in candidates:
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                pass
        raise NeedsInput("A Unicode TrueType font is required; install Arial or DejaVu Sans")

    def caption_track(self, cues, directory, width, height, preset, duration):
        track = directory / "captions"
        track.mkdir(exist_ok=True)
        blank = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        blank.save(track / "blank.png")
        font = self.font(preset["font"], max(12, round(preset["font_size"] * height / 1920)))
        stroke = max(1, round(height / 450))
        entries = []
        cursor = 0.0
        for i, cue in enumerate(cues):
            start, end = cue["start"], min(cue["end"], duration)
            if start >= duration:
                break
            if start > cursor:
                entries.extend(["file 'blank.png'", f"duration {start-cursor:.6f}"])
            frame = blank.copy()
            draw = ImageDraw.Draw(frame)
            current_font = font
            for size in range(font.size, 8, -1):
                box = draw.multiline_textbbox((0, 0), cue["text"], font=current_font, stroke_width=stroke, spacing=5)
                if box[2] - box[0] < width * 0.9:
                    break
                current_font = self.font(preset["font"], size - 1)
            draw.multiline_text((width / 2, height * preset["subtitle_y"]), cue["text"], anchor="mm", align="center",
                                font=current_font, fill=preset["accent"], stroke_fill="#201426", stroke_width=stroke, spacing=5)
            name = f"cue-{i:04}.png"
            frame.save(track / name)
            entries.extend([f"file '{name}'", f"duration {end-start:.6f}"])
            cursor = end
        if cursor < duration:
            entries.extend(["file 'blank.png'", f"duration {duration-cursor:.6f}"])
        entries.append("file 'blank.png'")
        manifest = track / "track.txt"
        manifest.write_text("\n".join(entries), "utf-8")
        return manifest

    def particle_loop(self, directory, check):
        folder = directory / "particles"
        folder.mkdir(exist_ok=True)
        rng = random.Random(42)
        dots = [(rng.randrange(270), rng.randrange(480), rng.choice([1, 1, 2]), rng.randrange(80, 170)) for _ in range(28)]
        for frame in range(24):
            image = Image.new("RGBA", (270, 480))
            draw = ImageDraw.Draw(image)
            for x, y, radius, alpha in dots:
                y = (y - frame * 20) % 480
                draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=(255, 223, 128, alpha))
            image.save(folder / f"{frame:03}.png")
        output = directory / "particles.mov"
        self.run(["-framerate", "12", "-i", str(folder / "%03d.png"), "-frames:v", "24", "-c:v", "qtrle", str(output)],
                 directory, check, "particles.log")
        return output

    def render(self, timeline, directory, preview, check):
        directory = Path(directory)
        preset = timeline["preset"]
        width, height = preset["width"], preset["height"]
        if preview:
            scale = min(1, 540 / width)
            width, height = max(180, round(width * scale / 2) * 2), max(320, round(height * scale / 2) * 2)
        fps = preset["fps"]
        segments = []
        for index, scene in enumerate(timeline["scenes"]):
            check()
            path = self.store.asset_path(scene["asset_id"])
            duration = scene["end"] - scene["start"]
            output = directory / f'{"p" if preview else "f"}-scene-{index:03}.mp4'
            vf = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1"
            if preset["motion"] == "zoom":
                vf += f",zoompan=z='min(max(zoom,pzoom)+0.0004,1.06)':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s={width}x{height}:fps={fps}"
            if preset["glow"]:
                vf += ",eq=saturation=1.08:brightness='0.012+0.008*sin(2*PI*t/4)':eval=frame"
            self.run(["-loop", "1", "-framerate", str(fps), "-i", str(path), "-vf", vf,
                      "-t", str(duration), "-r", str(fps), "-an", "-c:v", "libx264", "-preset", "ultrafast" if preview else "veryfast",
                      "-crf", "25" if preview else "20", "-pix_fmt", "yuv420p", "-threads", "4", str(output)], directory, check, "scene.log")
            segments.append(output)
        manifest = directory / "scenes.txt"
        manifest.write_text("\n".join(f"file '{s.name}'" for s in segments), "utf-8")
        captions = self.caption_track(timeline["captions"], directory, width, height, preset, timeline["duration"])
        inputs = ["-f", "concat", "-safe", "0", "-i", str(manifest), "-i", str(self.store.asset_path(timeline["voice_id"])),
                  "-f", "concat", "-safe", "0", "-i", str(captions)]
        filters = [f"[2:v]fps={fps},format=rgba[subs]", "[0:v][subs]overlay=0:0:eof_action=pass[v0]"]
        last = "v0"
        next_input = 3
        if preset["particles"]:
            particles = self.particle_loop(directory, check)
            inputs += ["-stream_loop", "-1", "-i", str(particles)]
            filters += [f"[{next_input}:v]scale={width}:{height}[dots]", f"[{last}][dots]overlay=0:0:shortest=1[v1]"]
            last = "v1"
            next_input += 1
        if timeline.get("overlay_id"):
            inputs += ["-stream_loop", "-1", "-i", str(self.store.asset_path(timeline["overlay_id"]))]
            filters += [f"[{next_input}:v]scale={width}:{height},format=rgba[fx]", f"[{last}][fx]overlay=0:0:shortest=1[v2]"]
            last = "v2"
            next_input += 1
        if timeline.get("music_id"):
            inputs += ["-stream_loop", "-1", "-i", str(self.store.asset_path(timeline["music_id"]))]
            filters += ["[1:a]asplit=2[voice][side]", f"[{next_input}:a]volume={preset['music_volume']}[music]",
                        "[music][side]sidechaincompress=threshold=0.03:ratio=8[ducked]",
                        "[voice][ducked]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11[a]"]
        else:
            filters += ["[1:a]loudnorm=I=-16:TP=-1.5:LRA=11[a]"]
        output = directory / ("preview.tmp.mp4" if preview else "final.tmp.mp4")
        self.run([*inputs, "-filter_complex", ";".join(filters), "-map", f"[{last}]", "-map", "[a]",
                  "-t", str(timeline["duration"]), "-r", str(fps), "-c:v", "libx264", "-preset", "ultrafast" if preview else "veryfast",
                  "-crf", "25" if preview else "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
                  "-ar", "48000", "-movflags", "+faststart", "-threads", "4", "-filter_complex_threads", "2", str(output)],
                 directory, check, "render-preview.log" if preview else "render-final.log")
        final = directory / ("preview.mp4" if preview else "final.mp4")
        output.replace(final)
        return final

    def verify(self, path, duration, width=None, height=None, check=lambda: None):
        report = self.probe(path)
        if not report["audio"] or not report["video"]:
            raise RuntimeError("Output must contain audio and video")
        if abs(report["duration"] - duration) > 0.4:
            raise RuntimeError("Output duration differs from narration")
        if width and (report["width"], report["height"]) != (width, height):
            raise RuntimeError("Output dimensions do not match preset")
        self.run(["-v", "error", "-xerror", "-i", str(path), "-f", "null", "-"], path.parent, check, "verify.log")
        return report

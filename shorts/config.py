from __future__ import annotations

import os
import shutil
import tomllib
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data_dir: Path = Path("data")
    host: str = "127.0.0.1"
    port: int = Field(8765, ge=1024, le=65535)
    api_token: str = ""
    ffmpeg: str = ""
    ffprobe: str = ""
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3.5:4b"
    ollama_context: int = Field(8192, ge=2048, le=32768)
    provider_timeout: int = Field(180, ge=1, le=1800)
    max_stage_attempts: int = Field(2, ge=1, le=3)
    tts_url: str = ""
    tts_voice: str = "Hải Đăng"
    tts_model: str = "vieneu-tts-v3-turbo"
    whisper_model: str = "small"
    allow_model_downloads: bool = False
    search_enabled: bool = True
    search_results: int = Field(5, ge=1, le=10)
    max_upload_mb: int = Field(100, ge=1, le=500)
    render_timeout: int = Field(1200, ge=10, le=7200)

    @model_validator(mode="after")
    def local_only(self):
        for value in (self.ollama_url, self.tts_url):
            if not value:
                continue
            p = urlparse(value)
            if p.scheme not in ("http", "https") or p.hostname not in ("127.0.0.1", "localhost", "::1") or p.username or p.password or p.query or p.fragment:
                raise ValueError("AI endpoints must be loopback URLs; paid cloud endpoints are disabled")
        if self.host not in ("127.0.0.1", "localhost", "::1") and len(self.api_token) < 24:
            raise ValueError("A token of at least 24 characters is required for LAN access")
        return self

    def binary(self, name: str) -> str | None:
        configured = getattr(self, name)
        if configured:
            return str(Path(configured).resolve()) if Path(configured).is_file() else shutil.which(configured)
        found = shutil.which(name)
        if found:
            return found
        if name == "ffmpeg":
            try:
                import imageio_ffmpeg
                return imageio_ffmpeg.get_ffmpeg_exe()
            except (ImportError, RuntimeError):
                pass
        if os.name == "nt":
            root = Path(os.environ.get("LOCALAPPDATA", "")) / "CapCut" / "Apps"
            paths = sorted(root.glob(f"*/{name}.exe"), key=lambda p: p.stat().st_mtime, reverse=True)
            if paths:
                return str(paths[0])
        return None


def load_settings(path: str | Path | None = None) -> Settings:
    path = Path(path or os.environ.get("SHORTS_CONFIG", "config.toml"))
    values = tomllib.loads(path.read_text("utf-8")) if path.exists() else {}
    if os.environ.get("SHORTS_DATA_DIR"):
        values["data_dir"] = os.environ["SHORTS_DATA_DIR"]
    if os.environ.get("SHORTS_API_TOKEN"):
        values["api_token"] = os.environ["SHORTS_API_TOKEN"]
    settings = Settings(**values)
    settings.data_dir = settings.data_dir.resolve()
    return settings

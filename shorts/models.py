from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Source(Strict):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    title: str = Field(min_length=1, max_length=500)
    url: str = Field(default="", max_length=2000)
    text: str = Field(default="", max_length=18000)
    period: str = Field(default="", max_length=200)


class Research(Strict):
    character: str = Field(min_length=1, max_length=200)
    work: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=12)
    sources: list[Source] = Field(min_length=1, max_length=12)
    warnings: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({s.id for s in self.sources}) != len(self.sources):
            raise ValueError("Source IDs must be unique")
        return self


class Section(Strict):
    text: str = Field(min_length=1, max_length=1800)
    source_ids: list[str] = Field(default_factory=list, max_length=12)
    evidence: str = Field(default="", max_length=2000)
    image_query: str = Field(default="", max_length=300)


class Plan(Strict):
    character: str = Field(min_length=1, max_length=200)
    work: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    angle: str = Field(min_length=1, max_length=300)
    sections: list[Section] = Field(min_length=1, max_length=16)
    pronunciation: dict[str, str] = Field(default_factory=dict, max_length=60)
    warnings: list[str] = Field(default_factory=list, max_length=20)


class Selection(Strict):
    asset_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    # Omitted: one image repeats across all sections. Otherwise one image per section.
    section: int | None = Field(default=None, ge=0, le=15)


class Assets(Strict):
    selected: list[Selection] = Field(min_length=1, max_length=16)
    music_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    overlay_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    warnings: list[str] = Field(default_factory=list, max_length=20)


class SpeechPart(Strict):
    section: int = Field(ge=0, le=15)
    start: float = Field(ge=0)
    end: float = Field(gt=0)


class Speech(Strict):
    asset_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    duration: float = Field(gt=0, le=600)
    parts: list[SpeechPart] = Field(default_factory=list, max_length=16)


class Cue(Strict):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("Caption end must be after start")
        if len(self.text.splitlines()) > 2 or any(len(line) > 44 for line in self.text.splitlines()):
            raise ValueError("Captions allow at most two lines of 44 characters")
        return self


class Captions(Strict):
    cues: list[Cue] = Field(min_length=1, max_length=1000)
    method: Literal["manual", "tts_sections", "whisper"] = "manual"
    warnings: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def no_overlap(self):
        for prev, cur in zip(self.cues, self.cues[1:]):
            if cur.start < prev.end - 0.001:
                raise ValueError("Captions must be sorted and cannot overlap")
        return self


class Preset(Strict):
    width: int = Field(1080, ge=180, le=2160, multiple_of=2)
    height: int = Field(1920, ge=320, le=3840, multiple_of=2)
    fps: int = Field(30, ge=15, le=60)
    font: str = Field("Arial", pattern=r"^[\w \-]{1,80}$")
    font_size: int = Field(54, ge=16, le=100)
    subtitle_y: float = Field(0.62, ge=0.2, le=0.8)
    accent: str = Field("#F5D98C", pattern=r"^#[0-9A-Fa-f]{6}$")
    motion: Literal["zoom", "static"] = "zoom"
    glow: bool = True
    particles: bool = True
    music_volume: float = Field(0.12, ge=0, le=0.5)


class JobCreate(Strict):
    character: str = Field(min_length=1, max_length=200)
    work: str = Field(default="", max_length=200)
    angle: str = Field(default="Giới thiệu xuất thân và bước ngoặt đầu tiên", max_length=300)
    period: str = Field(default="", max_length=200)
    target_seconds: int = Field(60, ge=5, le=180)
    mode: Literal["auto", "hybrid", "manual"] = "hybrid"
    sources: list[Source] = Field(default_factory=list, max_length=12)
    preset: Preset = Field(default_factory=Preset)
    voice: str = Field(default="", max_length=100)
    # Must exist under the configured whisper model cache when downloads are disabled.
    precise_captions: bool = True
    auto_start: bool = True
    require_review: bool = True


class Override(Strict):
    data: dict
    locked: bool = True


class Approval(Strict):
    content_verified: bool
    identity_verified: bool
    rights_confirmed: bool
    note: str = Field(default="", max_length=1000)


class AssetMeta(Strict):
    label: str = Field(default="", max_length=200)
    character: str = Field(default="", max_length=200)
    work: str = Field(default="", max_length=200)
    source_url: str = Field(default="", max_length=2000)
    rights: Literal["unknown", "owned", "licensed", "public_domain"] = "unknown"
    identity_verified: bool = False

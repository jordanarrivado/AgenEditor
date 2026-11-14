"""Source-linked speech evidence. Times are integer microseconds on the video source clock."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .common import SCHEMA_VERSION, ContractModel, Identifier, Sha256Hex, utcnow


class ModelPreset(StrEnum):
    TINY = "tiny"
    BASE = "base"
    SMALL = "small"


class TranscriptWord(ContractModel):
    id: Identifier
    segment_id: Identifier
    text: str = Field(min_length=1, max_length=300)
    start_us: int = Field(ge=0)
    end_us: int = Field(gt=0)
    probability: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def _range(self) -> TranscriptWord:
        if self.end_us <= self.start_us:
            raise ValueError("word end must follow start")
        return self


class TranscriptSegment(ContractModel):
    id: Identifier
    start_us: int = Field(ge=0)
    end_us: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=4000)
    word_ids: list[Identifier] = Field(min_length=1)

    @model_validator(mode="after")
    def _range(self) -> TranscriptSegment:
        if self.end_us <= self.start_us:
            raise ValueError("segment end must follow start")
        return self


class SilenceCandidate(ContractModel):
    start_us: int = Field(ge=0)
    end_us: int = Field(gt=0)
    before_word_id: Identifier | None = None
    after_word_id: Identifier | None = None
    method: Literal["word_gap"] = "word_gap"

    @model_validator(mode="after")
    def _range(self) -> SilenceCandidate:
        if self.end_us <= self.start_us:
            raise ValueError("silence end must follow start")
        return self


class HookCandidate(ContractModel):
    segment_id: Identifier
    score: float = Field(ge=0, le=1)
    signals: list[str] = Field(default_factory=list)


class Transcript(ContractModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    id: Identifier
    project_id: Identifier
    asset_id: Identifier
    source_sha256: Sha256Hex
    model_preset: ModelPreset
    model_version: str = Field(max_length=100)
    #: Device/compute type that actually produced this transcript, e.g. ``cpu/int8`` (None: unrecorded).
    compute: str | None = Field(default=None, max_length=40)
    language: str = Field(min_length=2, max_length=32)
    language_probability: float | None = Field(default=None, ge=0, le=1)
    duration_us: int = Field(gt=0)
    words: list[TranscriptWord] = Field(default_factory=list)
    segments: list[TranscriptSegment] = Field(default_factory=list)
    silence_candidates: list[SilenceCandidate] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _evidence(self) -> Transcript:
        ids = [w.id for w in self.words]
        if len(ids) != len(set(ids)):
            raise ValueError("word IDs must be unique")
        if any(w.end_us > self.duration_us for w in self.words):
            raise ValueError("word exceeds source duration")
        if any(b.start_us < a.start_us for a, b in zip(self.words, self.words[1:], strict=False)):
            raise ValueError("words must be ordered by source time")
        segments = {s.id: s for s in self.segments}
        if len(segments) != len(self.segments):
            raise ValueError("segment IDs must be unique")
        for word in self.words:
            segment = segments.get(word.segment_id)
            if segment is None or word.id not in segment.word_ids:
                raise ValueError("every word must belong to a cited segment")
        for segment in self.segments:
            if any(word_id not in ids for word_id in segment.word_ids):
                raise ValueError("segment cites an unknown word")
        return self

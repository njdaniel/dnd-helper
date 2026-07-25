"""Validated data at language-model boundaries."""

from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field


class Message(TypedDict):
    """A provider-neutral chat message."""

    role: Literal["user", "assistant"]
    content: str


class PersonaCard(BaseModel):
    """The player-safe traits used to portray one NPC."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    public_desc: str
    personality: str
    goals: str


class SceneContext(BaseModel):
    """Dynamic scene state supplied to an NPC."""

    model_config = ConfigDict(extra="forbid")

    location: str | None = None
    on_stage: list[str] = Field(default_factory=list)
    summary: str = ""


class NpcReply(BaseModel):
    """A model-generated NPC response safe for downstream typed use."""

    model_config = ConfigDict(extra="forbid")

    line: str = Field(min_length=1, max_length=1200)
    mood: str = ""
    memory_notes: list[str] = Field(default_factory=list)


class LoreExtraction(BaseModel):
    """Facts extracted from dialogue for later memory ingestion."""

    model_config = ConfigDict(extra="forbid")

    memory_notes: list[str] = Field(default_factory=list)

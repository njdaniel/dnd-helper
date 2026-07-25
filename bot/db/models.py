"""SQLAlchemy models for campaign state."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class shared by all database models."""


def _enum(*values: str, name: str) -> Enum:
    return Enum(
        *values,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )


class UpdatedAtMixin(TimestampMixin):
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )


class Guild(TimestampMixin, Base):
    __tablename__ = "guild"

    id: Mapped[int] = mapped_column(primary_key=True)
    discord_guild_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    name: Mapped[str] = mapped_column(String(255))
    content_rating: Mapped[str] = mapped_column(
        String(50),
        default="pg13",
        server_default="pg13",
    )
    dm_role_id: Mapped[int | None] = mapped_column(BigInteger)
    ambient_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("0")
    )
    daily_reply_budget: Mapped[int] = mapped_column(
        Integer, default=200, server_default=text("200")
    )


class Persona(UpdatedAtMixin, Base):
    __tablename__ = "persona"
    __table_args__ = (UniqueConstraint("guild_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(ForeignKey("guild.id"))
    name: Mapped[str] = mapped_column(String(255))
    avatar_url: Mapped[str | None] = mapped_column(String(2048))
    status: Mapped[str] = mapped_column(
        _enum("active", "retired", name="persona_status"),
        default="active",
        server_default="active",
    )
    public_desc: Mapped[str] = mapped_column(Text)
    personality: Mapped[str] = mapped_column(Text)
    goals: Mapped[str] = mapped_column(Text)
    secrets: Mapped[str | None] = mapped_column(Text)
    knowledge_tags: Mapped[list[str]] = mapped_column(
        JSON, default=list, server_default="[]"
    )
    created_by: Mapped[int] = mapped_column(BigInteger)


class LoreEntry(UpdatedAtMixin, Base):
    __tablename__ = "lore_entry"
    __table_args__ = (Index("ix_lore_entry_guild_category", "guild_id", "category"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(ForeignKey("guild.id"))
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(
        _enum(
            "location",
            "faction",
            "person",
            "event",
            "item",
            "rule",
            "other",
            name="lore_category",
        )
    )
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    visibility: Mapped[str] = mapped_column(
        _enum("public", "dm_only", name="lore_visibility"),
        default="public",
        server_default="public",
    )
    source: Mapped[str] = mapped_column(
        _enum("manual", "extracted", "recap", name="lore_source"),
        default="manual",
        server_default="manual",
    )
    image_url: Mapped[str | None] = mapped_column(String(2048))
    created_by: Mapped[int] = mapped_column(BigInteger)


class Scene(UpdatedAtMixin, Base):
    __tablename__ = "scene"
    __table_args__ = (
        Index(
            "uq_scene_active_channel",
            "channel_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(ForeignKey("guild.id"))
    channel_id: Mapped[int] = mapped_column(BigInteger)
    title: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(
        _enum("active", "paused", "ended", name="scene_status"),
        default="active",
        server_default="active",
    )
    mode: Mapped[str] = mapped_column(
        _enum("standard", "epic", name="scene_mode"),
        default="standard",
        server_default="standard",
    )
    location_lore_id: Mapped[int | None] = mapped_column(ForeignKey("lore_entry.id"))
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    image_url: Mapped[str | None] = mapped_column(String(2048))


class ScenePersona(Base):
    __tablename__ = "scene_persona"

    guild_id: Mapped[int] = mapped_column(ForeignKey("guild.id"))
    scene_id: Mapped[int] = mapped_column(ForeignKey("scene.id"), primary_key=True)
    persona_id: Mapped[int] = mapped_column(ForeignKey("persona.id"), primary_key=True)


class SceneMessage(TimestampMixin, Base):
    __tablename__ = "scene_message"
    __table_args__ = (
        Index("ix_scene_message_scene_created", "scene_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(ForeignKey("guild.id"))
    scene_id: Mapped[int] = mapped_column(ForeignKey("scene.id"))
    discord_message_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    author_type: Mapped[str] = mapped_column(
        _enum("player", "npc", "dm", "system", name="message_author_type")
    )
    author_name: Mapped[str] = mapped_column(String(255))
    persona_id: Mapped[int | None] = mapped_column(ForeignKey("persona.id"))
    content: Mapped[str] = mapped_column(Text)


class UsageLog(TimestampMixin, Base):
    __tablename__ = "usage_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(ForeignKey("guild.id"))
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(255))
    tier: Mapped[str | None] = mapped_column(String(50))
    input_tokens: Mapped[int] = mapped_column(Integer)
    cache_read_tokens: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )
    output_tokens: Mapped[int] = mapped_column(Integer)
    latency_ms: Mapped[int] = mapped_column(Integer)
    purpose: Mapped[str] = mapped_column(String(50))

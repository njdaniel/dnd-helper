"""Guild-scoped repository operations."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import (
    Guild,
    LoreEntry,
    Persona,
    Scene,
    SceneMessage,
    ScenePersona,
    UsageLog,
)


async def get_or_create_guild(session: AsyncSession, guild_id: int, name: str) -> Guild:
    """Return the Discord guild, creating it when first observed."""
    guild = await session.scalar(
        select(Guild).where(Guild.discord_guild_id == guild_id)
    )
    if guild is None:
        guild = Guild(discord_guild_id=guild_id, name=name)
        session.add(guild)
        await session.flush()
    return guild


async def create_persona(
    session: AsyncSession, guild_id: int, **values: object
) -> Persona:
    persona = Persona(guild_id=guild_id, **values)
    session.add(persona)
    await session.flush()
    return persona


async def get_persona(
    session: AsyncSession, guild_id: int, persona_id: int
) -> Persona | None:
    result = await session.scalars(
        select(Persona).where(Persona.guild_id == guild_id, Persona.id == persona_id)
    )
    return result.first()


async def get_persona_by_name(
    session: AsyncSession, guild_id: int, name: str
) -> Persona | None:
    result = await session.scalars(
        select(Persona).where(Persona.guild_id == guild_id, Persona.name == name)
    )
    return result.first()


async def list_personas(
    session: AsyncSession, guild_id: int, *, status: str | None = None
) -> list[Persona]:
    statement = select(Persona).where(Persona.guild_id == guild_id)
    if status is not None:
        statement = statement.where(Persona.status == status)
    return list((await session.scalars(statement.order_by(Persona.name))).all())


async def create_lore_entry(
    session: AsyncSession, guild_id: int, **values: object
) -> LoreEntry:
    entry = LoreEntry(guild_id=guild_id, **values)
    session.add(entry)
    await session.flush()
    return entry


async def get_lore_entry(
    session: AsyncSession, guild_id: int, lore_entry_id: int
) -> LoreEntry | None:
    result = await session.scalars(
        select(LoreEntry).where(
            LoreEntry.guild_id == guild_id, LoreEntry.id == lore_entry_id
        )
    )
    return result.first()


async def create_scene(session: AsyncSession, guild_id: int, **values: object) -> Scene:
    scene = Scene(guild_id=guild_id, **values)
    session.add(scene)
    await session.flush()
    return scene


async def get_active_scene(
    session: AsyncSession, guild_id: int, channel_id: int
) -> Scene | None:
    result = await session.scalars(
        select(Scene).where(
            Scene.guild_id == guild_id,
            Scene.channel_id == channel_id,
            Scene.status == "active",
        )
    )
    return result.first()


async def add_scene_persona(
    session: AsyncSession, guild_id: int, scene_id: int, persona_id: int
) -> ScenePersona:
    association = ScenePersona(
        guild_id=guild_id, scene_id=scene_id, persona_id=persona_id
    )
    session.add(association)
    await session.flush()
    return association


async def list_scene_personas(
    session: AsyncSession, guild_id: int, scene_id: int
) -> list[Persona]:
    statement = (
        select(Persona)
        .join(ScenePersona, ScenePersona.persona_id == Persona.id)
        .where(
            ScenePersona.guild_id == guild_id,
            ScenePersona.scene_id == scene_id,
            Persona.guild_id == guild_id,
        )
        .order_by(Persona.name)
    )
    return list((await session.scalars(statement)).all())


async def create_scene_message(
    session: AsyncSession, guild_id: int, **values: object
) -> SceneMessage:
    message = SceneMessage(guild_id=guild_id, **values)
    session.add(message)
    await session.flush()
    return message


async def list_scene_messages(
    session: AsyncSession, guild_id: int, scene_id: int, *, limit: int = 40
) -> list[SceneMessage]:
    statement = (
        select(SceneMessage)
        .where(SceneMessage.guild_id == guild_id, SceneMessage.scene_id == scene_id)
        .order_by(SceneMessage.created_at.desc(), SceneMessage.id.desc())
        .limit(limit)
    )
    messages = list((await session.scalars(statement)).all())
    messages.reverse()
    return messages


async def create_usage_log(
    session: AsyncSession, guild_id: int, **values: object
) -> UsageLog:
    usage = UsageLog(guild_id=guild_id, **values)
    session.add(usage)
    await session.flush()
    return usage

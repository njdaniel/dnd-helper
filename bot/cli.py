"""Small command-line harness for exercising NPC dialogue without Discord."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from time import perf_counter_ns, time_ns

from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import Settings
from bot.db import repo
from bot.db.models import Persona, SceneMessage
from bot.db.session import create_engine, create_session_factory, session_scope
from bot.engine.llm import LLMEngine
from bot.engine.persona import generate_reply
from bot.engine.providers import LLMProvider, ProviderError

CLI_GUILD_ID = 0
CLI_CHANNEL_ID = 0
CLI_AUTHOR_ID = 0
CLI_AUTHOR_NAME = "Player"


@dataclass
class CliScenePrompt:
    """Concrete scene view satisfying persona.ScenePrompt."""

    content_rating: str
    location: str | None
    on_stage: Sequence[str]
    summary: str
    messages: Sequence[SceneMessage]


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser separately so its contract is easy to test."""
    parser = argparse.ArgumentParser(prog="python -m bot.cli")
    commands = parser.add_subparsers(dest="command", required=True)

    npc = commands.add_parser("npc")
    npc_commands = npc.add_subparsers(dest="npc_command", required=True)
    create = npc_commands.add_parser("create")
    create.add_argument("name")
    create.add_argument("--public-desc", default="")
    create.add_argument("--personality", default="")
    create.add_argument("--goals", default="")
    create.add_argument("--secret")
    create.add_argument("--tags", default="")
    npc_commands.add_parser("list")
    show = npc_commands.add_parser("show")
    show.add_argument("name")
    show.add_argument("--dm", action="store_true")

    lore = commands.add_parser("lore")
    lore_commands = lore.add_subparsers(dest="lore_command", required=True)
    add = lore_commands.add_parser("add")
    add.add_argument("title")
    add.add_argument("--body", default="")
    add.add_argument(
        "--category",
        choices=("location", "faction", "person", "event", "item", "rule", "other"),
        default="other",
    )
    add.add_argument("--tags", default="")
    add.add_argument("--dm-only", action="store_true")

    scene = commands.add_parser("scene")
    scene_commands = scene.add_subparsers(dest="scene_command", required=True)
    start = scene_commands.add_parser("start")
    start.add_argument("title")
    start.add_argument("--npc", required=True)
    scene_commands.add_parser("end")

    talk = commands.add_parser("talk")
    talk.add_argument("npc")
    talk.add_argument("message")
    talk.add_argument("--dm", action="store_true")
    return parser


def _tags(value: str) -> list[str]:
    return [tag.strip() for tag in value.split(",") if tag.strip()]


async def run_command(
    args: argparse.Namespace,
    *,
    settings: Settings,
    session: AsyncSession,
    provider: LLMProvider | None = None,
    output: Callable[[str], None] = print,
) -> None:
    """Execute one parsed command against the bot's normal database APIs."""
    guild = await repo.get_or_create_guild(session, CLI_GUILD_ID, "Local CLI Campaign")

    if args.command == "npc":
        if args.npc_command == "create":
            persona = await repo.create_persona(
                session,
                guild.id,
                name=args.name,
                avatar_url=None,
                public_desc=args.public_desc,
                personality=args.personality,
                goals=args.goals,
                secrets=args.secret,
                knowledge_tags=_tags(args.tags),
                created_by=CLI_AUTHOR_ID,
            )
            output(f"Created NPC {persona.name}.")
        elif args.npc_command == "list":
            personas = await repo.list_personas(session, guild.id)
            output("\n".join(persona.name for persona in personas) or "No NPCs found.")
        else:
            persona = await _require_persona(session, guild.id, args.name)
            lines = [
                f"Name: {persona.name}",
                f"Public description: {persona.public_desc}",
                f"Personality: {persona.personality}",
                f"Goals: {persona.goals}",
                f"Tags: {', '.join(persona.knowledge_tags)}",
            ]
            if args.dm:
                lines.append(f"Secret: {persona.secrets or 'None'}")
            output("\n".join(lines))
        return

    if args.command == "lore":
        entry = await repo.create_lore_entry(
            session,
            guild.id,
            title=args.title,
            body=args.body,
            category=args.category,
            tags=_tags(args.tags),
            visibility="dm_only" if args.dm_only else "public",
            source="manual",
            image_url=None,
            created_by=CLI_AUTHOR_ID,
        )
        output(f"Added lore {entry.title}.")
        return

    if args.command == "scene":
        active = await repo.get_active_scene(session, guild.id, CLI_CHANNEL_ID)
        if args.scene_command == "end":
            if active is None:
                raise ValueError("No active CLI scene.")
            active.status = "ended"
            await session.flush()
            output("Scene ended.")
            return
        if active is not None:
            raise ValueError("A CLI scene is already active; end it first.")
        persona = await _require_persona(session, guild.id, args.npc)
        scene = await repo.create_scene(
            session,
            guild.id,
            channel_id=CLI_CHANNEL_ID,
            title=args.title,
        )
        await repo.add_scene_persona(session, guild.id, scene.id, persona.id)
        output(f"Started scene {args.title} with {persona.name}.")
        return

    await _talk(
        args,
        settings,
        session,
        guild.id,
        guild.content_rating,
        provider,
        output,
    )


async def _talk(
    args: argparse.Namespace,
    settings: Settings,
    session: AsyncSession,
    guild_id: int,
    content_rating: str,
    provider: LLMProvider | None,
    output: Callable[[str], None],
) -> None:
    persona = await _require_persona(session, guild_id, args.npc)
    scene = await repo.get_active_scene(session, guild_id, CLI_CHANNEL_ID)
    if scene is None:
        raise ValueError("No active CLI scene. Start one with `scene start`.")
    on_stage = await repo.list_scene_personas(session, guild_id, scene.id)
    if persona.id not in {item.id for item in on_stage}:
        raise ValueError(f"{persona.name} is not in the active scene.")

    message_id = -time_ns()
    await repo.create_scene_message(
        session,
        guild_id,
        scene_id=scene.id,
        discord_message_id=message_id,
        author_type="dm" if args.dm else "player",
        author_name="DM" if args.dm else CLI_AUTHOR_NAME,
        persona_id=None,
        content=args.message,
    )
    messages = await repo.list_scene_messages(session, guild_id, scene.id)
    location = None
    if scene.location_lore_id is not None:
        lore = await repo.get_lore_entry(session, guild_id, scene.location_lore_id)
        location = lore.title if lore is not None else None
    prompt_scene = CliScenePrompt(
        content_rating=content_rating,
        location=location,
        on_stage=[item.name for item in on_stage],
        summary=scene.summary,
        messages=messages,
    )
    engine = LLMEngine(
        settings=settings,
        session=session,
        guild_id=guild_id,
        provider=provider,
    )
    started = perf_counter_ns()
    reply = await generate_reply(engine, persona, prompt_scene, args.dm)
    elapsed = (perf_counter_ns() - started) / 1_000_000
    await repo.create_scene_message(
        session,
        guild_id,
        scene_id=scene.id,
        discord_message_id=message_id - 1,
        author_type="npc",
        author_name=persona.name,
        persona_id=persona.id,
        content=reply.line,
    )
    provider_name = provider.name if provider is not None else settings.llm_provider
    model = getattr(settings, f"{provider_name.replace('-', '_')}_model_dialogue")
    output(reply.line)
    output(f"[provider={provider_name} model={model} elapsed={elapsed:.0f}ms]")


async def _require_persona(session: AsyncSession, guild_id: int, name: str) -> Persona:
    persona = await repo.get_persona_by_name(session, guild_id, name)
    if persona is None:
        raise ValueError(f"NPC {name!r} not found.")
    return persona


async def _main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings()
    engine = create_engine(settings.database_url)
    factory = create_session_factory(engine)
    try:
        async with session_scope(factory) as session:
            await run_command(args, settings=settings, session=session)
    except (ProviderError, ValueError) as error:
        print(f"Error: {error}")
        return 1
    finally:
        await engine.dispose()
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_main()))


if __name__ == "__main__":
    main()

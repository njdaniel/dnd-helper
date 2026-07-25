"""CLI parsing and dialogue-path tests."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.cli import CLI_CHANNEL_ID, build_parser, run_command
from bot.config import Settings
from bot.db import repo
from bot.db.models import SceneMessage


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        DISCORD_TOKEN="test-token",
        DEV_GUILD_ID=123,
        LLM_PROVIDER="anthropic",
        ANTHROPIC_API_KEY="test-key",
        ANTHROPIC_MODEL_DIALOGUE="test-dialogue",
    )


def test_parser_accepts_npc_lore_scene_and_talk_options() -> None:
    parser = build_parser()

    npc = parser.parse_args(
        [
            "npc",
            "create",
            "Mira",
            "--public-desc",
            "A mapmaker",
            "--personality",
            "Wary",
            "--goals",
            "Explore",
            "--secret",
            "Knows the road",
            "--tags",
            "roads, maps",
        ]
    )
    lore = parser.parse_args(
        [
            "lore",
            "add",
            "Old Road",
            "--body",
            "It vanished.",
            "--category",
            "location",
            "--tags",
            "roads",
            "--dm-only",
        ]
    )
    scene = parser.parse_args(["scene", "start", "Crossroads", "--npc", "Mira"])
    talk = parser.parse_args(["talk", "Mira", "Who goes there?", "--dm"])

    assert (npc.name, npc.secret, npc.tags) == (
        "Mira",
        "Knows the road",
        "roads, maps",
    )
    assert (lore.title, lore.category, lore.dm_only) == (
        "Old Road",
        "location",
        True,
    )
    assert (scene.title, scene.npc) == ("Crossroads", "Mira")
    assert (talk.npc, talk.message, talk.dm) == ("Mira", "Who goes there?", True)


async def test_talk_uses_fake_provider_and_persists_both_sides(
    db_session: AsyncSession, fake_provider
) -> None:
    parser = build_parser()
    settings = _settings()
    output: list[str] = []

    await run_command(
        parser.parse_args(
            [
                "npc",
                "create",
                "Mira",
                "--public-desc",
                "A mapmaker",
                "--personality",
                "Wary",
                "--goals",
                "Explore",
                "--secret",
                "SECRET_SENTINEL",
            ]
        ),
        settings=settings,
        session=db_session,
        provider=fake_provider,
        output=output.append,
    )
    await run_command(
        parser.parse_args(["scene", "start", "Crossroads", "--npc", "Mira"]),
        settings=settings,
        session=db_session,
        provider=fake_provider,
        output=output.append,
    )
    await run_command(
        parser.parse_args(["talk", "Mira", "Who goes there?"]),
        settings=settings,
        session=db_session,
        provider=fake_provider,
        output=output.append,
    )

    guild = await repo.get_or_create_guild(db_session, 0, "Local CLI Campaign")
    scene = await repo.get_active_scene(db_session, guild.id, CLI_CHANNEL_ID)
    assert scene is not None
    messages = (
        await db_session.scalars(
            select(SceneMessage)
            .where(SceneMessage.scene_id == scene.id)
            .order_by(SceneMessage.id)
        )
    ).all()
    assert [(row.author_type, row.author_name, row.content) for row in messages] == [
        ("player", "Player", "Who goes there?"),
        ("npc", "Mira", "The old road remembers."),
    ]
    assert fake_provider.calls == 1
    assert output[-2] == "The old road remembers."
    assert "provider=anthropic model=test-dialogue elapsed=" in output[-1]

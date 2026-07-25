"""Operational slash commands."""

from discord import Interaction, app_commands
from discord.ext import commands

from bot.config import Settings


def configured_model(settings: Settings) -> str:
    """Return the dialogue model selected for normal bot replies."""
    if settings.llm_provider == "anthropic":
        return settings.anthropic_model_dialogue
    return settings.ollama_model_dialogue


class Meta(commands.Cog):
    """Commands that report whether the bot is healthy."""

    def __init__(self, bot: commands.Bot, settings: Settings) -> None:
        self.bot = bot
        self.settings = settings

    @app_commands.command(name="ping", description="Check the bot's connection.")
    async def ping(self, interaction: Interaction) -> None:
        """Report Discord latency and the active language model."""
        latency_ms = round(self.bot.latency * 1000)
        await interaction.response.send_message(
            f"Pong! {latency_ms} ms · "
            f"{self.settings.llm_provider} / {configured_model(self.settings)}"
        )


async def setup(bot: commands.Bot) -> None:
    """Load the metadata command into a configured bot."""
    settings = getattr(bot, "settings", None)
    if not isinstance(settings, Settings):
        raise RuntimeError("the bot must expose validated settings before loading cogs")
    await bot.add_cog(Meta(bot, settings))

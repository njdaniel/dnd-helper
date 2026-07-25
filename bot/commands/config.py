"""Guild configuration and model-usage slash commands."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import func, select

from bot.config import Settings
from bot.db.models import Guild, UsageLog
from bot.db.session import SessionFactory

# USD per million tokens, keyed by the model actually recorded on the usage
# row. Keying by tier would misprice any deployment that points a tier at a
# different model — which the ANTHROPIC_MODEL_* settings explicitly allow.
_ANTHROPIC_PRICES = {
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5": (10.0, 50.0),
}

# Cache reads cost ~10% of the base input rate; cache writes ~1.25x. Omitting
# writes understates spend on exactly the requests prompt caching optimises.
_CACHE_READ_MULTIPLIER = 0.1
_CACHE_WRITE_MULTIPLIER = 1.25

# Discord caps message content at 2,000 characters and an embed
# description at 4,096. A usage row is roughly 100 characters, so this
# leaves generous headroom for the totals that follow.
_MAX_USAGE_ROWS = 20


@dataclass(frozen=True)
class UsageSummary:
    """One provider/tier row rendered by `/usage`."""

    provider: str
    tier: str | None
    model: str
    calls: int
    input_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    output_tokens: int
    mean_latency_ms: float

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.cache_read_tokens
            + self.cache_creation_tokens
            + self.output_tokens
        )

    @property
    def is_priced(self) -> bool:
        """Whether a spend figure can be produced for this row at all."""
        return self.provider == "anthropic" and self.model in _ANTHROPIC_PRICES

    @property
    def estimated_spend(self) -> float:
        prices = _ANTHROPIC_PRICES.get(self.model)
        if self.provider != "anthropic" or prices is None:
            return 0.0
        input_price, output_price = prices
        return (
            self.input_tokens * input_price
            + self.cache_read_tokens * input_price * _CACHE_READ_MULTIPLIER
            + self.cache_creation_tokens * input_price * _CACHE_WRITE_MULTIPLIER
            + self.output_tokens * output_price
        ) / 1_000_000


class ConfigCog(commands.Cog):
    """Commands for guild settings and operational visibility."""

    def __init__(self, session_factory: SessionFactory, settings: Settings) -> None:
        self._session_factory = session_factory
        self._settings = settings

    @staticmethod
    def _is_dm(interaction: discord.Interaction, guild: Guild) -> bool:
        discord_guild = interaction.guild
        if discord_guild is None:
            return False
        if interaction.user.id == discord_guild.owner_id:
            return True
        if guild.dm_role_id is None or not isinstance(interaction.user, discord.Member):
            return False
        return any(role.id == guild.dm_role_id for role in interaction.user.roles)

    @app_commands.command(name="config", description="Update campaign settings.")
    @app_commands.describe(
        content_rating="Campaign content rating, such as pg13 or mature",
        dm_role="Role allowed to administer the campaign",
        daily_reply_budget="Maximum model replies per day",
    )
    async def config(
        self,
        interaction: discord.Interaction,
        content_rating: str | None = None,
        dm_role: discord.Role | None = None,
        daily_reply_budget: app_commands.Range[int, 0, 100_000] | None = None,
    ) -> None:
        """Set one or more guild configuration values."""
        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        async with self._session_factory() as session, session.begin():
            guild = await session.scalar(
                select(Guild).where(Guild.discord_guild_id == interaction.guild.id)
            )
            if guild is None:
                guild = Guild(
                    discord_guild_id=interaction.guild.id,
                    name=interaction.guild.name,
                )
                session.add(guild)
                await session.flush()

            if not self._is_dm(interaction, guild):
                await interaction.response.send_message(
                    "Only the configured DM role or server owner can use `/config`.",
                    ephemeral=True,
                )
                return

            changed: list[str] = []
            if content_rating is not None:
                rating = content_rating.strip()
                if not rating or len(rating) > 50:
                    await interaction.response.send_message(
                        "Content rating must be between 1 and 50 characters.",
                        ephemeral=True,
                    )
                    return
                guild.content_rating = rating
                changed.append(f"content rating: `{rating}`")
            if dm_role is not None:
                guild.dm_role_id = dm_role.id
                changed.append(f"DM role: {dm_role.mention}")
            if daily_reply_budget is not None:
                guild.daily_reply_budget = daily_reply_budget
                changed.append(f"daily reply budget: `{daily_reply_budget:,}`")

            if not changed:
                changed = [
                    f"content rating: `{guild.content_rating}`",
                    f"DM role: <@&{guild.dm_role_id}>"
                    if guild.dm_role_id is not None
                    else "DM role: not set",
                    f"daily reply budget: `{guild.daily_reply_budget:,}`",
                ]

        await interaction.response.send_message(
            "\n".join(changed),
            ephemeral=True,
        )

    @app_commands.command(name="usage", description="Show this month's model usage.")
    async def usage(self, interaction: discord.Interaction) -> None:
        """Show monthly provider/tier totals and today's remaining budget."""
        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        now = datetime.now(UTC)
        month_start = datetime(now.year, now.month, 1)
        today = now.date().isoformat()

        async with self._session_factory() as session:
            guild = await session.scalar(
                select(Guild).where(Guild.discord_guild_id == interaction.guild.id)
            )
            if guild is None:
                await interaction.response.send_message(
                    "Run `/config` before viewing usage.", ephemeral=True
                )
                return

            rows = (
                await session.execute(
                    select(
                        UsageLog.provider,
                        UsageLog.tier,
                        UsageLog.model,
                        func.count(UsageLog.id),
                        func.sum(UsageLog.input_tokens),
                        func.sum(UsageLog.cache_read_tokens),
                        func.sum(UsageLog.cache_creation_tokens),
                        func.sum(UsageLog.output_tokens),
                        func.avg(UsageLog.latency_ms),
                    )
                    .where(
                        UsageLog.guild_id == guild.id,
                        UsageLog.created_at >= month_start,
                    )
                    .group_by(UsageLog.provider, UsageLog.tier, UsageLog.model)
                    .order_by(UsageLog.provider, UsageLog.tier, UsageLog.model)
                )
            ).all()
            summaries = [
                UsageSummary(
                    provider=row[0],
                    tier=row[1],
                    model=row[2],
                    calls=row[3],
                    input_tokens=row[4] or 0,
                    cache_read_tokens=row[5] or 0,
                    cache_creation_tokens=row[6] or 0,
                    output_tokens=row[7] or 0,
                    mean_latency_ms=row[8] or 0.0,
                )
                for row in rows
            ]
            used_today = (
                await session.scalar(
                    select(func.count(UsageLog.id)).where(
                        UsageLog.guild_id == guild.id,
                        func.date(UsageLog.created_at) == today,
                    )
                )
                or 0
            )
            budget = guild.daily_reply_budget

        # Grouping by model (rather than tier) makes the row count unbounded:
        # every model ever configured this month gets its own line. Show the
        # busiest and say how many were dropped, so a long history degrades
        # into a shorter report instead of a message Discord refuses to send.
        ranked = sorted(summaries, key=lambda s: s.calls, reverse=True)
        shown, hidden = ranked[:_MAX_USAGE_ROWS], ranked[_MAX_USAGE_ROWS:]
        lines = [
            (
                f"**{summary.provider} / {summary.tier or 'unknown'} / "
                f"{summary.model}** — "
                f"{summary.calls:,} calls, {summary.total_tokens:,} tokens, "
                f"{summary.mean_latency_ms:,.0f} ms mean latency"
            )
            for summary in shown
        ]
        if hidden:
            lines.append(
                f"*…and {len(hidden)} more combination(s), "
                f"{sum(s.calls for s in hidden):,} calls — included in the "
                "totals below.*"
            )
        if not lines:
            lines.append("No model calls recorded this month.")

        total_calls = sum(summary.calls for summary in summaries)
        total_tokens = sum(summary.total_tokens for summary in summaries)
        mean_latency = (
            sum(summary.mean_latency_ms * summary.calls for summary in summaries)
            / total_calls
            if total_calls
            else 0.0
        )
        lines.extend(
            [
                "",
                (
                    f"**Month total** — {total_calls:,} calls, "
                    f"{total_tokens:,} tokens, "
                    f"{mean_latency:,.0f} ms mean latency"
                ),
                (
                    f"**Daily budget remaining** — "
                    f"{max(budget - used_today, 0):,} / {budget:,}"
                ),
            ]
        )
        # Driven by the rows returned, not the currently selected provider:
        # switching to Ollama must not hide money already spent this month.
        if any(summary.is_priced for summary in summaries):
            spend = sum(summary.estimated_spend for summary in summaries)
            lines.append(f"**Estimated Anthropic spend** — ${spend:,.4f}")
        unpriced = {
            summary.model
            for summary in summaries
            if summary.provider == "anthropic" and not summary.is_priced
        }
        if unpriced:
            lines.append(
                "**Unpriced models** — "
                + ", ".join(sorted(unpriced))
                + " (no rate on file; spend above excludes them)"
            )

        embed = discord.Embed(title="Usage this month", description="\n".join(lines))
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    """Load the cog from a bot that owns shared settings and sessions."""
    session_factory = getattr(bot, "session_factory")
    settings = getattr(bot, "settings")
    await bot.add_cog(ConfigCog(session_factory, settings))

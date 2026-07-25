"""Create the initial campaign schema."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "guild",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("discord_guild_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "content_rating",
            sa.String(length=50),
            nullable=False,
            server_default="pg13",
        ),
        sa.Column("dm_role_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "ambient_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "daily_reply_budget",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("200"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.UniqueConstraint("discord_guild_id"),
    )
    op.create_table(
        "persona",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.Integer(), sa.ForeignKey("guild.id"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("avatar_url", sa.String(length=2048), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "active",
                "retired",
                name="persona_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
            server_default="active",
        ),
        sa.Column("public_desc", sa.Text(), nullable=False),
        sa.Column("personality", sa.Text(), nullable=False),
        sa.Column("goals", sa.Text(), nullable=False),
        sa.Column("secrets", sa.Text(), nullable=True),
        sa.Column("knowledge_tags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.UniqueConstraint("guild_id", "name"),
    )
    op.create_table(
        "lore_entry",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.Integer(), sa.ForeignKey("guild.id"), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "category",
            sa.Enum(
                "location",
                "faction",
                "person",
                "event",
                "item",
                "rule",
                "other",
                name="lore_category",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "visibility",
            sa.Enum(
                "public",
                "dm_only",
                name="lore_visibility",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
            server_default="public",
        ),
        sa.Column(
            "source",
            sa.Enum(
                "manual",
                "extracted",
                "recap",
                name="lore_source",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
            server_default="manual",
        ),
        sa.Column("image_url", sa.String(length=2048), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_lore_entry_guild_category",
        "lore_entry",
        ["guild_id", "category"],
    )
    op.create_table(
        "scene",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.Integer(), sa.ForeignKey("guild.id"), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "active",
                "paused",
                "ended",
                name="scene_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "mode",
            sa.Enum(
                "standard",
                "epic",
                name="scene_mode",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
            server_default="standard",
        ),
        sa.Column(
            "location_lore_id",
            sa.Integer(),
            sa.ForeignKey("lore_entry.id"),
            nullable=True,
        ),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("image_url", sa.String(length=2048), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
    )
    op.create_index(
        "uq_scene_active_channel",
        "scene",
        ["channel_id"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
    )
    op.create_table(
        "scene_persona",
        sa.Column("guild_id", sa.Integer(), sa.ForeignKey("guild.id"), nullable=False),
        sa.Column("scene_id", sa.Integer(), sa.ForeignKey("scene.id"), nullable=False),
        sa.Column(
            "persona_id", sa.Integer(), sa.ForeignKey("persona.id"), nullable=False
        ),
        sa.PrimaryKeyConstraint("scene_id", "persona_id"),
    )
    op.create_table(
        "scene_message",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.Integer(), sa.ForeignKey("guild.id"), nullable=False),
        sa.Column("scene_id", sa.Integer(), sa.ForeignKey("scene.id"), nullable=False),
        sa.Column("discord_message_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "author_type",
            sa.Enum(
                "player",
                "npc",
                "dm",
                "system",
                name="message_author_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("author_name", sa.String(length=255), nullable=False),
        sa.Column(
            "persona_id",
            sa.Integer(),
            sa.ForeignKey("persona.id"),
            nullable=True,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.UniqueConstraint("discord_message_id"),
    )
    op.create_index(
        "ix_scene_message_scene_created",
        "scene_message",
        ["scene_id", "created_at"],
    )
    op.create_table(
        "usage_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.Integer(), sa.ForeignKey("guild.id"), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column(
            "cache_read_tokens",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(length=50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("usage_log")
    op.drop_index("ix_scene_message_scene_created", table_name="scene_message")
    op.drop_table("scene_message")
    op.drop_table("scene_persona")
    op.drop_index("uq_scene_active_channel", table_name="scene")
    op.drop_table("scene")
    op.drop_index("ix_lore_entry_guild_category", table_name="lore_entry")
    op.drop_table("lore_entry")
    op.drop_table("persona")
    op.drop_table("guild")

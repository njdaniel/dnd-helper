"""Make a lore title unique within its guild.

Every /lore command addresses an entry by title. Without this, two entries can
share one and the lookup silently returns the older, leaving the other
unreachable and showing two identical autocomplete choices.

Depends on 0002 (usage_log tier and cache-write columns), so that migration
must land first.
"""

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # SQLite cannot ALTER a table to add a constraint, so batch mode rebuilds
    # it. This fails loudly if duplicate titles already exist — which is the
    # correct outcome: they need resolving by hand, not silently collapsing.
    with op.batch_alter_table("lore_entry") as batch:
        batch.create_unique_constraint(
            "uq_lore_entry_guild_title", ["guild_id", "title"]
        )


def downgrade() -> None:
    with op.batch_alter_table("lore_entry") as batch:
        batch.drop_constraint("uq_lore_entry_guild_title", type_="unique")

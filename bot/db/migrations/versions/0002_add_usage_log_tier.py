"""Store the configured model tier with each usage row."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "usage_log",
        sa.Column("tier", sa.String(length=50), nullable=True),
    )

    op.add_column(
        "usage_log",
        sa.Column(
            "cache_creation_tokens",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("usage_log", "cache_creation_tokens")
    op.drop_column("usage_log", "tier")

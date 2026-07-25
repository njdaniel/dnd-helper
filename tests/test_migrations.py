"""Migrations must target the configured database, not a hardcoded path."""

import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC = REPO_ROOT / ".venv" / "bin" / "alembic"
DEFAULT_DB = REPO_ROOT / "dnd_helper.db"

APP_TABLES = {
    "guild",
    "persona",
    "lore_entry",
    "scene",
    "scene_persona",
    "scene_message",
    "usage_log",
}


def _alembic(*args: str, database_url: str) -> subprocess.CompletedProcess[str]:
    executable = str(ALEMBIC) if ALEMBIC.exists() else shutil.which("alembic") or ""
    if not executable:
        pytest.skip("alembic is not installed in this environment")
    return subprocess.run(
        [executable, *args],
        cwd=REPO_ROOT,
        env={
            "DATABASE_URL": database_url,
            "PATH": str(Path(sys.executable).parent),
        },
        capture_output=True,
        text=True,
        check=True,
    )


def _table_names(path: Path) -> set[str]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return {row[0] for row in rows}


def test_migrations_honour_database_url(tmp_path: Path) -> None:
    """`alembic.ini` holds a placeholder; DATABASE_URL is what actually counts.

    Without the override in `env.py`, migrations run against the placeholder
    while the bot uses the configured URL — schema drift that only shows up at
    runtime, long after the migration reported success.
    """
    target = tmp_path / "campaign.db"
    default_existed = DEFAULT_DB.exists()

    _alembic("upgrade", "head", database_url=f"sqlite+aiosqlite:///{target}")

    assert target.exists(), "migration did not create the configured database"
    assert APP_TABLES <= _table_names(target)

    if not default_existed:
        assert not DEFAULT_DB.exists(), (
            "migration wrote to the placeholder path from alembic.ini "
            "instead of the configured DATABASE_URL"
        )


def test_migrations_round_trip(tmp_path: Path) -> None:
    """A migration that cannot be undone cannot be safely iterated on."""
    target = tmp_path / "campaign.db"
    url = f"sqlite+aiosqlite:///{target}"

    _alembic("upgrade", "head", database_url=url)
    _alembic("downgrade", "base", database_url=url)

    assert not (APP_TABLES & _table_names(target)), "downgrade left tables behind"

    _alembic("upgrade", "head", database_url=url)
    assert APP_TABLES <= _table_names(target)

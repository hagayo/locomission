from pathlib import Path

import pytest

from locomission.migrate import (
    DRAFT_SEED, VERSIONS, MigrationError, migration_files, migration_sql,
)


def sql_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "db"


def test_forward_only_migration_order_and_draft_opt_in():
    assert [p.name for p in migration_files(sql_dir())] == list(VERSIONS)
    assert [p.name for p in migration_files(sql_dir(), include_drafts=True)] == [
        *VERSIONS, DRAFT_SEED,
    ]


def test_legacy_scripts_are_valid_atomic_transaction_bodies():
    for file in migration_files(sql_dir(), include_drafts=True):
        sql = migration_sql(file.read_text(encoding="utf-8"))
        assert "BEGIN;" not in sql
        assert not any(line.strip() == "COMMIT;" for line in sql.splitlines())
        assert "CREATE " in sql or "INSERT " in sql or "ALTER " in sql


@pytest.mark.parametrize("invalid", [
    "CREATE TABLE x(id int);",
    "BEGIN; CREATE TABLE x(id int);",
    "COMMIT;",
    "BEGIN;\nCREATE TABLE x(id int);\nCOMMIT;\nDROP TABLE players;",
])
def test_invalid_migration_script_is_rejected(invalid):
    with pytest.raises(MigrationError):
        migration_sql(invalid)

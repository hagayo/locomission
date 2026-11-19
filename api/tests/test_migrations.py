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


@pytest.mark.skipif(
    not __import__("os").getenv("LOCOMISSION_TEST_DATABASE_URL"),
    reason="Explicit isolated PostgreSQL test URL is required",
)
def test_real_postgis_migrations_and_ledger(tmp_path):
    """Apply all versions on a disposable DB; refuse checksum drift."""
    import os
    from uuid import uuid4

    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    from locomission.migrate import run_migrations

    props = conninfo_to_dict(os.environ["LOCOMISSION_TEST_DATABASE_URL"])
    assert props.get("dbname") == "locomission_test"
    dbname = "locomission_schema_ci_" + uuid4().hex[:12]
    admin_dsn = make_conninfo(**{**props, "dbname": "postgres"})
    test_dsn = make_conninfo(**{**props, "dbname": dbname})
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
        try:
            assert run_migrations(test_dsn, sql_dir()) == [
                "pending " + name for name in VERSIONS
            ]
            assert run_migrations(test_dsn, sql_dir(), apply=True) == [
                "applied " + name for name in VERSIONS
            ]
            assert run_migrations(test_dsn, sql_dir(), apply=True) == [
                "verified " + name for name in VERSIONS
            ]
            applied = run_migrations(
                test_dsn, sql_dir(), apply=True, include_drafts=True
            )
            assert applied[-1] == "applied " + DRAFT_SEED
            with psycopg.connect(test_dsn) as db:
                statuses = db.execute(
                    "SELECT status FROM missions ORDER BY slug"
                ).fetchall()
                assert len(statuses) == 3
                assert all(status == "draft" for (status,) in statuses)
                assert db.execute(
                    "SELECT count(*) FROM schema_migrations"
                ).fetchone()[0] == 3
            copy_dir = tmp_path / "migrations"
            copy_dir.mkdir()
            for source_file in migration_files(sql_dir(), include_drafts=True):
                (copy_dir / source_file.name).write_bytes(source_file.read_bytes())
            (copy_dir / VERSIONS[0]).write_text(
                (copy_dir / VERSIONS[0]).read_text() + "\n-- modified"
            )
            with pytest.raises(MigrationError, match="changed"):
                run_migrations(test_dsn, copy_dir, include_drafts=True)
        finally:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(dbname))
            )

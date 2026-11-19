"""Forward-only migrations with checksums, a transaction and an advisory lock.

Explicit operator action only: never mutate schema during API startup.
File 003 seeds *unapproved* historical draft candidates only when opted in.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path

import psycopg


VERSIONS = ("001_initial.sql", "002_clerk_game.sql")
DRAFT_SEED = "003_historical_candidates.sql"
LOCK_ID = 1400684877958327461


class MigrationError(RuntimeError):
    """Schema state requires human action rather than an unsafe guess."""


def directory_default() -> Path:
    local = Path.cwd() / "db"
    if local.is_dir():
        return local
    bundled = Path("/app/db")
    if bundled.is_dir():
        return bundled
    return Path(__file__).resolve().parents[3] / "db"


def migration_files(directory: Path, *, include_drafts: bool = False) -> list[Path]:
    names = [*VERSIONS]
    if include_drafts:
        names.append(DRAFT_SEED)
    files = [directory / name for name in names]
    if not all(file.is_file() for file in files):
        raise MigrationError("One or more SQL migration files are missing")
    return files


def migration_sql(source: str) -> str:
    """Remove legacy BEGIN/COMMIT wrappers so the ledger shares one transaction."""
    lines = source.splitlines()
    begins = [n for n, line in enumerate(lines) if line.strip().upper() == "BEGIN;"]
    commits = [n for n, line in enumerate(lines) if line.strip().upper() == "COMMIT;"]
    if len(begins) != 1 or len(commits) != 1 or begins[0] >= commits[0]:
        raise MigrationError("Expected one BEGIN/COMMIT wrapper in SQL migration")
    if any(
        line.strip() and not line.lstrip().startswith("--")
        for line in lines[commits[0] + 1:]
    ):
        raise MigrationError("Unexpected SQL after the migration COMMIT")
    return "\n".join(lines[:begins[0]] + lines[begins[0] + 1:commits[0]])


def run_migrations(db_url: str, directory: Path, *,
                   apply: bool = False, include_drafts: bool = False) -> list[str]:
    files = migration_files(directory, include_drafts=include_drafts)
    changes = []
    with psycopg.connect(db_url, connect_timeout=10) as conn:
        with conn.transaction():
            # Only one migration operator can apply a version at a time.
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
            has_ledger = conn.execute(
                "SELECT to_regclass('public.schema_migrations') IS NOT NULL AS yes"
            ).fetchone()[0]
            if not has_ledger and apply:
                # Never assume an existing manually migrated DB is fresh.
                if conn.execute(
                    "SELECT to_regclass('public.players') IS NOT NULL AS yes"
                ).fetchone()[0]:
                    raise MigrationError(
                        "Existing schema without migration ledger. Manual DBA "
                        "baseline review required before applying migrations"
                    )
                conn.execute(
                    "CREATE TABLE schema_migrations("
                    "version text PRIMARY KEY, "
                    "checksum_sha256 text NOT NULL, "
                    "applied_at timestamptz NOT NULL DEFAULT now())"
                )
            applied = {}
            if has_ledger:
                applied = dict(conn.execute(
                    "SELECT version,checksum_sha256 FROM schema_migrations"
                ).fetchall())

            expected = {f.name for f in files}
            unknown = set(applied) - expected
            if unknown:
                if unknown == {DRAFT_SEED} and not include_drafts:
                    # The optional content seed has been applied previously.
                    expected.add(DRAFT_SEED)
                else:
                    raise MigrationError("Database contains unknown migration versions")
            for file in files:
                raw = file.read_bytes()
                digest = hashlib.sha256(raw).hexdigest()
                if file.name in applied:
                    if applied[file.name] != digest:
                        raise MigrationError("SQL changed after being applied: " + file.name)
                    changes.append("verified " + file.name)
                    continue
                sql = migration_sql(raw.decode("utf-8"))
                if apply:
                    conn.execute(sql)
                    conn.execute(
                        "INSERT INTO schema_migrations(version,checksum_sha256)"
                        " VALUES (%s,%s)",
                        (file.name, digest),
                    )
                    changes.append("applied " + file.name)
                else:
                    changes.append("pending " + file.name)
    return changes


def main() -> int:
    cli = argparse.ArgumentParser(
        description="Plan or apply LocoMission database migrations safely"
    )
    cli.add_argument("--dir", type=Path, default=directory_default())
    cli.add_argument("--apply", action="store_true",
                     help="Actually apply pending SQL; default is planning only")
    cli.add_argument("--include-drafts", action="store_true",
                     help="Seed three draft missions (never auto-approved)")
    args = cli.parse_args()
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        cli.error("DATABASE_URL must be set in the process environment")
    try:
        for line in run_migrations(
            db_url, args.dir, apply=args.apply, include_drafts=args.include_drafts
        ):
            print(line)
    except (MigrationError, psycopg.Error) as exc:
        print("Migration halted:", exc if isinstance(exc, MigrationError)
              else type(exc).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

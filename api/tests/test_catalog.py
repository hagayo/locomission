"""Pilot catalog must be safe, idempotent and human-review-only."""
from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from locomission.catalog import (
    CatalogError, import_drafts, load_catalog, validate_candidate,
)

CATALOG = Path(__file__).resolve().parents[2] / "content" / "tel-aviv-pilot-drafts.json"


def valid_entry():
    return json.loads(CATALOG.read_text(encoding="utf-8"))[0]


def test_curated_pilot_catalog_offline():
    rows = load_catalog(CATALOG)
    assert len(rows) == 5
    assert all(row.source_url.startswith("https://www.tel-aviv.gov.il/") for row in rows)
    assert all("אישור" in row.review_note and "מיקום" in row.review_note
               for row in rows)
    assert len({row.slug for row in rows}) == len(rows)


@pytest.mark.parametrize("change", [
    {"status": "approved"},
    {"reviewed_by": "Automated reviewer"},
    {"source_url": "http://example.com"},
    {"source_url": "https://localhost/test"},
    {"source_url": "https://fake.example:8080"},
    {"lat": float("nan")},
    {"lat": 91},
    {"points": True},
    {"radius_m": 0},
    {"title_he": "English only"},
    {"visual_criteria_he": ["חייבים לחצות את הכביש"]},
])
def test_reject_unapproved_or_unsafe_catalog_schema(change):
    with pytest.raises(CatalogError):
        validate_candidate({**valid_entry(), **change})


def test_invalid_json_and_duplicate_slug_fail_before_database(tmp_path):
    rows = [valid_entry(), valid_entry()]
    f = tmp_path / "candidate.json"
    f.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(CatalogError, match="Duplicate candidate slug"):
        load_catalog(f)
    f.write_text('{"slug":"x","slug":"y"}', encoding="utf-8")
    with pytest.raises(CatalogError):
        load_catalog(f)
    f.write_text('[{"lat":NaN}]', encoding="utf-8")
    with pytest.raises(CatalogError):
        load_catalog(f)


@pytest.mark.skipif(
    not os.getenv("LOCOMISSION_TEST_DATABASE_URL"),
    reason="Requires isolated PostgreSQL/PostGIS CI instance",
)
def test_atomic_import_never_modifies_edited_or_approved_missions():
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict
    from urllib.parse import urlsplit, urlunsplit

    from locomission.migrate import run_migrations

    original = os.environ["LOCOMISSION_TEST_DATABASE_URL"]
    assert conninfo_to_dict(original)["dbname"] == "locomission_test"
    parts = urlsplit(original)
    admin_url = urlunsplit(parts._replace(path="/postgres"))
    dbname = "lm_catalog_ci_" + uuid4().hex[:12]
    test_url = urlunsplit(parts._replace(path="/" + dbname))
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
        try:
            run_migrations(
                test_url, Path(__file__).resolve().parents[2] / "db",
                apply=True,
            )
            rows = load_catalog(CATALOG)
            assert import_drafts(test_url, rows) == {
                "inserted": 5, "unchanged": 0,
            }
            from locomission.pilot import check_approved_content
            assert check_approved_content(test_url) == (0, 5)
            assert import_drafts(test_url, rows) == {
                "inserted": 0, "unchanged": 5,
            }
            with psycopg.connect(test_url) as db:
                db.execute(
                    "UPDATE missions SET historical_fact_he=%s WHERE slug=%s",
                    ("ידנית נערך", rows[0].slug),
                )
                db.execute(
                    "UPDATE missions SET status='approved',reviewed_by=%s,"
                    "reviewed_at=now() WHERE slug=%s",
                    ("Human editor", rows[1].slug),
                )
            assert import_drafts(test_url, rows) == {
                "inserted": 0, "unchanged": 5,
            }
            with psycopg.connect(test_url) as db:
                edited = db.execute(
                    "SELECT historical_fact_he FROM missions WHERE slug=%s",
                    (rows[0].slug,),
                ).fetchone()[0]
                approved = db.execute(
                    "SELECT status,reviewed_by FROM missions WHERE slug=%s",
                    (rows[1].slug,),
                ).fetchone()
                total = db.execute(
                    "SELECT count(*) FROM missions"
                ).fetchone()[0]
                assert edited == "ידנית נערך"
                assert approved == ("approved", "Human editor")
                assert total == 5
            assert check_approved_content(test_url) == (1, 4)
        finally:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(dbname),
                )
            )

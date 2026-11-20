"""Bounded, offline-validated and insert-only historical draft catalog."""
from __future__ import annotations

import argparse
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import psycopg

from .domain import validate_coordinate

SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
HEBREW = re.compile(r"[\u0590-\u05FF]")
FIELDS = frozenset((
    "slug", "title_he", "historical_fact_he", "instructions_he",
    "visual_criteria_he", "source_title", "source_url", "review_note",
    "lat", "lon", "radius_m", "points",
))
MAX_CATALOG_BYTES = 2_000_000
MAX_MISSIONS = 200
CATALOG_LOCK = 1400684877958327491


class CatalogError(ValueError):
    """A rejected catalog must not modify any database entry."""


@dataclass(frozen=True)
class Candidate:
    slug: str
    title_he: str
    historical_fact_he: str
    instructions_he: str
    visual_criteria_he: list[str]
    source_title: str
    source_url: str
    review_note: str
    lat: float
    lon: float
    radius_m: int
    points: int


def required_text(raw: dict, key: str, minimum: int, maximum: int,
                  *, hebrew: bool = False) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise CatalogError("Invalid text: " + key)
    value = value.strip()
    if "\x00" in value or (hebrew and not HEBREW.search(value)):
        raise CatalogError("Invalid Hebrew text: " + key)
    return value


def require_integer(raw: dict, key: str, minimum: int, maximum: int) -> int:
    value = raw.get(key)
    if type(value) is not int or not minimum <= value <= maximum:
        raise CatalogError("Invalid integer: " + key)
    return value


def validate_candidate(raw: object) -> Candidate:
    if not isinstance(raw, dict) or set(raw) != FIELDS:
        raise CatalogError("Expected exact draft-only catalog fields")
    slug = required_text(raw, "slug", 4, 80)
    if not SLUG.fullmatch(slug):
        raise CatalogError("Invalid slug")
    url = required_text(raw, "source_url", 12, 2048)
    try:
        parts = urlsplit(url)
        if (parts.scheme != "https" or not parts.hostname or
                parts.username or parts.password or parts.fragment or
                parts.hostname in ("localhost", "127.0.0.1") or
                parts.port not in (None, 443)):
            raise CatalogError("Source must use public HTTPS")
    except ValueError as exc:
        raise CatalogError("Invalid HTTPS source URL") from exc
    lat, lon = raw.get("lat"), raw.get("lon")
    if (type(lat) not in (int, float) or type(lon) not in (int, float) or
            not math.isfinite(lat) or not math.isfinite(lon)):
        raise CatalogError("Coordinates must be finite numeric values")
    try:
        validate_coordinate(lat, lon, 0)
    except ValueError as exc:
        raise CatalogError("Invalid approximate GPS coordinate") from exc
    criteria = raw.get("visual_criteria_he")
    if not isinstance(criteria, list) or not 2 <= len(criteria) <= 6:
        raise CatalogError("Expected two to six visual criteria")
    if any(
        not isinstance(value, str) or not 8 <= len(value.strip()) <= 180
        or not HEBREW.search(value) for value in criteria
    ):
        raise CatalogError("Invalid Hebrew visual criterion")
    if len({s.strip() for s in criteria}) != len(criteria):
        raise CatalogError("Duplicate criterion")
    note = required_text(raw, "review_note", 40, 1000, hebrew=True)
    if "אישור" not in note or "מיקום" not in note:
        raise CatalogError("Review note must flag unapproved, unverified location")
    return Candidate(
        slug=slug,
        title_he=required_text(raw, "title_he", 4, 120, hebrew=True),
        historical_fact_he=required_text(
            raw, "historical_fact_he", 50, 1200, hebrew=True,
        ),
        instructions_he=required_text(
            raw, "instructions_he", 25, 600, hebrew=True,
        ),
        visual_criteria_he=[s.strip() for s in criteria],
        source_title=required_text(raw, "source_title", 5, 200),
        source_url=url, review_note=note,
        lat=float(lat), lon=float(lon),
        radius_m=require_integer(raw, "radius_m", 10, 10000),
        points=require_integer(raw, "points", 1, 10000),
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise CatalogError("Duplicate JSON field")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise CatalogError("Non-finite JSON numeric value")


def load_catalog(path: Path) -> list[Candidate]:
    if not path.is_file() or path.stat().st_size > MAX_CATALOG_BYTES:
        raise CatalogError("Missing or oversized JSON catalog")
    try:
        raw = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CatalogError("Invalid UTF-8 JSON") from exc
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_MISSIONS:
        raise CatalogError("Expected bounded nonempty candidate list")
    result = [validate_candidate(entry) for entry in raw]
    if len({row.slug for row in result}) != len(result):
        raise CatalogError("Duplicate candidate slug")
    return result


def import_drafts(database_url: str, rows: list[Candidate]) -> dict[str, int]:
    """Atomic insert-only operation; never overwrite existing or approved rows."""
    inserted = 0
    with psycopg.connect(database_url) as conn:
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (CATALOG_LOCK,))
            for row in rows:
                result = conn.execute(
                    "INSERT INTO missions "
                    "(slug,title_he,historical_fact_he,instructions_he,"
                    "visual_criteria_he,source_title,source_url,review_note,"
                    "status,location,radius_m,points) VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,%s,'draft',"
                    "ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography,%s,%s)"
                    " ON CONFLICT (slug) DO NOTHING RETURNING slug",
                    (
                        row.slug, row.title_he, row.historical_fact_he,
                        row.instructions_he, row.visual_criteria_he,
                        row.source_title, row.source_url, row.review_note,
                        row.lon, row.lat, row.radius_m, row.points,
                    ),
                ).fetchone()
                inserted += int(result is not None)
    return {"inserted": inserted, "unchanged": len(rows) - inserted}


def main() -> int:
    cli = argparse.ArgumentParser(description="Import DRAFT history candidates")
    cli.add_argument("--file", required=True, type=Path)
    cli.add_argument("--apply", action="store_true")
    args = cli.parse_args()
    try:
        rows = load_catalog(args.file)
        if not args.apply:
            print("Validated", len(rows), "draft candidates. No database writes.")
            return 0
        url = os.getenv("DATABASE_URL", "")
        if not url:
            raise CatalogError("DATABASE_URL required for --apply")
        result = import_drafts(url, rows)
        print("Drafts:", result["inserted"], "inserted,",
              result["unchanged"], "existing and untouched")
        return 0
    except (CatalogError, psycopg.Error) as exc:
        detail = str(exc) if isinstance(exc, CatalogError) else type(exc).__name__
        print("Import blocked:", detail)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

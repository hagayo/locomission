"""Local editorial review; publication requires a human and four attestations."""
from __future__ import annotations

import argparse
import os
from urllib.parse import urlparse

import psycopg
from psycopg.rows import dict_row

from .domain import validate_coordinate


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LocoMission historical mission editor")
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("drafts", help="Display unpublished historical candidates")
    approve = actions.add_parser("approve", help="Approve an independently reviewed draft")
    approve.add_argument("slug")
    approve.add_argument("--reviewer", required=True)
    approve.add_argument("--lat", type=float, required=True)
    approve.add_argument("--lon", type=float, required=True)
    approve.add_argument("--radius-m", type=int, required=True)
    for flag in ("source-reviewed", "hebrew-reviewed",
                 "safe-photo-site-reviewed", "visual-criteria-reviewed"):
        approve.add_argument("--" + flag, action="store_true")
    return parser


def require_review(args: argparse.Namespace) -> None:
    if not 2 <= len(args.reviewer.strip()) <= 100:
        raise ValueError("Reviewer name is required")
    if not 10 <= args.radius_m <= 10000:
        raise ValueError("Invalid geofence radius")
    validate_coordinate(args.lat, args.lon, 0)
    if not all((
        args.source_reviewed, args.hebrew_reviewed,
        args.safe_photo_site_reviewed, args.visual_criteria_reviewed,
    )):
        raise ValueError("All four human review confirmations are required")


def run(args: argparse.Namespace) -> int:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise ValueError("DATABASE_URL missing")
    if args.action == "approve":
        require_review(args)
    with psycopg.connect(url, row_factory=dict_row) as db:
        if args.action == "drafts":
            drafts = db.execute(
                "SELECT slug,title_he,source_title,source_url,review_note "
                "FROM missions WHERE status='draft' ORDER BY slug"
            ).fetchall()
            for d in drafts:
                print(d["slug"], "|", d["title_he"], "|", d["source_title"],
                      "|", d["source_url"], "|", d["review_note"])
            return 0
        with db.transaction():
            draft = db.execute(
                "SELECT source_url,visual_criteria_he FROM missions "
                "WHERE slug=%s AND status='draft' FOR UPDATE",
                (args.slug,),
            ).fetchone()
            if not draft:
                raise ValueError("Mission is not an unpublished draft")
            source = urlparse(draft["source_url"])
            if source.scheme != "https" or not source.hostname:
                raise ValueError("Historical source must have an HTTPS URL")
            if not draft["visual_criteria_he"]:
                raise ValueError("Visual criteria missing")
            saved = db.execute(
                "UPDATE missions SET "
                "location=ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography, "
                "radius_m=%s,reviewed_by=%s,reviewed_at=now(),"
                "status='approved',review_note='Human editor approved' "
                "WHERE slug=%s AND status='draft' RETURNING id",
                (args.lon, args.lat, args.radius_m,
                 args.reviewer.strip(), args.slug),
            ).fetchone()
            if not saved:
                raise ValueError("Approval failed")
    print("Approved:", args.slug)
    return 0


def main() -> int:
    args = make_parser().parse_args()
    try:
        return run(args)
    except (ValueError, psycopg.Error) as exc:
        print("Editorial operation rejected:", type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

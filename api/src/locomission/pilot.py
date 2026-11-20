"""Operator-driven pilot readiness gate. No self-approval or secret logging.

The sample decision file is intentionally BLOCKED. Only humans can provide
evidence; an automated check can enforce completeness, not legal correctness.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import psycopg

REQUIRED = (
    "editorial_source_and_field_review",
    "safe_public_photography",
    "age_gate_and_minors_policy",
    "clerk_real_tenant_smoke",
    "openai_real_provider_smoke",
    "privacy_notice_and_legal_review",
    "processor_and_data_retention_review",
    "physical_deletion_and_backups",
    "on_device_gps_and_camera_permissions",
    "security_cost_monitoring_and_restore",
)


class ReadinessError(ValueError):
    pass


def review_decisions(data: object) -> list[str]:
    if not isinstance(data, dict):
        raise ReadinessError("Decision file must be an object")
    if set(data) != {"decisions", "max_physical_erasure_seconds"}:
        raise ReadinessError("Unexpected readiness decision schema")
    decisions = data["decisions"]
    if not isinstance(decisions, dict) or set(decisions) != set(REQUIRED):
        raise ReadinessError("Missing or unknown release gate IDs")
    blocked = []
    for key in REQUIRED:
        answer = decisions[key]
        if (not isinstance(answer, dict)
                or set(answer) != {"approved", "reviewer", "evidence"}):
            raise ReadinessError("Invalid gate evidence schema: " + key)
        if answer["approved"] is not True:
            blocked.append(key)
            continue
        reviewer = answer["reviewer"]
        evidence = answer["evidence"]
        if (not isinstance(reviewer, str) or len(reviewer.strip()) < 3
                or not isinstance(evidence, str)
                or len(evidence.strip()) < 12
                or any(value in evidence.lower() for value in (
                    "todo", "tbd", "placeholder", "replace", "example",
                ))):
            blocked.append(key)
    x = data["max_physical_erasure_seconds"]
    # X must be operator-defined based on real infrastructure and processor
    # evidence. The application supplies no default or inferred number.
    if type(x) is not int or not 1 <= x <= 31_536_000:
        blocked.append("physical_erasure_SLA_X_not_decided")
    return blocked


def check_approved_content(database_url: str) -> tuple[int, int]:
    """Read-only, no coordinates, player data, or sensitive identifiers."""
    with psycopg.connect(database_url, connect_timeout=10) as conn:
        approved = conn.execute(
            "SELECT count(*) FROM missions WHERE status='approved'"
            " AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL"
            " AND content_tier='free'"
        ).fetchone()[0]
        drafts = conn.execute(
            "SELECT count(*) FROM missions WHERE status='draft'"
        ).fetchone()[0]
        return approved, drafts


def main() -> int:
    cli = argparse.ArgumentParser(description="Audit pilot release blockers")
    cli.add_argument("--decisions", required=True, type=Path)
    cli.add_argument("--check-db", action="store_true",
                     help="Read anonymous approved/draft mission counts")
    args = cli.parse_args()
    try:
        if not args.decisions.is_file() or args.decisions.stat().st_size > 250_000:
            raise ReadinessError("Missing or oversized decision file")
        data = json.loads(args.decisions.read_text(encoding="utf-8"))
        blocked = review_decisions(data)
        if args.check_db:
            url = os.getenv("DATABASE_URL")
            if not url:
                blocked.append("database_url_missing")
            else:
                approved, drafts = check_approved_content(url)
                print("Mission inventory:", approved, "approved,", drafts, "drafts")
                if approved < 3:
                    blocked.append("fewer_than_three_approved_missions")
        else:
            blocked.append("live_database_not_checked")
        if blocked:
            print("PILOT BLOCKED. Evidence/review still needed:")
            for value in blocked:
                print(" -", value)
            return 2
        print("Technical release checklist complete. Human launch approval required.")
        return 0
    except (OSError, UnicodeError, json.JSONDecodeError,
            ReadinessError, psycopg.Error) as exc:
        print("PILOT BLOCKED: invalid or inaccessible release evidence")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

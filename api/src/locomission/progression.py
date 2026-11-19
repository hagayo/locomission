"""Small MVP achievements computed from durable facts, no redundant ledger."""
from __future__ import annotations

from typing import Any

AVATAR_IDS = ("explorer", "compass", "torch", "map", "star")


def achievements(profile: dict[str, Any], groups_joined: int) -> list[dict[str, Any]]:
    completed = profile["completed_missions"]
    steps = (
        ("first", "משימה ראשונה", completed, 1),
        ("five", "חוקר מסלולים", completed, 5),
        ("twenty", "מגלה ארצות", completed, 20),
        ("team", "שחקן קבוצתי", groups_joined, 1),
    )
    return [
        {"id": key, "title_he": title, "current": min(current, goal),
         "goal": goal, "unlocked": current >= goal}
        for key, title, current, goal in steps
    ]

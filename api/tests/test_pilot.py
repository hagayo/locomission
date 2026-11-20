import copy

import pytest

from locomission.pilot import REQUIRED, ReadinessError, review_decisions


def blocked_file():
    from pathlib import Path
    import json
    source = Path(__file__).resolve().parents[2] / "docs" / "PILOT-GATES.example.json"
    return json.loads(source.read_text(encoding="utf-8"))


def test_release_sample_is_explicitly_blocked():
    outcome = review_decisions(blocked_file())
    assert len(outcome) == len(REQUIRED) + 1
    assert "physical_erasure_SLA_X_not_decided" in outcome


def test_no_fake_or_incomplete_attestation_can_pass():
    data = blocked_file()
    for name in REQUIRED:
        data["decisions"][name] = {
            "approved": True, "reviewer": "Sample reviewer",
            "evidence": "https://evidence.invalid/sample-review-123",
        }
    assert review_decisions(data) == ["physical_erasure_SLA_X_not_decided"]
    data["max_physical_erasure_seconds"] = 7200
    assert review_decisions(data) == []
    data["decisions"][REQUIRED[0]]["evidence"] = "TBD"
    assert REQUIRED[0] in review_decisions(data)
    data["decisions"][REQUIRED[0]]["evidence"] = ""
    assert REQUIRED[0] in review_decisions(data)
    data["max_physical_erasure_seconds"] = None
    assert "physical_erasure_SLA_X_not_decided" in review_decisions(data)


@pytest.mark.parametrize("change", [
    {"max_physical_erasure_seconds": True},
    {"max_physical_erasure_seconds": 0},
    {"max_physical_erasure_seconds": -30},
    {"max_physical_erasure_seconds": "unknown"},
])
def test_x_cannot_be_inferred(change):
    data = {**blocked_file(), **change}
    assert "physical_erasure_SLA_X_not_decided" in review_decisions(data)


def test_missing_gate_schema_fails_closed():
    data = copy.deepcopy(blocked_file())
    data["decisions"].pop(REQUIRED[1])
    with pytest.raises(ReadinessError):
        review_decisions(data)

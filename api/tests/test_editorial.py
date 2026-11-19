import pytest
from locomission.editorial import make_parser, require_review


def review_args():
    return [
        "approve", "jaffa-clock-tower",
        "--reviewer", "Test Reviewer",
        "--lat", "32.05", "--lon", "34.76", "--radius-m", "250",
        "--source-reviewed", "--hebrew-reviewed",
        "--safe-photo-site-reviewed", "--visual-criteria-reviewed",
    ]


def test_all_editorial_attestations_required():
    parser = make_parser()
    require_review(parser.parse_args(review_args()))
    for flag in (
        "--source-reviewed", "--hebrew-reviewed",
        "--safe-photo-site-reviewed", "--visual-criteria-reviewed",
    ):
        with pytest.raises(ValueError):
            require_review(parser.parse_args([x for x in review_args() if x != flag]))


def test_invalid_coordinates_rejected():
    args = review_args()
    args[5] = "91"
    with pytest.raises(ValueError):
        require_review(make_parser().parse_args(args))


def test_anonymous_editor_rejected():
    args = review_args()
    args[3] = " "
    with pytest.raises(ValueError):
        require_review(make_parser().parse_args(args))

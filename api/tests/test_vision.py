import io

import pytest
from PIL import Image

from locomission.vision import InvalidPhoto, VisionUnavailable, grade_photo, normalize_photo


def photo_bytes():
    im = Image.new("RGB", (60, 40), (30, 80, 120))
    out = io.BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


def test_normalization_reencodes_and_removes_metadata():
    data = normalize_photo(photo_bytes())
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "JPEG"
        assert image.size == (60, 40)
        assert not image.getexif()


def test_invalid_and_oversize_images_rejected():
    with pytest.raises(InvalidPhoto):
        normalize_photo(b"junk")
    with pytest.raises(InvalidPhoto):
        normalize_photo(b"x" * (5 * 1024 * 1024 + 1))


def test_no_model_call_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(VisionUnavailable):
        grade_photo(b"sample", "title", "instructions", ["criterion"])

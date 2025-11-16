from pathlib import Path
from urllib.parse import urlparse
import sys

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.ui import extract_ck_parameters, PostSelectionDialog


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://coomer.st/patreon/user/123/post/456", ("patreon", "123", "456")),
        ("https://kemono.su/fanbox/user/789", ("fanbox", "789", None)),
    ],
)
def test_extract_ck_parameters(url, expected):
    parsed = urlparse(url)
    assert extract_ck_parameters(parsed) == expected

def test_apply_sort_and_filters_by_counts():
    entries = [
        {"post_id": "1", "metrics": {"attachments": 5, "images": 2, "videos": 0}, "order": 0},
        {"post_id": "2", "metrics": {"attachments": 2, "images": 1, "videos": 1}, "order": 1},
        {"post_id": "3", "metrics": {"attachments": 1, "images": 0, "videos": 1}, "order": 2},
    ]

    filters = {"attachments": 2, "images": None, "videos": None}
    ordered = PostSelectionDialog.apply_sort_and_filters(entries, filters, "attachments", True)

    assert [entry["post_id"] for entry in ordered] == ["1", "2"]


def test_apply_sort_and_filters_respects_sort_direction():
    entries = [
        {"post_id": "1", "metrics": {"attachments": 2, "images": 1, "videos": 0}, "order": 0},
        {"post_id": "2", "metrics": {"attachments": 3, "images": 3, "videos": 2}, "order": 1},
        {"post_id": "3", "metrics": {"attachments": 3, "images": 0, "videos": 1}, "order": 2},
    ]

    filters = {"attachments": 3, "images": None, "videos": None}
    ordered = PostSelectionDialog.apply_sort_and_filters(entries, filters, "images", False)

    assert [entry["post_id"] for entry in ordered] == ["3", "2"]
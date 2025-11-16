from pathlib import Path
from urllib.parse import urlparse
import sys

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.ui import extract_ck_parameters


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
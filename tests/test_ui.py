import sys
import threading
from pathlib import Path
from urllib.parse import urlparse
from unittest.mock import Mock

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.ui import extract_ck_parameters, PostSelectionDialog, ImageDownloaderApp


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

def test_close_program_cancels_threads_and_flushes_partial_records():
    class DummyDownloader:
        def __init__(self):
            self.cancel_event = threading.Event()
            self.partial_downloads = {"foo": {"tmp_path": "foo.tmp"}}
            self.shutdown_called = False
            self.cancelled = False
            self.worker_started = threading.Event()
            self.worker_finished = threading.Event()

        def worker(self):
            self.worker_started.set()
            self.cancel_event.wait()
            self.worker_finished.set()

        def request_cancel(self):
            self.cancelled = True
            self.cancel_event.set()

        def shutdown_executor(self):
            self.shutdown_called = True
            self.partial_downloads.clear()

    dummy_downloader = DummyDownloader()

    app = ImageDownloaderApp.__new__(ImageDownloaderApp)
    app.destroy = Mock()
    app.active_downloader = dummy_downloader
    app._managed_downloaders = {dummy_downloader}
    app._download_threads = set()
    app._managed_downloaders_lock = threading.Lock()
    app._download_threads_lock = threading.Lock()

    worker_thread = threading.Thread(target=dummy_downloader.worker)
    app._download_threads.add(worker_thread)
    worker_thread.start()
    assert dummy_downloader.worker_started.wait(timeout=1)

    app.close_program(wait_timeout=1)

    assert dummy_downloader.cancelled
    assert dummy_downloader.shutdown_called
    assert dummy_downloader.partial_downloads == {}
    assert dummy_downloader.worker_finished.is_set()
    app.destroy.assert_called_once()

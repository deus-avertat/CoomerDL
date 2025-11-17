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


def test_apply_sort_and_filters_by_video_size_and_duration():
    entries = [
        {
            "post_id": "1",
            "metrics": {"largest_video_size": 1024, "longest_video_duration": 60, "videos": 1},
            "order": 0,
        },
        {
            "post_id": "2",
            "metrics": {"largest_video_size": 50_000, "longest_video_duration": 10, "videos": 2},
            "order": 1,
        },
        {
            "post_id": "3",
            "metrics": {"largest_video_size": 2048, "longest_video_duration": 300, "videos": 1},
            "order": 2,
        },
    ]

    ordered_by_size = PostSelectionDialog.apply_sort_and_filters(entries, {}, "largest_video_size", True)
    assert [entry["post_id"] for entry in ordered_by_size] == ["2", "3", "1"]

    ordered_by_duration = PostSelectionDialog.apply_sort_and_filters(entries, {}, "longest_video_duration", True)
    assert [entry["post_id"] for entry in ordered_by_duration] == ["3", "1", "2"]


def test_video_metrics_detected_from_metadata_and_strings():
    dialog = PostSelectionDialog.__new__(PostSelectionDialog)

    post = {
        "attachments": [
            {
                "name": "clip",  # No extension
                "metadata": {"type": "video/mp4", "size": "1048576", "duration": "1:02"},
            },
            {
                "path": "video-two",  # No extension but mimetype provided
                "mimetype": "video/webm",
                "metadata": {"bytes": 2048, "length": 10},
            },
        ]
    }

    metrics = dialog._calculate_media_counts(post)

    assert metrics["videos"] == 2
    assert metrics["images"] == 0
    assert metrics["largest_video_size"] == 1_048_576
    assert metrics["longest_video_duration"] == 62


def test_video_metrics_detect_human_readable_sizes_and_nested_durations():
    dialog = PostSelectionDialog.__new__(PostSelectionDialog)

    post = {
        "attachments": [
            {
                "name": "clip-one.mp4",
                "size": "1.5 MB",
                "metadata": {"duration": {"seconds": "90"}},
            },
            {
                "name": "clip-two.mp4",
                "metadata": {"size": {"bytes": 4096}, "length": {"value": "45"}},
            },
        ]
    }

    metrics = dialog._calculate_media_counts(post)

    assert metrics["videos"] == 2
    assert metrics["largest_video_size"] == pytest.approx(1.5 * 1024 * 1024)
    assert metrics["longest_video_duration"] == 90


def test_video_metrics_fetch_remote_headers(monkeypatch):
    dialog = PostSelectionDialog.__new__(PostSelectionDialog)
    dialog._media_base = "https://coomer.st"
    dialog._head_cache = {}
    dialog._log_callback = None

    class DummyResponse:
        def __init__(self, headers, status_code=200):
            self.headers = headers
            self.status_code = status_code

        def close(self):
            pass

    def fake_head(url, **kwargs):
        return DummyResponse({"Content-Length": "2048"})

    monkeypatch.setattr("app.ui.requests.head", fake_head)
    monkeypatch.setattr("app.ui.requests.get", lambda *args, **kwargs: DummyResponse({}, status_code=404))

    entry = {"path": "/data/clip.mp4"}
    detail = dialog._extract_video_detail(entry, 1)

    assert detail["size_bytes"] == 2048
    assert detail["duration_seconds"] is None


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

import tempfile
import threading
import unittest
from unittest import mock

import requests

from downloader.downloader import Downloader


class SafeRequestRetryTests(unittest.TestCase):
    def _create_downloader(self, tmpdir, log_callback):
        with mock.patch.object(Downloader, "init_db"), \
                mock.patch.object(Downloader, "load_download_cache"), \
                mock.patch.object(Downloader, "load_partial_downloads"):
            downloader = Downloader(
                tmpdir,
                max_retries=1,
                retry_interval=0,
                log_callback=log_callback,
                tr=lambda text, **kwargs: text,
            )

        downloader.download_cache = {}
        downloader.partial_downloads = {}
        downloader.rate_limit_interval = 0
        self.addCleanup(downloader.shutdown_executor)
        return downloader

    def test_safe_request_retries_on_rate_limit_errors(self):
        log_messages = []

        with tempfile.TemporaryDirectory() as tmpdir:
            downloader = self._create_downloader(tmpdir, log_messages.append)

            url = "https://example.com/resource"
            response = requests.Response()
            response.status_code = 429
            response.url = url
            prepared_request = requests.Request("GET", url).prepare()

            def raise_http_error(*args, **kwargs):
                raise requests.exceptions.HTTPError(response=response, request=prepared_request)

            session_mock = mock.Mock()
            session_mock.get.side_effect = raise_http_error
            session_mock.close = mock.Mock()
            downloader._session_local.session = session_mock
            downloader._sessions.add(session_mock)

            result = downloader.safe_request(url, max_retries=1)

            self.assertIsNone(result)
            self.assertEqual(session_mock.get.call_count, 2)
            self.assertTrue(any("Error 429 - Retrying" in msg for msg in log_messages))

    def test_safe_request_uses_isolated_sessions_per_thread(self):
        log_messages = []

        class DummyResponse:
            def __init__(self, url):
                self.status_code = 200
                self.url = url

            def raise_for_status(self):
                return None

        call_sessions = []
        call_lock = threading.Lock()

        with tempfile.TemporaryDirectory() as tmpdir:
            downloader = self._create_downloader(tmpdir, log_messages.append)
            downloader.max_retries = 0
            urls = [f"https://example.com/{i}" for i in range(downloader.max_workers)]
            start_barrier = threading.Barrier(downloader.max_workers)

            class DummySession:
                def __init__(self):
                    self.closed = False

                def get(self, url, stream=True, headers=None, timeout=None):
                    start_barrier.wait(timeout=5)
                    with call_lock:
                        call_sessions.append(self)
                    return DummyResponse(url)

                def close(self):
                    self.closed = True

            with mock.patch("downloader.downloader.requests.Session", DummySession):
                futures = [downloader.executor.submit(downloader.safe_request, url) for url in urls]

                for future in futures:
                    response = future.result(timeout=5)
                    self.assertIsNotNone(response)
                    self.assertEqual(response.status_code, 200)

        unique_sessions = {id(session) for session in call_sessions}
        expected_sessions = len(urls)
        self.assertGreaterEqual(len(unique_sessions), expected_sessions)


if __name__ == "__main__":
    unittest.main()
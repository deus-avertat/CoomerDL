import tempfile
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
        self.addCleanup(downloader.executor.shutdown)
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

            downloader.session.get = mock.Mock(side_effect=raise_http_error)

            result = downloader.safe_request(url, max_retries=1)

            self.assertIsNone(result)
            self.assertEqual(downloader.session.get.call_count, 2)
            self.assertTrue(any("Error 429 - Retrying" in msg for msg in log_messages))


if __name__ == "__main__":
    unittest.main()
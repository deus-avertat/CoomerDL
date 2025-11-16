import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import requests

from downloader.downloader import Downloader


class DownloaderRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.config_dir = os.path.join(self.temp_dir, "config")
        self.downloader = Downloader(
            download_folder=self.temp_dir,
            max_workers=1,
            max_retries=2,
            retry_interval=0,
            config_dir=self.config_dir,
        )
        self.downloader.log = lambda *args, **kwargs: None
        self.downloader.tr = lambda message, **kwargs: message.format(**kwargs) if kwargs else message
        self.downloader.update_progress_callback = None
        self.downloader.update_global_progress_callback = None
        self.downloader.rate_limit_interval = 0

    def tearDown(self):
        try:
            self.downloader.shutdown_executor()
        finally:
            if hasattr(self.downloader, "db_connection"):
                self.downloader.db_connection.close()
            shutil.rmtree(self.temp_dir)

    def test_safe_request_respects_retry_limit(self):
        response = requests.Response()
        response.status_code = 503
        error = requests.exceptions.HTTPError(response=response)
        self.downloader.session.get = MagicMock(side_effect=error)

        result = self.downloader.safe_request("https://example.com/file.jpg")

        self.assertIsNone(result)
        self.assertEqual(
            self.downloader.session.get.call_count,
            self.downloader.max_retries + 1,
        )

    def test_process_media_element_stops_after_configured_attempts(self):
        media_url = "https://example.com/file.jpg"

        with patch.object(self.downloader, "safe_request", return_value=None) as mock_safe_request:
            self.downloader.process_media_element(media_url, "user123", post_id="post1")

        self.assertEqual(mock_safe_request.call_count, self.downloader.max_retries + 1)
        self.assertIn(media_url, self.downloader.failed_files)


if __name__ == "__main__":
    unittest.main()
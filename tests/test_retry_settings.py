import unittest
from unittest import mock

from downloader.downloader import Downloader
from app.settings_window import SettingsWindow


class DummyWidget:
    def __init__(self, value):
        self._value = value

    def get(self):
        return str(self._value)


class FakeDownloader:
    def __init__(self):
        self.max_retries = 3
        self.retry_interval = 2.0
        self.rate_limit_interval = 5.0
        self.file_naming_mode = 0
        self.updated_max_downloads = None

    def set_retry_settings(self, max_retries, retry_interval):
        Downloader.set_retry_settings(self, max_retries, retry_interval)

    def update_max_downloads(self, new_max):
        self.updated_max_downloads = new_max


class RetrySettingsTests(unittest.TestCase):
    def test_set_retry_settings_does_not_touch_rate_limit(self):
        downloader = Downloader(
            download_folder="resources/downloads",
            max_workers=1,
            rate_limit_interval=1.5,
            retry_interval=2.5,
            max_retries=2,
        )
        downloader.tr = lambda message, **kwargs: message
        try:
            downloader.set_retry_settings(10, 3.0)

            self.assertEqual(downloader.max_retries, 10)
            self.assertEqual(downloader.retry_interval, 3.0)
            self.assertEqual(downloader.rate_limit_interval, 1.5)
        finally:
            downloader.shutdown_executor()
            downloader.db_connection.close()

    def test_apply_download_settings_updates_retry_interval(self):
        downloader = FakeDownloader()

        settings_window = SettingsWindow.__new__(SettingsWindow)
        settings_window.translate = lambda text: text
        settings_window.settings = {}
        settings_window.save_settings = lambda: None
        settings_window.downloader = downloader

        widgets = {
            "max_downloads": DummyWidget("4"),
            "folder_structure": DummyWidget("by_service"),
            "max_retries": DummyWidget("5"),
            "retry_interval": DummyWidget("1.5"),
            "file_naming_mode": DummyWidget("Use File ID (default)"),
        }

        with mock.patch("app.settings_window.messagebox.showinfo"), \
                mock.patch("app.settings_window.messagebox.showerror"):
            settings_window.apply_download_settings(
                widgets["max_downloads"],
                widgets["folder_structure"],
                widgets["max_retries"],
                widgets["retry_interval"],
                widgets["file_naming_mode"],
            )

        self.assertEqual(settings_window.settings["retry_interval"], 1.5)
        self.assertEqual(settings_window.settings["max_retries"], 5)
        self.assertEqual(downloader.retry_interval, 1.5)
        self.assertEqual(downloader.max_retries, 5)
        self.assertEqual(downloader.rate_limit_interval, 5.0)
        self.assertEqual(downloader.updated_max_downloads, 4)


if __name__ == "__main__":
    unittest.main()
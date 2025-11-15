import os
import json
import re
import queue
import threading
import time
from pathlib import Path
from bs4 import BeautifulSoup
from urllib.parse import urlparse
import cloudscraper
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from concurrent.futures import ThreadPoolExecutor

class SimpCity:
    def __init__(self, download_folder, max_workers=5, log_callback=None, enable_widgets_callback=None, update_progress_callback=None, update_global_progress_callback=None, tr=None, request_timeout=20):
        self.download_folder = download_folder
        self.max_workers = max_workers
        self.descargadas = set()
        self.log_callback = log_callback
        self.enable_widgets_callback = enable_widgets_callback
        self.update_progress_callback = update_progress_callback
        self.update_global_progress_callback = update_global_progress_callback
        self.cancel_event = threading.Event()
        self.pause_event = threading.Event()
        self.pause_event.set()
        self.state_lock = threading.Lock()
        self._is_paused = False
        self.cancel_requested = False
        self.total_files = 0
        self.completed_files = 0
        self.download_queue = queue.Queue()
        self.scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False})
        self.tr = tr

        # Selectors from original crawler
        self.title_selector = "h1[class=p-title-value]"
        self.posts_selector = "div[class*=message-main]"
        self.post_content_selector = "div[class*=message-userContent]"
        self.images_selector = "img[class*=bbImage]"
        self.videos_selector = "video source"
        self.iframe_selector = "iframe[class=saint-iframe]"
        self.attachments_block_selector = "section[class=message-attachments]"
        self.attachments_selector = "a"
        self.next_page_selector = "a[class*=pageNav-jump--next]"
        try:
            self.request_timeout = float(request_timeout)
        except (TypeError, ValueError):
            self.request_timeout = 20.0
        if self.request_timeout <= 0:
            self.request_timeout = 0.1

    def log(self, message):
        if self.log_callback:
            self.log_callback(message)

    def wait_if_paused(self):
        while not self.pause_event.is_set():
            if self.cancel_event.is_set():
                return False
            time.sleep(0.1)
        return True

    def request_pause(self):
        if self.cancel_event.is_set():
            return
        with self.state_lock:
            if self._is_paused:
                return
            self._is_paused = True
        self.pause_event.clear()
        self.log(self.tr("Descarga en pausa"))

    def request_resume(self):
        with self.state_lock:
            if not self._is_paused:
                return
            self._is_paused = False
        self.pause_event.set()
        self.log(self.tr("Descarga reanudada"))

    def request_cancel(self):
        self.cancel_requested = True
        self.pause_event.set()
        with self.state_lock:
            self._is_paused = False
        self.log(self.tr("Descarga cancelada por el usuario."))
        if self.enable_widgets_callback:
            self.enable_widgets_callback()

    @property
    def is_paused(self):
        with self.state_lock:
            return self._is_paused

    @property
    def cancel_requested(self):
        return self.cancel_event.is_set()

    @cancel_requested.setter
    def cancel_requested(self, value):
        if value:
            self.cancel_event.set()
        else:
            self.cancel_event.clear()

    def sanitize_folder_name(self, name):
        return re.sub(r'[<>:"/\\|?*]', '_', name)

    def get_cookies_with_selenium(self, url, cookies_file='resources/config/cookies.json'):
        cookies = None
        if os.path.exists(cookies_file):
            with open(cookies_file, 'r') as file:
                cookies = json.load(file)

        if not cookies:
            options = Options()
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            driver = webdriver.Chrome(options=options)
            driver.get(url)

            self.log(self.tr("Por favor, inicia sesión en el navegador abierto."))
            WebDriverWait(driver, 300).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, '.message-content.js-messageContent'))
            )
            cookies = driver.get_cookies()
            driver.quit()

            with open(cookies_file, 'w') as file:
                json.dump(cookies, file)

        return cookies

    def set_cookies_in_scraper(self, cookies):
        for cookie in cookies:
            self.scraper.cookies.set(cookie['name'], cookie['value'])

    def fetch_page(self, url):
        if self.cancel_requested or not self.wait_if_paused():
            return None
        try:
            cookies = self.get_cookies_with_selenium(url)
            self.set_cookies_in_scraper(cookies)
            response = self.scraper.get(url, timeout=self.request_timeout)
            if response.status_code == 200:
                return BeautifulSoup(response.content, 'html.parser')
            else:
                self.log(self.tr(f"Error: {response.status_code} al acceder a {url}"))
                return None
        except Exception as e:
            self.log(self.tr(f"Error al acceder a {url}: {e}"))
            return None

    def save_file(self, file_url, path):
        if self.cancel_requested or not self.wait_if_paused():
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        response = self.scraper.get(file_url, stream=True, timeout=self.request_timeout)
        if response.status_code == 200:
            with open(path, 'wb') as file:
                for chunk in response.iter_content(1024):
                    if self.cancel_requested or not self.wait_if_paused():
                        file.close()
                        if os.path.exists(path):
                            os.remove(path)
                        return
                    file.write(chunk)
            self.log(self.tr(f"Archivo descargado: {path}"))
        else:
            self.log(self.tr(f"Error al descargar {file_url}: {response.status_code}"))

    def process_post(self, post_content, download_folder):
        if self.cancel_requested or not self.wait_if_paused():
            return
        # Procesar imágenes
        images = post_content.select(self.images_selector)
        for img in images:
            if self.cancel_requested or not self.wait_if_paused():
                return
            src = img.get('src')
            if src:
                file_name = os.path.basename(urlparse(src).path)
                file_path = os.path.join(download_folder, file_name)
                self.save_file(src, file_path)

        # Procesar videos
        videos = post_content.select(self.videos_selector)
        for video in videos:
            if self.cancel_requested or not self.wait_if_paused():
                return
            src = video.get('src')
            if src:
                file_name = os.path.basename(urlparse(src).path)
                file_path = os.path.join(download_folder, file_name)
                self.save_file(src, file_path)

        # Procesar archivos adjuntos
        attachments_block = post_content.select_one(self.attachments_block_selector)
        if attachments_block:
            attachments = attachments_block.select(self.attachments_selector)
            for attachment in attachments:
                if self.cancel_requested or not self.wait_if_paused():
                    return
                href = attachment.get('href')
                if href:
                    file_name = os.path.basename(urlparse(href).path)
                    file_path = os.path.join(download_folder, file_name)
                    self.save_file(href, file_path)

    def process_page(self, url):
        if self.cancel_requested or not self.wait_if_paused():
            return
        soup = self.fetch_page(url)
        if not soup:
            return

        title_element = soup.select_one(self.title_selector)
        folder_name = self.sanitize_folder_name(title_element.text.strip()) if title_element else 'SimpCity_Download'
        download_folder = os.path.join(self.download_folder, folder_name)
        os.makedirs(download_folder, exist_ok=True)

        message_inners = soup.select(self.posts_selector)
        for post in message_inners:
            if self.cancel_requested or not self.wait_if_paused():
                return
            post_content = post.select_one(self.post_content_selector)
            if post_content:
                self.process_post(post_content, download_folder)

        next_page = soup.select_one(self.next_page_selector)
        if next_page:
            next_page_url = next_page.get('href')
            if next_page_url:
                if self.cancel_requested or not self.wait_if_paused():
                    return
                self.process_page(self.base_url + next_page_url)

    def download_images_from_simpcity(self, url):
        if self.cancel_requested or not self.wait_if_paused():
            return
        self.log(self.tr(f"Procesando hilo: {url}"))
        self.process_page(url)
        self.log(self.tr("Descarga completada."))

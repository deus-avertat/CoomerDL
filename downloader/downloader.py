from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Semaphore
from urllib.parse import quote_plus, urlencode, urljoin, urlparse
import os
import re
import requests
import threading
import time
import sqlite3

class Downloader:
	def __init__(self, download_folder, max_workers=5, log_callback=None,
				 enable_widgets_callback=None, update_progress_callback=None,
				 update_global_progress_callback=None, headers=None,
				 max_retries=5, retry_interval=1.0, stream_read_timeout=20,
				 download_images=True, download_videos=True, download_compressed=True,
				 tr=None, folder_structure='default', rate_limit_interval=1.0,
				 config_dir=None):
		
		self.download_folder = download_folder
		self.log_callback = log_callback
		self.enable_widgets_callback = enable_widgets_callback
		self.update_progress_callback = update_progress_callback
		self.update_global_progress_callback = update_global_progress_callback
		self.cancel_requested = threading.Event()
		self.pause_event = threading.Event()
		self.pause_event.set()
		self.state_lock = threading.Lock()
		self._is_paused = False
		self.headers = headers or {
			'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)',
			'Referer': 'https://coomer.st/',
			"Accept": "text/css"
		}
		self.media_counter = 0
		self.session = requests.Session()
		self.max_workers = max_workers  
		self.executor = ThreadPoolExecutor(max_workers=self.max_workers)
		self.rate_limit = Semaphore(self.max_workers)  
		self.domain_locks = defaultdict(lambda: Semaphore(self.max_workers))
		self.domain_last_request = defaultdict(float)  
		self.rate_limit_interval = rate_limit_interval
		self.download_mode = "multi"  
		self.video_extensions = ('.mp4', '.mkv', '.webm', '.mov', '.avi', '.flv', '.wmv', '.m4v')
		self.image_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff')
		self.document_extensions = ('.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx')
		self.compressed_extensions = ('.zip', '.rar', '.7z', '.tar', '.gz')
		self.download_images = download_images
		self.download_videos = download_videos
		self.download_compressed = download_compressed
		self.futures = []  
		self.total_files = 0
		self.completed_files = 0
		self.skipped_files = []  
		self.failed_files = []
		self.start_time = None
		self.tr = tr
		self.shutdown_called = False  
		self.folder_structure = folder_structure
		self.failed_retry_count = {}
		self.max_retries = max_retries
		self.retry_interval = retry_interval
		self.file_lock = threading.Lock()
		self.post_attachment_counter = defaultdict(int)
		self.subdomain_cache = {}
		self.subdomain_locks = defaultdict(threading.Lock)
		self.stream_read_timeout = stream_read_timeout
		self.partial_update_interval = 5.0

		self.config_dir = config_dir or os.path.join("resources", "config")
		os.makedirs(self.config_dir, exist_ok=True)
		self.db_path = os.path.join(self.config_dir, "downloads.db")
		self.db_lock = threading.Lock()  
		self.init_db()
		self.load_download_cache()
		self.load_partial_downloads()
		

	def init_db(self):
		self.db_connection = sqlite3.connect(self.db_path, check_same_thread=False)
		self.db_cursor = self.db_connection.cursor()
		self.db_cursor.execute("""
			CREATE TABLE IF NOT EXISTS downloads (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				media_url TEXT UNIQUE,
				file_path TEXT,
				file_size INTEGER,
				user_id TEXT,
				post_id TEXT,
				downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
			)
		""")
		self.db_cursor.execute("""
							   CREATE TABLE IF NOT EXISTS partial_downloads
							   (
								   media_url       TEXT PRIMARY KEY,
								   tmp_path        TEXT,
								   downloaded_size INTEGER,
								   total_size      INTEGER,
								   user_id         TEXT,
								   post_id         TEXT,
								   updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
							   )
							   """)
		self.db_connection.commit()

	def load_download_cache(self):
		with self.db_lock:
			self.db_cursor.execute("SELECT media_url, file_path, file_size FROM downloads")
			rows = self.db_cursor.fetchall()
		self.download_cache = {row[0]: (row[1], row[2]) for row in rows}

	def load_partial_downloads(self):
		with self.db_lock:
			self.db_cursor.execute(
				"SELECT media_url, tmp_path, downloaded_size, total_size, user_id, post_id FROM partial_downloads")
			rows = self.db_cursor.fetchall()

		self.partial_downloads = {}
		stale_entries = []
		for media_url, tmp_path, downloaded_size, total_size, user_id, post_id in rows:
			if tmp_path and os.path.exists(tmp_path):
				self.partial_downloads[media_url] = {
					"tmp_path": tmp_path,
					"downloaded_size": downloaded_size or 0,
					"total_size": total_size or 0,
					"user_id": user_id,
					"post_id": post_id,
				}
			else:
				stale_entries.append(media_url)

		if stale_entries:
			with self.db_lock:
				self.db_cursor.executemany("DELETE FROM partial_downloads WHERE media_url = ?",
										   [(url,) for url in stale_entries])
				self.db_connection.commit()

	def update_partial_download(self, media_url, tmp_path, downloaded_size, total_size, user_id, post_id):
		if not media_url or not tmp_path:
			return

		stored_total_size = total_size if total_size else None
		with self.db_lock:
			self.db_cursor.execute("""
								   INSERT INTO partial_downloads (media_url, tmp_path, downloaded_size, total_size,
																  user_id, post_id, updated_at)
								   VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
								   ON CONFLICT(media_url) DO UPDATE SET tmp_path        = excluded.tmp_path,
																		downloaded_size = excluded.downloaded_size,
																		total_size      = excluded.total_size,
																		user_id         = excluded.user_id,
																		post_id         = excluded.post_id,
																		updated_at      = CURRENT_TIMESTAMP
								   """,
								   (media_url, tmp_path, downloaded_size, stored_total_size, user_id, post_id)
								   )
		self.db_connection.commit()

		self.partial_downloads[media_url] = {
			"tmp_path": tmp_path,
			"downloaded_size": downloaded_size,
			"total_size": total_size or 0,
			"user_id": user_id,
			"post_id": post_id,
		}

	def remove_partial_download(self, media_url):
		if not media_url:
			return
		with self.db_lock:
			self.db_cursor.execute("DELETE FROM partial_downloads WHERE media_url = ?", (media_url,))
			self.db_connection.commit()
		self.partial_downloads.pop(media_url, None)

	def log(self, message):
		if self.log_callback:
			self.log_callback(self.tr(message) if self.tr else message)

	def wait_if_paused(self):
		while not self.pause_event.is_set():
			if self.cancel_requested.is_set():
				return False
			time.sleep(0.1)
		return True

	def request_pause(self):
		if self.cancel_requested.is_set():
			return
		with self.state_lock:
			if self._is_paused:
				return
			self._is_paused = True
		self.pause_event.clear()
		self.log(self.tr("Download paused."))

	def request_resume(self):
		with self.state_lock:
			if not self._is_paused:
				return
			self._is_paused = False
		self.pause_event.set()
		self.log(self.tr("Download resumed."))

	@property
	def is_paused(self):
		with self.state_lock:
			return self._is_paused

	def set_download_mode(self, mode, max_workers):
		
		if mode == 'queue':
			max_workers = 1  
		
		self.download_mode = mode
		self.max_workers = max_workers

		
		if self.executor:
			self.executor.shutdown(wait=True)

		
		self.executor = ThreadPoolExecutor(max_workers=max_workers)
		self.rate_limit = Semaphore(max_workers)

		self.log(f"Updated download mode to {mode} with max_workers = {max_workers}")
	
	def set_retry_settings(self, max_retries, retry_interval):
		try:
			max_retries = int(max_retries)
		except (TypeError, ValueError):
			max_retries = self.max_retries
		self.max_retries = max(0, max_retries)
		self.retry_interval = retry_interval

	def request_cancel(self):
		self.cancel_requested.set()
		self.pause_event.set()
		with self.state_lock:
			self._is_paused = False
		self.log(self.tr("Download cancellation requested."))
		for future in self.futures:
			future.cancel()

	def shutdown_executor(self):
		if not self.shutdown_called:
			self.shutdown_called = True
			self.pause_event.set()
			with self.state_lock:
				self._is_paused = False
			if self.executor:
				self.executor.shutdown(wait=True)
			if self.enable_widgets_callback:
				self.enable_widgets_callback()
			self.log(self.tr("All downloads completed or cancelled."))

	def _log_retry(self, attempt, max_retries, template, **kwargs):
		template_text = self.tr(template) if self.tr else template
		message = template_text.format(
			attempt=attempt + 1,
			max_retries_val=max_retries + 1,
			**kwargs,
		)
		self.log(message)

	def safe_request(self, url, max_retries=None, headers=None):
		if max_retries is None:
			max_retries = self.max_retries
		if headers is None:
			headers = self.headers

		parsed = urlparse(url)
		domain = parsed.netloc
		path = parsed.path

		for attempt in range(max_retries + 1):
			if self.cancel_requested.is_set():
				return None

			if not self.wait_if_paused():
				return None

			with self.domain_locks[domain]:
				elapsed_time = time.time() - self.domain_last_request[domain]
				if elapsed_time < self.rate_limit_interval:
					time.sleep(self.rate_limit_interval - elapsed_time)

				try:
					self.domain_last_request[domain] = time.time()
					
					response = self.session.get(url, stream=True, headers=headers, timeout=self.stream_read_timeout)
					sc = response.status_code

					if sc in (403, 404) and ("coomer" in domain or "kemono" in domain):
						if self.update_progress_callback:
							self.update_progress_callback(0, 0, status=f"{sc} - probing subdomains")

						with self.subdomain_locks[path]:
							if path in self.subdomain_cache:
								alt_url = self.subdomain_cache[path]
							else:
								alt_url = self._find_valid_subdomain(url)
								self.subdomain_cache[path] = alt_url

						if alt_url != url:
							found = urlparse(alt_url).netloc
							if self.update_progress_callback:
								self.update_progress_callback(0, 0, status=f"Subdomain found: {found}")

							
							response = self.session.get(alt_url, stream=True, headers=headers)
							response.raise_for_status()
							return response
						else:
							if self.update_progress_callback:
								self.update_progress_callback(0, 0, status="Exhausted subdomains")
							return None

					response.raise_for_status()
					return response

				except requests.exceptions.RequestException as e:
					status_code = getattr(e.response, 'status_code', None)

					if isinstance(e, requests.exceptions.ReadTimeout):
						self._log_retry(
							attempt,
							max_retries,
							"Intento {attempt}/{max_retries_val}: Read timeout ({stream_timeout}s) - Reintentando...",
							stream_timeout=self.stream_read_timeout,
						)
						if attempt < max_retries:
							time.sleep(self.retry_interval)
						continue

					if status_code in (429, 500, 502, 503, 504):
						self._log_retry(
							attempt,
							max_retries,
							"Attempt {attempt}/{max_retries_val}: Error {status_code} - Retrying...",
							status_code=status_code,
						)
						if attempt < max_retries:
							time.sleep(self.retry_interval)
					elif status_code not in (403, 404):
						url_display = getattr(e.request, 'url', url)
						if len(url_display) > 60:
							url_display = url_display[:60] + "..."
						self._log_retry(
							attempt,
							max_retries,
							"Attempt {attempt}/{max_retries_val}: Error accessing {url} - {error}",
							url=url_display,
							error=e,
						)
						if attempt < max_retries:
							time.sleep(self.retry_interval)
					else:
						self._log_retry(
							attempt,
							max_retries,
							"Attempt {attempt}/{max_retries_val}: Error {status_code} - Retrying...",
							status_code=status_code,
						)
						
					if status_code in (403, 404) and ("coomer" in domain or "kemono" in domain) and attempt == max_retries:
						self.log(self.tr("Fallo final al acceder a {url} con error {status_code}").format(url=url, status_code=status_code))


		return None

	def _find_valid_subdomain(self, url, max_subdomains=10):
		parsed = urlparse(url)
		original_path = parsed.path
		
		path = original_path
		if not original_path.startswith("/data/"):
			path = ("/data" + original_path) if not original_path.startswith("/data") else original_path

		host = parsed.netloc
		
		if "coomer" in host:
			base_domains = ["coomer.st"]
		elif "kemono" in host:
			
			base_domains = ["kemono.cr", "kemono.su"]
		else:
			base_domains = [host]

		for base in base_domains:
			for i in range(1, max_subdomains + 1):
				domain = f"n{i}.{base}"
				test_url = parsed._replace(netloc=domain, path=path).geturl()

				if self.update_progress_callback:
					self.update_progress_callback(0, 0, status=f"Testing subdomain: {domain}")

				try:
					resp = self.session.get(test_url, headers=self.headers,
						timeout=self.stream_read_timeout, stream=True)
					if resp.status_code == 200:
						return test_url
					else:
						if self.update_progress_callback:
							self.update_progress_callback(0, 0, status=f"Invalid subdomain: {domain}")
				except requests.exceptions.ReadTimeout:
					if self.update_progress_callback:
						self.update_progress_callback(0, 0, status=f"Timeout in: {domain}")
				except Exception:
					if self.update_progress_callback:
						self.update_progress_callback(0, 0, status=f"Invalid subdomain: {domain}")

		return url

	def fetch_user_posts(self, site, user_id, service, query=None, specific_post_id=None, initial_offset=0, log_fetching=True):
		all_posts = []
		offset = initial_offset
		user_id_encoded = quote_plus(user_id)
		while True:
			if self.cancel_requested.is_set():
				return all_posts

			api_url = f"https://{site}/api/v1/{service}/user/{user_id_encoded}/posts"
			url_query = {"o": offset}
			if query is not None:
				url_query["q"] = query
			api_url += "?" + urlencode(url_query)
			if log_fetching:
				self.log(self.tr("Fetching user posts from {api_url}", api_url=api_url))
			try:
				
				response = self.session.get(api_url, headers=self.headers)
				response.raise_for_status()
				try:
					posts_data = response.json()
				except ValueError as e:
					self.log(self.tr("Error al parsear JSON: {e}", e=e))
					break
				
				if isinstance(posts_data, dict) and 'data' in posts_data:
					posts = posts_data['data']
				else:
					posts = posts_data
				if not posts:
					break
				if specific_post_id:
					post = next((p for p in posts if p['id'] == specific_post_id), None)
					if post:
						return [post]
				all_posts.extend(posts)
				offset += 50
			except Exception as e:
				self.log(self.tr("Error fetching user posts: {e}", e=e))
				break
		if specific_post_id:
			return [post for post in all_posts if post['id'] == specific_post_id]
		return all_posts


	def get_filename(self, media_url, post_id=None, post_name=None, attachment_index=1, post_time=None):
		base_name = os.path.basename(media_url).split('?')[0]
		name_no_ext, extension = os.path.splitext(base_name)
		if not hasattr(self, 'file_naming_mode'):
			self.file_naming_mode = 0
		mode = self.file_naming_mode

		def sanitize(name):
			
			sanitized = self.sanitize_filename(name)
			return sanitized.strip()

		if mode == 0:
			
			sanitized = sanitize(name_no_ext)
			if not sanitized:
				sanitized = "file"  
			final_name = f"{sanitized}_{attachment_index}{extension}"
		elif mode == 1:
			
			sanitized_post = sanitize(post_name or "")
			if not sanitized_post:
				sanitized_post = f"post_{post_id}" if post_id else "post"
			short_hash = f"{hash(media_url) & 0xFFFF:04x}"
			final_name = f"{sanitized_post}_{attachment_index}_{short_hash}{extension}"
		elif mode == 2:
			
			sanitized_post = sanitize(post_name or "")
			if not sanitized_post:
				sanitized_post = f"post_{post_id}" if post_id else "post"
			if post_id:
				final_name = f"{sanitized_post} - {post_id}_{attachment_index}{extension}"
			else:
				final_name = f"{sanitized_post}_{attachment_index}{extension}"
		elif mode == 3:
			
			sanitized_post = sanitize(post_name or "")
			if not sanitized_post:
				sanitized_post = f"post_{post_id}" if post_id else "post"
			sanitized_time = sanitize(post_time or "")
			short_hash = f"{hash(media_url) & 0xFFFF:04x}"
			final_name = f"{sanitized_time} - {sanitized_post}_{attachment_index}_{short_hash}{extension}"
		else:
			final_name = sanitize(name_no_ext) + extension

		return final_name



	def process_post(self, post, site):
		base = f"https://{site}/"

		def _full(path):
			if not path:
				return None
			p = path if str(path).startswith('/') else f'/{path}'
			return urljoin(base, p)

		media_urls = []

		f = post.get('file') or {}
		u = _full(f.get('path') or f.get('url') or f.get('name'))
		if u:
			media_urls.append(u)

		for att in (post.get('attachments') or []):
			u = _full(att.get('path') or att.get('url') or att.get('name'))
			if u:
				media_urls.append(u)

		return media_urls

	def sanitize_filename(self, filename):
		return re.sub(r'[<>:"/\\|?*]', '_', filename)

	def get_media_folder(self, extension, user_id, post_id=None):
		if extension in self.video_extensions:
			folder_name = "videos"
		elif extension in self.image_extensions:
			folder_name = "images"
		elif extension in self.document_extensions:
			folder_name = "documents"
		elif extension in self.compressed_extensions:
			folder_name = "compressed"
		else:
			folder_name = "other"
		if self.folder_structure == 'post_number' and post_id:
			media_folder = os.path.join(self.download_folder, user_id, f'post_{post_id}', folder_name)
		else:
			media_folder = os.path.join(self.download_folder, user_id, folder_name)
		return media_folder

	def process_media_element(self, media_url, user_id, post_id=None,
						  post_name=None, post_time=None, download_id=None):
		
		
		if self.cancel_requested.is_set():
			return

		if not self.wait_if_paused():
			return

		extension = os.path.splitext(media_url)[1].lower()
		if (extension in self.image_extensions and not self.download_images) or \
		(extension in self.video_extensions and not self.download_videos) or \
		(extension in self.compressed_extensions and not self.download_compressed):
			self.log(f"Skipping {media_url} due to settings.")
			return

		
		if post_id:
			self.post_attachment_counter[post_id] += 1
			attachment_index = self.post_attachment_counter[post_id]
		else:
			attachment_index = 1

		filename = self.get_filename(media_url, post_id=post_id, post_name=post_name, post_time=post_time,
									attachment_index=attachment_index)
		media_folder = self.get_media_folder(extension, user_id, post_id)
		os.makedirs(media_folder, exist_ok=True)

		final_path = os.path.normpath(os.path.join(media_folder, filename))
		tmp_path = final_path + ".tmp"
		partial_info = self.partial_downloads.get(media_url)
		downloaded_size = 0
		total_size = 0
		if partial_info:
			stored_tmp_path = partial_info.get("tmp_path")
			if stored_tmp_path and stored_tmp_path != tmp_path and os.path.exists(
					stored_tmp_path) and not os.path.exists(tmp_path):
				try:
					os.makedirs(os.path.dirname(tmp_path), exist_ok=True)
					os.rename(stored_tmp_path, tmp_path)
				except OSError:
					pass
			total_size = partial_info.get("total_size", 0) or 0

		if os.path.exists(tmp_path):
			downloaded_size = os.path.getsize(tmp_path)
			if downloaded_size > 0:
				self.log(f"Found existing partial file ({downloaded_size} bytes) for {media_url}")
			if partial_info and partial_info.get("downloaded_size") and partial_info.get(
					"downloaded_size") > downloaded_size:
				self.log("Stored progress is ahead of file size; using on-disk bytes instead.")

		self.update_partial_download(media_url, tmp_path, downloaded_size, total_size, user_id, post_id)
		
		if media_url in self.download_cache:
			self.log(f"File from {media_url} is in DB, skipping.")
			with self.file_lock:
				self.skipped_files.append(final_path)
				self.remove_partial_download(media_url)
			return

		self.log(f"Starting download from {media_url}")

		for attempt in range(self.max_retries + 1):
			if self.cancel_requested.is_set():
				if os.path.exists(tmp_path):
					os.remove(tmp_path)
					self.remove_partial_download(media_url)
				self.log(f"Download cancelled from {media_url}")
				return

			resume_from = downloaded_size if os.path.exists(tmp_path) else 0
			request_headers = self.headers.copy()
			if resume_from > 0:
				request_headers['Range'] = f'bytes={resume_from}-'
				self.log(f"Attempting resume at byte {resume_from} for {media_url}")

			response = self.safe_request(media_url, max_retries=self.max_retries, headers=request_headers)
			
			if response is None:
				if attempt < self.max_retries:
					self.log(f"Initial request failed for {media_url}. Resuming download in {self.retry_interval}s. (Attempt {attempt+1}/{self.max_retries + 1})")
					time.sleep(self.retry_interval)
					continue 
				else:
					break

			try:
				if resume_from > 0 and response.status_code == 200 and 'content-range' not in response.headers:
					self.log(f"Range header ignored for {media_url}; restarting from beginning.")
					resume_from = 0
					downloaded_size = 0

				attempt_start_downloaded = downloaded_size
				self.start_time = time.time()

				file_mode = 'ab' if resume_from > 0 else 'wb'

				content_length = response.headers.get('content-length')
				if 'content-range' in response.headers:
					match = re.search(r'/([0-9]+)$', response.headers['content-range'])
					if match:
						total_size = int(match.group(1))
				elif content_length:
					try:
						length_value = int(content_length)
					except (TypeError, ValueError):
						length_value = 0
					if length_value:
						if downloaded_size > 0:
							total_size = max(total_size, downloaded_size + length_value)
						else:
							total_size = length_value

				self.update_partial_download(media_url, tmp_path, downloaded_size, total_size, user_id, post_id)
				last_partial_update = time.time()

				with open(tmp_path, file_mode) as f:
					for chunk in response.iter_content(chunk_size=1048576):
						if self.cancel_requested.is_set():
							raise Exception("Cancellation Requested")
						if not self.wait_if_paused():
							raise Exception("Cancellation Requested")
						if chunk:
							f.write(chunk)
							downloaded_size += len(chunk)
							if time.time() - last_partial_update >= self.partial_update_interval:
								self.update_partial_download(media_url, tmp_path, downloaded_size, total_size, user_id, post_id)
								last_partial_update = time.time()
							if self.update_progress_callback:
								elapsed_time = time.time() - self.start_time
								bytes_this_attempt = downloaded_size - attempt_start_downloaded
								speed = bytes_this_attempt / elapsed_time if elapsed_time > 0 else 0
								remaining_bytes = (total_size - downloaded_size) if total_size else 0
								remaining_time = (remaining_bytes / speed) if speed > 0 and remaining_bytes > 0 else 0
								self.update_progress_callback(downloaded_size, total_size,
															file_id=download_id,
															file_path=tmp_path,
															speed=speed,
															eta=remaining_time)

				while total_size and downloaded_size < total_size:

					if not self.wait_if_paused():
						raise Exception("Cancellation Requested")
					resume_headers = self.headers.copy()
					resume_headers['Range'] = f'bytes={downloaded_size}-'
					self.log(f"Resuming download at byte {downloaded_size} for {media_url}")
					
					part_response = self.safe_request(media_url, max_retries=self.max_retries, headers=resume_headers)

					if part_response is None:
						raise Exception("Resumption Failed after retries")

					with open(tmp_path, 'ab') as f:
						for chunk in part_response.iter_content(chunk_size=1048576):
							if self.cancel_requested.is_set():
								raise Exception("Cancellation Requested")
							if not self.wait_if_paused():
								raise Exception("Cancellation Requested")
							if chunk:
								f.write(chunk)
								downloaded_size += len(chunk)
							if time.time() - last_partial_update >= self.partial_update_interval:
								self.update_partial_download(media_url, tmp_path, downloaded_size, total_size, user_id, post_id)
								last_partial_update = time.time()
								if self.update_progress_callback:
									elapsed_time = time.time() - self.start_time
									bytes_this_attempt = downloaded_size - attempt_start_downloaded
									speed = bytes_this_attempt / elapsed_time if elapsed_time > 0 else 0
									remaining_bytes = (total_size - downloaded_size) if total_size else 0
									remaining_time = (remaining_bytes / speed) if speed > 0 and remaining_bytes > 0 else 0
									self.update_progress_callback(downloaded_size, total_size,
																file_id=download_id,
																file_path=tmp_path,
																speed=speed,
																eta=remaining_time)

				
				if total_size > 0 and downloaded_size != total_size:
					raise Exception(f"Final size mismatch: expected {total_size}, got {downloaded_size}")

				
				with self.file_lock:
					if os.path.exists(final_path):
						os.remove(final_path)
					os.rename(tmp_path, final_path)
				self.remove_partial_download(media_url)

				
				with self.file_lock:
					self.completed_files += 1
				
				self.log(f"Download success from {media_url}")
				if self.update_global_progress_callback:
					self.update_global_progress_callback(self.completed_files, self.total_files)

				
				with self.db_lock:
					self.db_cursor.execute(
						"""INSERT OR REPLACE INTO downloads (media_url, file_path, file_size, user_id, post_id)
						VALUES (?, ?, ?, ?, ?)""",
						(media_url, final_path, total_size, user_id, post_id)
					)
					self.db_connection.commit()

				self.download_cache[media_url] = (final_path, total_size)
				return
			except Exception as e:
				
				if str(e) == "Cancellation Requested":
					if os.path.exists(tmp_path):
						os.remove(tmp_path)
					self.remove_partial_download(media_url)
					self.log(f"Download cancelled from {media_url}")
					return 

				if attempt < self.max_retries:
					time.sleep(self.retry_interval)
					continue

		self.log(f"Failed to download {media_url} after {self.max_retries + 1} total download attempts.")
		self.update_partial_download(media_url, tmp_path, downloaded_size, total_size, user_id, post_id)
		with self.file_lock:
			self.failed_files.append(media_url)


	def get_remote_file_size(self, media_url, filename):
		try:
			response = requests.head(media_url, allow_redirects=True)
			if response.status_code == 200:
				size = int(response.headers.get('Content-Length', 0))
				return media_url, filename, size
			else:
				self.log(self.tr(f"Failed to get size for {filename}: HTTP {response.status_code}"))
				return media_url, filename, None
		except Exception as e:
			self.log(self.tr(f"Error getting size for {filename}: {e}"))
			return media_url, filename, None

	def download_media(self, site, user_id, service, query=None, download_all=False, initial_offset=0, selected_post_ids=None):
		try:
			self.log(self.tr("Starting download process..."))

			log_fetching = download_all or bool(selected_post_ids)
			posts = self.fetch_user_posts(
				site, user_id, service,
				query=query,
				initial_offset=initial_offset,
				log_fetching=log_fetching
			)
			if not posts:
				self.log(self.tr("No posts found for this user."))
				return

			if selected_post_ids is not None:
				selected_set = set(selected_post_ids)
				posts = [post for post in posts if post.get('id') in selected_set]
				if not posts:
					self.log(self.tr("No valid posts were selected for download."))
					return
			elif not download_all:
				
				posts = posts[:50]

			self.total_files = 0
			for post in posts:

				if not self.wait_if_paused():
					return

				current_post_id = post.get('id') or "unknown_id"
				
				title = post.get('title') or ""

				
				media_urls = self.process_post(post, site)


				for media_url in media_urls:
					ext = os.path.splitext(media_url)[1].lower()
					if (ext in self.image_extensions and not self.download_images) or \
					   (ext in self.video_extensions and not self.download_videos) or \
					   (ext in self.compressed_extensions and not self.download_compressed):
						continue

					
					self.total_files += 1

			
			futures = []
			for post in posts:
				if not self.wait_if_paused():
					return
				current_post_id = post.get('id') or "unknown_id"
				title = post.get('title') or ""
				time = post.get('published') or ""

				media_urls = self.process_post(post, site)
				for media_url in media_urls:
					ext = os.path.splitext(media_url)[1].lower()
					if (ext in self.image_extensions and not self.download_images) or \
					   (ext in self.video_extensions and not self.download_videos) or \
					   (ext in self.compressed_extensions and not self.download_compressed):
						continue

					
					if self.download_mode == 'queue':
						
						self.process_media_element(
							media_url,
							user_id,
							post_id=current_post_id,
							post_name=title,
							post_time=time
						)
					else:
						
						future = self.executor.submit(
							self.process_media_element,
							media_url,
							user_id,
							current_post_id,
							title,  
							time,
							media_url 
						)
						futures.append(future)

			
			if self.download_mode == 'multi':
				for future in as_completed(futures):
					if self.cancel_requested.is_set():
						break

		except Exception as e:
			self.log(self.tr(f"Error during download: {e}"))
		finally:
			self.shutdown_executor()

	def download_single_post(self, site, post_id, service, user_id):
		try:
			post = self.fetch_user_posts(site, user_id, service, specific_post_id=post_id)
			if not post:
				self.log(self.tr("No post found for this ID."))
				return
			media_urls = self.process_post(post[0], site)
			futures = []
			grouped_media_urls = defaultdict(list)
			for media_url in media_urls:
				grouped_media_urls[post[0]['id']].append(media_url)
			self.total_files = len(media_urls)
			self.completed_files = 0
			for post_id, media_urls in grouped_media_urls.items():
				if not self.wait_if_paused():
					return
				for media_url in media_urls:
					if self.download_mode == 'queue':
						self.process_media_element(media_url, user_id, post_id)
					else:
						future = self.executor.submit(self.process_media_element, media_url, user_id, post_id)
						futures.append(future)
			if self.download_mode == 'multi':
				for future in as_completed(futures):
					if self.cancel_requested.is_set():
						break
		except Exception as e:
			self.log(self.tr(f"Error during download: {e}"))
		finally:
			self.shutdown_executor()
	
	def fetch_single_post(self, site, post_id, service):
		api_url = f"https://{site}/api/v1/{service}/post/{post_id}"
		self.log(self.tr(f"Fetching post from {api_url}"))
		try:
			with self.rate_limit:
				response = self.session.get(api_url, headers=self.headers)
			response.raise_for_status()
			return response.json()
		except Exception as e:
			self.log(self.tr(f"Error fetching post: {e}"))
			return None

	def clear_database(self):
		
		with self.db_lock:
			self.db_cursor.execute("DELETE FROM downloads")
			self.db_connection.commit()
		self.log(self.tr("Database cleared."))
	
	def update_max_downloads(self, new_max):
		
		
		if self.executor:
			self.executor.shutdown(wait=True)

		self.max_workers = new_max
		self.executor = ThreadPoolExecutor(max_workers=new_max)
		self.rate_limit = Semaphore(new_max)

		self.log(f"Updated max_workers to {new_max}")

import datetime
import copy
import json
import queue
import sys
import re
import os
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext
from typing import Optional
from urllib.parse import ParseResult, parse_qs, urlparse
import webbrowser
import requests
from PIL import Image
import customtkinter as ctk
from PIL import Image, ImageTk
import functools
import subprocess

#from app.patch_notes import PatchNotes
from app.settings_window import SettingsWindow
#from app.user_panel import UserPanel
from app.about_window import AboutWindow
from downloader.bunkr import BunkrDownloader
from downloader.downloader import Downloader
from downloader.erome import EromeDownloader
from downloader.simpcity import SimpCity, SIMPCITY_COOKIES_FILE
from downloader.jpg5 import Jpg5Downloader
from app.progress_manager import ProgressManager
from app.donors import DonorsModal

VERSION = "V0.8.17"
MAX_LOG_LINES = None

IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".bmp",
    ".tiff",
}

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".webm",
    ".mov",
    ".avi",
    ".flv",
    ".wmv",
    ".m4v",
}

class PostSelectionDialog(ctk.CTkToplevel):
    def __init__(self, parent, posts, tr, user_id, service, site, log_callback=None):
        super().__init__(parent)
        self.title(tr("Select posts"))
        self.geometry("600x800")
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.on_cancel)

        self._tr = tr
        self._checkbox_vars = {}
        self._confirmed = False
        self._result = []
        self._post_dates = {}
        self._available_years = []
        self._post_search_texts = {}
        self._post_entries = []
        self._no_results_label = None
        self._size_formatter = functools.partial(self._format_bytes, precision=1)
        self._log_callback = log_callback
        self._site = site
        self._media_base = f"https://{site}/" if site else None
        self._head_cache = {}

        years_set = set()

        header_text = tr(
            "{count} posts found for {service} / {user} on {site}",
            count=len(posts),
            service=service,
            user=user_id,
            site=site,
        )
        header_label = ctk.CTkLabel(self, text=header_text, wraplength=500, justify="left")
        header_label.pack(padx=20, pady=(20, 10), anchor="w")

        self.scrollable = ctk.CTkScrollableFrame(self)
        self.scrollable.pack(fill="both", expand=True, padx=20, pady=(0, 10))

        for post in posts:
            post_id = post.get("id", "")
            title = post.get("title") or tr("Untitled")
            published = post.get("published") or ""
            if isinstance(published, str) and published:
                published_display = published.replace("T", " ")
            else:
                published_display = ""

            parsed_date = self._parse_published_date(published)
            self._post_dates[post_id] = parsed_date
            if parsed_date is not None:
                years_set.add(parsed_date.year)

            search_fragments = [str(post_id), title or ""]
            content = post.get("content")
            if isinstance(content, str):
                search_fragments.append(content)
            tags = post.get("tags")
            if isinstance(tags, (list, tuple)):
                search_fragments.extend(str(tag) for tag in tags if tag)
            self._post_search_texts[post_id] = " ".join(filter(None, search_fragments)).lower()

            display_text = f"{title} (ID: {post_id})"
            if published_display:
                display_text += f"\n{tr('Published')}: {published_display}"

            metrics = self._calculate_media_counts(post)
            metrics_text = tr(
                "Images: {images} | Videos: {videos} | Attachments: {attachments}",
                images=metrics["images"],
                videos=metrics["videos"],
                attachments=metrics["attachments"],
            )
            display_text += f"\n{metrics_text}"

            video_details = metrics.get("video_details") or []
            if video_details:
                video_lines = []
                for idx, detail in enumerate(video_details, start=1):
                    parts = []
                    size_bytes = detail.get("size_bytes")
                    duration_seconds = detail.get("duration_seconds")

                    if size_bytes is not None:
                        parts.append(self._size_formatter(size_bytes))
                    if duration_seconds is not None:
                        parts.append(self._format_duration(int(duration_seconds)))

                    label = detail.get("name") or f"{tr('Video')} {idx}"
                    if parts:
                        label = f"{label}: {', '.join(parts)}"
                    video_lines.append(label)

                display_text += f"\n{tr('Video details:')}\n" + "\n".join(video_lines)

            var = tk.BooleanVar(value=True)
            checkbox = ctk.CTkCheckBox(
                self.scrollable,
                text=display_text,
                variable=var,
                onvalue=True,
                offvalue=False,
                width=480,
            )
            self._checkbox_vars[post_id] = var
            entry = {
                "post_id": post_id,
                "checkbox": checkbox,
                "metrics": metrics,
                "order": len(self._post_entries),
            }
            self._post_entries.append(entry)

        controls_frame = ctk.CTkFrame(self)
        controls_frame.pack(fill="x", padx=20, pady=(0, 10))

        select_all_button = ctk.CTkButton(
            controls_frame,
            text=tr("Select all"),
            command=self.select_all,
        )
        select_all_button.pack(side="left", padx=(0, 10))

        deselect_all_button = ctk.CTkButton(
            controls_frame,
            text=tr("Deselect all"),
            command=self.deselect_all,
        )
        deselect_all_button.pack(side="left")

        metric_filter_frame = ctk.CTkFrame(self)
        metric_filter_frame.pack(fill="x", padx=20, pady=(0, 10))

        sort_label = ctk.CTkLabel(metric_filter_frame, text=tr("Sort by"))
        sort_label.grid(row=0, column=0, padx=(0, 10), pady=(10, 5), sticky="w")

        self._sort_option_map = {
            tr("Original order"): ("order", False),
            tr("Attachments (High to Low)"): ("attachments", True),
            tr("Attachments (Low to High)"): ("attachments", False),
            tr("Images (High to Low)"): ("images", True),
            tr("Images (Low to High)"): ("images", False),
            tr("Videos (High to Low)"): ("videos", True),
            tr("Videos (Low to High)"): ("videos", False),
            tr("Video size (High to Low)"): ("largest_video_size", True),
            tr("Video size (Low to High)"): ("largest_video_size", False),
            tr("Video length (Long to Short)"): ("longest_video_duration", True),
            tr("Video length (Short to Long)"): ("longest_video_duration", False),
        }
        sort_values = list(self._sort_option_map.keys())
        self.metric_sort_combobox = ctk.CTkComboBox(
            metric_filter_frame,
            values=sort_values,
            command=self.on_sort_selection,
            width=200,
        )
        self.metric_sort_combobox.set(sort_values[0])
        self.metric_sort_combobox.grid(row=0, column=1, padx=(0, 10), pady=(10, 5), sticky="w")

        clear_metrics_button = ctk.CTkButton(
            metric_filter_frame,
            text=tr("Clear count filters"),
            command=self.clear_metric_filters,
            width=160,
        )
        clear_metrics_button.grid(row=0, column=2, padx=(0, 10), pady=(10, 5), sticky="ew")

        self.min_attachments_var = tk.StringVar()
        self.min_images_var = tk.StringVar()
        self.min_videos_var = tk.StringVar()

        metric_inputs = [
            (tr("Min attachments"), self.min_attachments_var, 1),
            (tr("Min images"), self.min_images_var, 2),
            (tr("Min videos"), self.min_videos_var, 3),
        ]

        for label_text, var, row in metric_inputs:
            label = ctk.CTkLabel(metric_filter_frame, text=label_text)
            label.grid(row=row, column=0, padx=(0, 10), pady=5, sticky="w")
            entry = ctk.CTkEntry(metric_filter_frame, textvariable=var, width=140)
            entry.grid(row=row, column=1, padx=(0, 10), pady=5, sticky="w")
            var.trace_add("write", self._on_metric_filter_change)

        metric_filter_frame.grid_columnconfigure(1, weight=1)
        metric_filter_frame.grid_columnconfigure(2, weight=1)

        keyword_filter_frame = ctk.CTkFrame(self)
        keyword_filter_frame.pack(fill="x", padx=20, pady=(0, 10))

        keyword_label = ctk.CTkLabel(keyword_filter_frame, text=tr("Keyword filter"))
        keyword_label.grid(row=0, column=0, padx=(0, 10), pady=(10, 5), sticky="w")

        self.keyword_entry = ctk.CTkEntry(keyword_filter_frame)
        self.keyword_entry.grid(row=0, column=1, padx=(0, 10), pady=(10, 5), sticky="ew")

        self.keyword_match_all = tk.BooleanVar(value=False)
        match_all_checkbox = ctk.CTkCheckBox(
            keyword_filter_frame,
            text=tr("Match all keywords"),
            variable=self.keyword_match_all,
            onvalue=True,
            offvalue=False,
        )
        match_all_checkbox.grid(row=0, column=2, padx=(0, 10), pady=(10, 5), sticky="w")

        keyword_filter_button = ctk.CTkButton(
            keyword_filter_frame,
            text=tr("Filter by keywords"),
            command=self.select_by_keywords,
            width=160,
        )
        keyword_filter_button.grid(row=0, column=3, padx=(0, 10), pady=(10, 5), sticky="ew")

        clear_keyword_button = ctk.CTkButton(
            keyword_filter_frame,
            text=tr("Clear keywords"),
            command=self.clear_keyword_filter,
            width=140,
        )
        clear_keyword_button.grid(row=0, column=4, padx=(0, 10), pady=(10, 5), sticky="ew")

        keyword_filter_frame.grid_columnconfigure(1, weight=1)
        keyword_filter_frame.grid_columnconfigure(3, weight=1)
        keyword_filter_frame.grid_columnconfigure(4, weight=1)

        date_filter_frame = ctk.CTkFrame(self)
        date_filter_frame.pack(fill="x", padx=20, pady=(10, 10))

        start_label = ctk.CTkLabel(date_filter_frame, text=tr("Start date (YYYY-MM-DD)"))
        start_label.grid(row=0, column=0, padx=(0, 10), pady=(10, 5), sticky="w")
        self.start_date_entry = ctk.CTkEntry(date_filter_frame, width=140)
        self.start_date_entry.grid(row=0, column=1, padx=(0, 10), pady=(10, 5), sticky="w")

        end_label = ctk.CTkLabel(date_filter_frame, text=tr("End date (YYYY-MM-DD)"))
        end_label.grid(row=1, column=0, padx=(0, 10), pady=5, sticky="w")
        self.end_date_entry = ctk.CTkEntry(date_filter_frame, width=140)
        self.end_date_entry.grid(row=1, column=1, padx=(0, 10), pady=5, sticky="w")

        filter_button = ctk.CTkButton(
            date_filter_frame,
            text=tr("Select by date range"),
            command=self.select_by_date_range,
            width=160,
        )
        filter_button.grid(row=0, column=2, rowspan=2, padx=(10, 0), pady=5, sticky="ew")

        clear_filter_button = ctk.CTkButton(
            date_filter_frame,
            text=tr("Clear dates"),
            command=self.clear_date_filters,
            width=120,
        )
        clear_filter_button.grid(row=0, column=3, rowspan=2, padx=(10, 0), pady=5, sticky="ew")

        date_filter_frame.grid_columnconfigure(2, weight=1)
        date_filter_frame.grid_columnconfigure(3, weight=1)

        self._available_years = sorted(years_set)
        if self._available_years:
            year_filter_frame = ctk.CTkFrame(self)
            year_filter_frame.pack(fill="x", padx=20, pady=(0, 10))

            year_label = ctk.CTkLabel(year_filter_frame, text=tr("Select by year"))
            year_label.grid(row=0, column=0, padx=(0, 10), pady=10, sticky="w")

            year_values = [str(year) for year in self._available_years]
            self.year_combobox = ctk.CTkComboBox(
                year_filter_frame,
                values=year_values,
                width=120,
            )
            self.year_combobox.set(year_values[-1])
            self.year_combobox.grid(row=0, column=1, padx=(0, 10), pady=10, sticky="w")

            select_year_button = ctk.CTkButton(
                year_filter_frame,
                text=tr("Select year"),
                command=self.select_year,
                width=140,
            )
            select_year_button.grid(row=0, column=2, padx=(0, 10), pady=10, sticky="ew")

            year_filter_frame.grid_columnconfigure(2, weight=1)

        buttons_frame = ctk.CTkFrame(self, fg_color="transparent")
        buttons_frame.pack(fill="x", padx=20, pady=(0, 20))

        cancel_button = ctk.CTkButton(
            buttons_frame,
            text=tr("Cancel"),
            command=self.on_cancel,
        )
        cancel_button.pack(side="right", padx=(10, 0))

        confirm_button = ctk.CTkButton(
            buttons_frame,
            text=tr("Confirm"),
            command=self.on_confirm,
        )
        confirm_button.pack(side="right")

        self._refresh_post_layout()

    def select_all(self):
        for var in self._checkbox_vars.values():
            var.set(True)

    def deselect_all(self):
        for var in self._checkbox_vars.values():
            var.set(False)

    def select_by_date_range(self):
        start_text = self.start_date_entry.get().strip()
        end_text = self.end_date_entry.get().strip()

        start_date = self._parse_date_input(start_text) if start_text else None
        end_date = self._parse_date_input(end_text) if end_text else None

        if start_text and start_date is None:
            messagebox.showerror(
                self._tr("Error"),
                self._tr("Invalid start date. Use the YYYY-MM-DD format."),
            )
            return

        if end_text and end_date is None:
            messagebox.showerror(
                self._tr("Error"),
                self._tr("Invalid end date. Use the YYYY-MM-DD format."),
            )
            return

        if start_date and end_date and start_date > end_date:
            messagebox.showerror(
                self._tr("Error"),
                self._tr("Start date must be before end date."),
            )
            return

        matched = False
        for post_id, var in self._checkbox_vars.items():
            published_date = self._post_dates.get(post_id)
            if published_date is None:
                var.set(False)
                continue

            if start_date and published_date < start_date:
                var.set(False)
                continue

            if end_date and published_date > end_date:
                var.set(False)
                continue

            var.set(True)
            matched = True

        if not matched:
            messagebox.showinfo(
                self._tr("Info"),
                self._tr("No posts were found for the selected date range."),
            )

    def select_year(self):
        if not hasattr(self, "year_combobox"):
            return

        try:
            selected_year = int(self.year_combobox.get())
        except (TypeError, ValueError):
            messagebox.showerror(
                self._tr("Error"),
                self._tr("Invalid year selected."),
            )
            return

        matched = False
        for post_id, var in self._checkbox_vars.items():
            published_date = self._post_dates.get(post_id)
            if published_date is not None and published_date.year == selected_year:
                var.set(True)
                matched = True
            else:
                var.set(False)

        if not matched:
            messagebox.showinfo(
                self._tr("Info"),
                self._tr("No posts were found for the selected year."),
            )

    def clear_date_filters(self):
        self.start_date_entry.delete(0, tk.END)
        self.end_date_entry.delete(0, tk.END)

    def select_by_keywords(self):
        keywords_text = self.keyword_entry.get().strip()
        if not keywords_text:
            messagebox.showinfo(
                self._tr("Info"),
                self._tr("Please enter at least one keyword."),
            )
            return

        keywords = [kw.strip().lower() for kw in re.split(r"[,\n]+", keywords_text) if kw.strip()]
        if not keywords:
            messagebox.showinfo(
                self._tr("Info"),
                self._tr("Please enter at least one keyword."),
            )
            return

        match_all = bool(self.keyword_match_all.get())
        matched = False
        for post_id, var in self._checkbox_vars.items():
            haystack = self._post_search_texts.get(post_id, "")
            if not haystack:
                var.set(False)
                continue

            if match_all:
                is_match = all(keyword in haystack for keyword in keywords)
            else:
                is_match = any(keyword in haystack for keyword in keywords)

            var.set(is_match)
            if is_match:
                matched = True

        if not matched:
            messagebox.showinfo(
                self._tr("Info"),
                self._tr("No posts matched the provided keywords."),
            )

    def clear_keyword_filter(self):
        self.keyword_entry.delete(0, tk.END)
        self.keyword_match_all.set(False)
        self.select_all()

    def clear_metric_filters(self):
        self.min_attachments_var.set("")
        self.min_images_var.set("")
        self.min_videos_var.set("")

    def on_sort_selection(self, _value):
        self._refresh_post_layout()

    def _on_metric_filter_change(self, *_):
        self._refresh_post_layout()

    def _refresh_post_layout(self):
        if not self._post_entries:
            return

        if self._no_results_label is not None:
            self._no_results_label.destroy()
            self._no_results_label = None

        sort_choice = self.metric_sort_combobox.get() if hasattr(self, "metric_sort_combobox") else None
        sort_key, descending = self._sort_option_map.get(sort_choice, ("order", False))
        filters = self._parse_metric_filters()
        ordered_entries = self.apply_sort_and_filters(self._post_entries, filters, sort_key, descending)

        for entry in self._post_entries:
            entry["checkbox"].pack_forget()

        if not ordered_entries:
            self._no_results_label = ctk.CTkLabel(self.scrollable, text=self._tr("No posts match the current filters."))
            self._no_results_label.pack(pady=10)
            return

        for entry in ordered_entries:
            entry["checkbox"].pack(fill="x", padx=5, pady=5, anchor="w")

    @staticmethod
    def apply_sort_and_filters(entries, filters, sort_key, descending):
        def matches(entry):
            metrics = entry.get("metrics", {})
            for name, value in filters.items():
                if value is None:
                    continue
                if metrics.get(name, 0) < value:
                    return False
            return True

        filtered = [entry for entry in entries if matches(entry)]

        def sort_value(entry):
            if sort_key == "order":
                return entry.get("order", 0)
            return entry.get("metrics", {}).get(sort_key, 0)

        return sorted(filtered, key=sort_value, reverse=descending)

    def _parse_metric_filters(self):
        return {
            "attachments": self._parse_metric_value(getattr(self, "min_attachments_var", None)),
            "images": self._parse_metric_value(getattr(self, "min_images_var", None)),
            "videos": self._parse_metric_value(getattr(self, "min_videos_var", None)),
        }

    def _parse_metric_value(self, var):
        if var is None:
            return None
        value = var.get().strip()
        if not value:
            return None
        try:
            parsed = int(value)
        except ValueError:
            return None
        return max(parsed, 0)

    def _calculate_media_counts(self, post):
        attachments = post.get("attachments") or []
        main_file = post.get("file")
        media_entries = []
        if isinstance(main_file, dict) and any(main_file.get(key) for key in ("path", "url", "name")):
            media_entries.append(main_file)
        for attachment in attachments:
            if isinstance(attachment, dict):
                media_entries.append(attachment)

        metrics = {
            "attachments": len(media_entries),
            "images": 0,
            "videos": 0,
            "video_details": [],
            "total_video_size": 0,
            "largest_video_size": 0,
            "longest_video_duration": 0,
        }

        for entry in media_entries:
            media_type = self._detect_media_type(entry)
            if media_type == "image":
                metrics["images"] += 1
                continue

            if media_type != "video":
                self._log_debug(
                    f"Skipping non-media attachment: keys={list(entry.keys()) if isinstance(entry, dict) else type(entry)}"
                )
                continue

            metrics["videos"] += 1
            detail = self._extract_video_detail(entry, metrics["videos"])
            if detail:
                metrics["video_details"].append(detail)
                size_bytes = detail.get("size_bytes")
                if isinstance(size_bytes, (int, float)):
                    metrics["total_video_size"] += size_bytes
                    metrics["largest_video_size"] = max(metrics["largest_video_size"], size_bytes)
                duration_seconds = detail.get("duration_seconds")
                if isinstance(duration_seconds, (int, float)):
                    metrics["longest_video_duration"] = max(
                        metrics["longest_video_duration"], duration_seconds
                    )
                if size_bytes is None or duration_seconds is None:
                    self._log_debug(
                        f"Missing video metrics for attachment: name={detail.get('name')}, "
                        f"size={size_bytes}, duration={duration_seconds}, keys={list(entry.keys()) if isinstance(entry, dict) else type(entry)}"
                    )
            else:
                self._log_debug(
                    f"Unable to extract video detail for attachment with keys={list(entry.keys()) if isinstance(entry, dict) else type(entry)}"
                )
        return metrics

    def _detect_media_type(self, entry):
        source = str(entry.get("path") or entry.get("url") or entry.get("name") or "")
        lowered = source.lower().split("?")[0]
        _, ext = os.path.splitext(lowered)

        metadata = entry.get("metadata") if isinstance(entry, dict) else None
        type_hints = []
        for candidate in (entry, metadata):
            if isinstance(candidate, dict):
                type_hints.extend(
                    str(candidate.get(key) or "").lower()
                    for key in (
                        "type",
                        "mimetype",
                        "mime",
                        "media_type",
                        "content_type",
                        "mime_type",
                        "file_type",
                    )
                    if key in candidate
                )

                for bool_key in ("is_video", "video", "isVideo"):
                    if candidate.get(bool_key) is True:
                        type_hints.append("video")
                for bool_key in ("is_image", "image", "isImage"):
                    if candidate.get(bool_key) is True:
                        type_hints.append("image")

        if ext in VIDEO_EXTENSIONS or any(hint.startswith("video") for hint in type_hints):
            return "video"

        if ext in IMAGE_EXTENSIONS or any(hint.startswith("image") for hint in type_hints):
            return "image"

        return None

    def _extract_video_detail(self, entry, index):
        size_keys = ("size", "file_size", "filesize", "bytes", "size_bytes", "content_length")
        duration_keys = (
            "duration",
            "length",
            "video_length",
            "videoDuration",
            "duration_seconds",
        )

        size_bytes = None
        duration_seconds = None

        metadata = entry.get("metadata") if isinstance(entry, dict) else None
        candidates = [entry]
        if isinstance(metadata, dict):
            candidates.append(metadata)

        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            for key in size_keys:
                if key in candidate:
                    size_bytes = self._parse_size_value(candidate.get(key))
                    if size_bytes is not None:
                        break
            if size_bytes is not None:
                break

        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            for key in duration_keys:
                if key in candidate:
                    duration_seconds = self._parse_duration_value(candidate.get(key))
                    if duration_seconds is not None:
                        break
            if duration_seconds is not None:
                break

        name = None
        if isinstance(entry, dict):
            name = entry.get("name") or entry.get("path") or entry.get("url")
        if size_bytes is None or duration_seconds is None:
            remote_metrics = self._fetch_remote_video_metrics(entry)
            if remote_metrics:
                size_bytes = size_bytes if size_bytes is not None else remote_metrics.get("size_bytes")
                duration_seconds = (
                    duration_seconds
                    if duration_seconds is not None
                    else remote_metrics.get("duration_seconds")
                )

        return {
            "name": name,
            "size_bytes": size_bytes,
            "duration_seconds": duration_seconds,
            "index": index,
        }

    def _parse_size_value(self, value):
        numeric = self._parse_positive_number(value)
        if numeric is not None:
            return numeric

        if isinstance(value, str):
            cleaned = value.replace(",", "").strip()
            match = re.match(r"([0-9]*\.?[0-9]+)\s*([kmgt]?b)?", cleaned, re.IGNORECASE)
            if match:
                amount = float(match.group(1))
                unit = (match.group(2) or "b").lower()
                multiplier = {
                    "b": 1,
                    "kb": 1024,
                    "mb": 1024 ** 2,
                    "gb": 1024 ** 3,
                    "tb": 1024 ** 4,
                }.get(unit, 1)
                return amount * multiplier

        if isinstance(value, dict):
            for nested in value.values():
                nested_numeric = self._parse_size_value(nested)
                if nested_numeric is not None:
                    return nested_numeric

        return None

    def _parse_positive_number(self, value):
        if value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if number >= 0 else None

    def _parse_duration_value(self, value):
        parsed_number = self._parse_positive_number(value)
        if parsed_number is not None:
            return parsed_number

        if isinstance(value, str):
            parts = value.strip().split(":")
            if 1 <= len(parts) <= 3 and all(part.isdigit() for part in parts):
                parts = [int(part) for part in parts]
                while len(parts) < 3:
                    parts.insert(0, 0)
                hours, minutes, seconds = parts
                return hours * 3600 + minutes * 60 + seconds

        if isinstance(value, dict):
            for nested in value.values():
                nested_duration = self._parse_duration_value(nested)
                if nested_duration is not None:
                    return nested_duration

        return None

    def _fetch_remote_video_metrics(self, entry):
        if not isinstance(entry, dict):
            return None

        url = self._build_media_url(entry)
        if not url:
            return None

        cache = getattr(self, "_head_cache", None)
        if cache is None:
            cache = {}
            self._head_cache = cache

        if url in cache:
            return cache[url]

        metrics = {"size_bytes": None, "duration_seconds": None}

        try:
            response = requests.head(url, allow_redirects=True, timeout=5)
        except Exception as exc:
            self._log_debug(f"HEAD request failed for {url}: {exc}")
            response = None

        if response is None or response.status_code >= 400:
            try:
                response = requests.get(url, allow_redirects=True, stream=True, timeout=10)
            except Exception as exc:
                self._log_debug(f"GET request failed for {url}: {exc}")
                response = None

        if response is not None:
            headers = {k.lower(): v for k, v in response.headers.items()}
            content_length = headers.get("content-length")
            if content_length is not None:
                metrics["size_bytes"] = self._parse_size_value(content_length)

            for duration_key in (
                "content-duration",
                "x-amz-meta-duration",
                "x-oss-meta-duration",
                "x-video-duration",
                "video-duration",
            ):
                if duration_key in headers:
                    metrics["duration_seconds"] = self._parse_duration_value(headers.get(duration_key))
                    break

            if metrics["duration_seconds"] is None:
                duration_header = next(
                    (value for key, value in headers.items() if "duration" in key),
                    None,
                )
                if duration_header is not None:
                    metrics["duration_seconds"] = self._parse_duration_value(duration_header)

            if response and hasattr(response, "close"):
                try:
                    response.close()
                except Exception:
                    pass

        if metrics["size_bytes"] is None and metrics["duration_seconds"] is None:
            self._log_debug(
                f"Remote metadata unavailable for {url} (keys={list(entry.keys())})"
            )
            self._head_cache[url] = None
            return None

        self._head_cache[url] = metrics
        return metrics

    def _build_media_url(self, entry):
        path = None
        if isinstance(entry, dict):
            path = entry.get("path") or entry.get("url") or entry.get("name")

        if not path:
            return None

        if str(path).startswith("http://") or str(path).startswith("https://"):
            return path

        media_base = getattr(self, "_media_base", None)
        if media_base:
            normalized = path if str(path).startswith("/") else f"/{path}"
            return f"{media_base.rstrip('/')}{normalized}"

        return None

    def _log_debug(self, message):
        log_callback = getattr(self, "_log_callback", None)
        if callable(log_callback):
            try:
                log_callback(message)
            except Exception:
                pass

    def _format_bytes(self, num_bytes, precision=2):
        if num_bytes is None:
            return ""
        suffixes = ["B", "KB", "MB", "GB", "TB"]
        num = float(num_bytes)
        order = 0
        while num >= 1024 and order < len(suffixes) - 1:
            num /= 1024.0
            order += 1
        return f"{num:.{precision}f} {suffixes[order]}"

    def _format_duration(self, seconds):
        seconds = max(int(seconds), 0)
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours:d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:d}:{secs:02d}"

    def _parse_date_input(self, value):
        try:
            return datetime.datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return None

    def _parse_published_date(self, published):
        if not published:
            return None

        if isinstance(published, (int, float)):
            try:
                return datetime.datetime.fromtimestamp(published)
            except (OverflowError, OSError, ValueError):
                return None

        if isinstance(published, str):
            text = published.strip()
            if not text:
                return None

            if text.endswith("Z"):
                text = text[:-1] + "+00:00"

            try:
                return datetime.datetime.fromisoformat(text)
            except ValueError:
                pass

            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    return datetime.datetime.strptime(text, fmt)
                except ValueError:
                    continue

        return None

    def on_confirm(self):
        selected = [post_id for post_id, var in self._checkbox_vars.items() if var.get()]
        if not selected:
            messagebox.showwarning(self._tr("Aviso"), self._tr("Select at least one post to continue."))
            return
        self._confirmed = True
        self._result = selected
        self.destroy()

    def on_cancel(self):
        self._confirmed = False
        self._result = []
        self.destroy()

    def show(self):
        self.wait_window()
        return self._confirmed, self._result

def extract_ck_parameters(url: ParseResult) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Get the service, user and post id from the url if they exist
    """
    match = re.search(r"/(?P<service>[^/?]+)(/user/(?P<user>[^/?]+)(/post/(?P<post>[^/?]+))?)?", url.path)
    if match:
        service, user, post = match.group("service", "user", "post")
        return service, user, post
    else:
        return None, None, None

def extract_ck_query(url: ParseResult) -> tuple[Optional[str], int]:
    """
    Try to obtain the query and offset from the url if they exist
    """

    # This is kinda contrived but query parameters are awful to get right
    query = parse_qs(url.query)
    q = query.get("q")[0] if query.get("q") is not None and len(query.get("q")) > 0 else "0"
    o = query.get("o")[0] if query.get("o") is not None and len(query.get("o")) > 0 else "0"

    return q, int(o) if str.isdigit(o) else 0

# Application class
class ImageDownloaderApp(ctk.CTk):
    def __init__(self):
        self.errors = []  
        self._log_buffer = []
        self.github_stars = 0
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        super().__init__()
        self.version = VERSION
        self.title(f"Downloader [{VERSION}]")
        
        # Setup window
        self.setup_window()
        
        # Settings window
        self.settings_window = SettingsWindow(
            self,
            self.tr,
            self.load_translations,
            self.update_ui_texts,
            self.save_language_preference,
            VERSION,
            None,  # Por ahora, no se pasa ningún downloader
            self.check_for_new_version
        )

        self.all_logs = []  # Lista para almacenar todos los logs

        self.download_start_time = None
        self.errors = []
        self.warnings = []
        self.current_download_context = None
        self.last_download_metadata = None
        self.last_history_session_id = None
        self.history_file = Path("resources/config/logs/download_history.json")
        self.download_history = self.load_download_history()
        self.history_service_filter_value = "__all__"
        self.history_service_display_map = {}
        self.cancelled_downloader_snapshot = None

        self._managed_downloaders = set()
        self._managed_downloaders_lock = threading.Lock()
        self._download_threads = set()
        self._download_threads_lock = threading.Lock()

        # About window placeholder
        self.about_window = None

        self.extras_window = None

        # Load settings
        self.settings = self.settings_window.load_settings()

        # SimpCity cookie password cache (never persisted on disk)
        self.simpcity_cookie_password = os.getenv("COOMERDL_COOKIES_PASSWORD")

        # Language preferences
        lang = self.load_language_preference()
        self.load_translations(lang)
        self.image_downloader = None

        # Determine request timeout configuration
        request_timeout_setting = self.settings.get('http_timeout', 20.0)
        try:
            request_timeout_setting = float(request_timeout_setting)
        except (TypeError, ValueError):
            request_timeout_setting = 20.0
        if request_timeout_setting <= 0:
            request_timeout_setting = 0.1
        self.request_timeout = request_timeout_setting

        # About window (after timeout is known)
        self.about_window = AboutWindow(self, self.tr, VERSION, request_timeout=self.request_timeout)

        # Patch notes
        #self.patch_notes = PatchNotes(self, self.tr, request_timeout=self.request_timeout)

        self.progress_bars = {}
        
        # Obtener el número de estrellas de GitHub
        self.github_stars = self.get_github_stars("emy69", "CoomerDL", timeout=min(self.request_timeout, 5.0))

        # Cargar el icono de GitHub
        self.github_icon = self.load_github_icon()

        # Initialize UI
        self.initialize_ui()
        
        self.update_ui_texts()  

        self.update_queue = queue.Queue()
        self.check_update_queue()
        self.protocol("WM_DELETE_WINDOW", self.on_app_close)
        
        # Load all settings with defaults from the settings object
        self.max_downloads = self.settings.get('max_downloads', 3)
        max_retries_setting = self.settings.get('max_retries', 5)
        if not isinstance(max_retries_setting, int):
            try:
                max_retries_setting = int(max_retries_setting)
            except (TypeError, ValueError):
                max_retries_setting = 5
        max_retries_setting = max(0, min(max_retries_setting, 5))
        retry_interval_setting = self.settings.get('retry_interval', 2.0)
        folder_structure_setting = self.settings.get('folder_structure', 'default')
        
        # Load download folder
        self.download_folder = self.load_download_folder() 
        if self.download_folder:
            self.folder_path.configure(text=self.download_folder)

        self.default_downloader = self._register_downloader(Downloader(
            download_folder=self.download_folder,
            max_workers=self.max_downloads,
            log_callback=self.add_log_message_safe,
            update_progress_callback=self.update_progress,
            update_global_progress_callback=self.update_global_progress,
            tr=self.tr,
            retry_interval=retry_interval_setting,
            folder_structure=folder_structure_setting,
            max_retries=max_retries_setting,
            stream_read_timeout=self.request_timeout,
        ))
        
        self.settings_window.downloader = self.default_downloader

        self.active_downloader = None  # Initialize active_downloader

        # Cargar iconos redimensionados
        self.icons = {
            'image': self.load_and_resize_image('resources/img/iconos/ui/image_icon.png', (40, 40)),
            'video': self.load_and_resize_image('resources/img/iconos/ui/video.png', (40, 40)),
            'zip': self.load_and_resize_image('resources/img/iconos/ui/file-zip.png', (40, 40)),
            'default': self.load_and_resize_image('resources/img/iconos/ui/default_icon.png', (40, 40))
        }

        # Progress manager
        self.progress_manager = ProgressManager(
            root=self,
            icons=self.icons,
            footer_speed_label=self.footer_speed_label,
            footer_eta_label=self.footer_eta_label,
            progress_bar=self.progress_bar,
            progress_percentage=self.progress_percentage
        )
        
        # Check for new version on startup
        threading.Thread(target=self.check_for_new_version, args=(True,)).start()

    # Application close event
    def on_app_close(self):
        if self.is_download_active() and not self.active_downloader.cancel_requested:
            # Mostrar advertencia si hay una descarga activa
            messagebox.showwarning(
                self.tr("Descarga Activa"),
                self.tr("Hay una descarga en progreso. Por favor, cancela la descarga antes de cerrar.")
            )
        else:
            self.close_program()

    def is_download_active(self):
        return self.active_downloader is not None
    
    def close_program(self, wait_timeout: Optional[float] = 30.0):
        downloaders = self._get_managed_downloaders_snapshot()
        for downloader in downloaders:
            cancel = getattr(downloader, "request_cancel", None)
            if callable(cancel):
                cancel()

        self.active_downloader = None
        self._wait_for_download_threads(timeout=wait_timeout)

        for downloader in downloaders:
            shutdown = getattr(downloader, "shutdown_executor", None)
            if callable(shutdown):
                shutdown()
        self.destroy()

    def _register_downloader(self, downloader):
        if downloader is None:
            return None
        with self._managed_downloaders_lock:
            self._managed_downloaders.add(downloader)
        return downloader

    def _get_managed_downloaders_snapshot(self):
        with self._managed_downloaders_lock:
            return [downloader for downloader in self._managed_downloaders if downloader is not None]

    def _register_download_thread(self, thread):
        if thread is None:
            return None
        with self._download_threads_lock:
            self._download_threads.add(thread)
        return thread

    def _unregister_current_download_thread(self):
        current_thread = threading.current_thread()
        with self._download_threads_lock:
            self._download_threads.discard(current_thread)

    def _wait_for_download_threads(self, timeout: Optional[float] = None):
        deadline = time.time() + timeout if timeout is not None else None
        while True:
            with self._download_threads_lock:
                active_threads = [t for t in self._download_threads if t.is_alive()]
            if not active_threads:
                break

            for thread in active_threads:
                join_timeout = None
                if deadline is not None:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        break
                    join_timeout = remaining
                thread.join(join_timeout)

            if deadline is not None and time.time() >= deadline:
                break

        with self._download_threads_lock:
            self._download_threads = {t for t in self._download_threads if t.is_alive()}
    
    # Save and load language preferences
    def save_language_preference(self, language_code):
        config = {'language': language_code}
        with open('resources/config/languages/save_language/language_config.json', 'w') as config_file:
            json.dump(config, config_file)
        self.load_translations(language_code)
        self.update_ui_texts()
    
    def load_language_preference(self):
        try:
            with open('resources/config/languages/save_language/language_config.json', 'r') as config_file:
                config = json.load(config_file)
                return config.get('language', 'en')
        except FileNotFoundError:
            return 'en'

    # Load translations
    def load_translations(self, lang):
        path = "resources/config/languages/translations.json"
        with open(path, 'r', encoding='utf-8') as file:
            all_translations = json.load(file)
            self.translations = {key: value.get(lang, key) for key, value in all_translations.items()}
    
    def tr(self, text, **kwargs):
        translated_text = self.translations.get(text, text)
        if kwargs:
            translated_text = translated_text.format(**kwargs)
        return translated_text

    # Window setup
    def setup_window(self):
        window_width, window_height = 1280, 720
        center_x = int((self.winfo_screenwidth() / 2) - (window_width / 2))
        center_y = int((self.winfo_screenheight() / 2) - (window_height / 2))
        self.geometry(f"{window_width}x{window_height}+{center_x}+{center_y}")
        
        if sys.platform == "win32":
            self.iconbitmap("resources/img/window.ico")

    # Initialize UI components
    def initialize_ui(self):

        # Crear la barra de menú personalizada
        self.menu_bar = ctk.CTkFrame(self, height=30, corner_radius=0)
        self.menu_bar.pack(side="top", fill="x")

        # Añadir botones al menú
        self.create_custom_menubar()

        # Update alert frame (initially hidden)
        self.update_alert_frame = ctk.CTkFrame(self, fg_color="#4CAF50", corner_radius=0) # Green background
        self.update_alert_frame.pack(side="top", fill="x")
        self.update_alert_frame.pack_forget() # Hide initially

        self.update_alert_label = ctk.CTkLabel(self.update_alert_frame, text="", text_color="white", font=("Arial", 12, "bold"))
        self.update_alert_label.pack(side="left", padx=10, pady=5)

        self.update_download_button = ctk.CTkButton(self.update_alert_frame, text=self.tr("Download Now"), command=self.open_latest_release, fg_color="#388E3C", hover_color="#2E7D32")
        self.update_download_button.pack(side="right", padx=10, pady=5)

        # Input frame
        self.input_frame = ctk.CTkFrame(self)
        self.input_frame.pack(fill='x', padx=20, pady=20)
        self.input_frame.grid_columnconfigure(0, weight=1)
        self.input_frame.grid_rowconfigure(1, weight=1)

        self.url_label = ctk.CTkLabel(self.input_frame, text=self.tr("URL de la página web:"))
        self.url_label.grid(row=0, column=0, sticky='w')

        self.url_entry = ctk.CTkEntry(self.input_frame)
        self.url_entry.grid(row=1, column=0, sticky='ew', padx=(0, 5))

        self.browse_button = ctk.CTkButton(self.input_frame, text=self.tr("Seleccionar Carpeta"), command=self.select_folder)
        self.browse_button.grid(row=1, column=1, sticky='e')

        self.folder_path = ctk.CTkLabel(self.input_frame, text="", cursor="hand2", font=("Arial", 13))
        self.folder_path.grid(row=2, column=0, columnspan=2, sticky='w')
        self.folder_path.bind("<Button-1>", self.open_download_folder)

        # Añadir eventos para el efecto hover
        self.folder_path.bind("<Enter>", self.on_hover_enter)
        self.folder_path.bind("<Leave>", self.on_hover_leave)

        # Options frame
        self.options_frame = ctk.CTkFrame(self)
        self.options_frame.pack(pady=10, fill='x', padx=20)

        self.download_images_check = ctk.CTkCheckBox(self.options_frame, text=self.tr("Descargar Imágenes"))
        self.download_images_check.pack(side='left', padx=10)
        self.download_images_check.select()

        self.download_videos_check = ctk.CTkCheckBox(self.options_frame, text=self.tr("Descargar Vídeos"))
        self.download_videos_check.pack(side='left', padx=10)
        self.download_videos_check.select()

        self.download_compressed_check = ctk.CTkCheckBox(self.options_frame, text=self.tr("Descargar Comprimidos"))
        self.download_compressed_check.pack(side='left', padx=10)
        self.download_compressed_check.select()

        # Action frame
        self.action_frame = ctk.CTkFrame(self)
        self.action_frame.pack(pady=10, fill='x', padx=20)

        self.download_button = ctk.CTkButton(self.action_frame, text=self.tr("Descargar"), command=self.start_download)
        self.download_button.pack(side='left', padx=5)

        self.pause_button = ctk.CTkButton(self.action_frame, width=16, height=28, text=self.tr("⏸"), state="disabled", command=self.pause_download)
        self.pause_button.pack(side='left', padx=5)

        self.resume_button = ctk.CTkButton(self.action_frame, width=16, height=28, text=self.tr("▶"), state="disabled", command=self.resume_download)
        self.resume_button.pack(side='left', padx=5)

        self.cancel_button = ctk.CTkButton(self.action_frame, text=self.tr("Cancelar Descarga"), state="disabled", command=self.cancel_download)
        self.cancel_button.pack(side='left', padx=5)

        self.enable_preflight_check = ctk.CTkCheckBox(
            self.action_frame,
            text=self.tr("Preflight Post Selection")
        )
        self.enable_preflight_check.pack(side='right', padx=10)
        self.enable_preflight_check.select()

        self.progress_label = ctk.CTkLabel(self.action_frame, text="")
        self.progress_label.pack(side='left', padx=10)

        # self.log_textbox = ctk.CTkTextbox(self, width=590, height=200)
        # self.log_textbox.pack(pady=(10, 0), padx=20, fill='both', expand=True)
        # self.log_textbox.configure(state="disabled")

        # Log and history container
        self.log_history_container = ctk.CTkFrame(self)
        self.log_history_container.pack(pady=(10, 0), padx=20, fill='both', expand=True)

        # Log Textbox
        self.log_frame = ctk.CTkFrame(self.log_history_container)
        self.log_frame.pack(side='left', fill='both', expand=True)

        self.log_textbox = ctk.CTkTextbox(self.log_frame, width=590, height=200, activate_scrollbars=False)
        self.log_textbox.pack(side='left', fill='both', expand=True)

        self.log_scrollbar = ctk.CTkScrollbar(self.log_frame, command=self.log_textbox.yview)
        self.log_scrollbar.pack(side='right', fill='y')

        self.log_textbox.configure(state="disabled", yscrollcommand=self.log_scrollbar.set)

        # Download history sidebar
        self.history_frame = ctk.CTkFrame(self.log_history_container, width=260)
        self.history_frame.pack(side='left', fill='y', padx=(10, 0))

        self.history_title_label = ctk.CTkLabel(
            self.history_frame,
            text=self.tr("Recent Sessions"),
            font=("Arial", 14, "bold")
        )
        self.history_title_label.pack(fill='x', padx=5, pady=(5, 5))

        self.history_search_var = tk.StringVar()
        self.history_search_var.trace_add("write", lambda *_: self.update_history_display())

        self.history_search_entry = ctk.CTkEntry(
            self.history_frame,
            textvariable=self.history_search_var,
            placeholder_text=self.tr("Search sessions...")
        )
        self.history_search_entry.pack(fill='x', padx=5, pady=(0, 10))

        self.history_service_filter = ctk.CTkComboBox(
            self.history_frame,
            values=[],
            command=self.on_history_service_selected
        )
        self.history_service_filter.pack(fill='x', padx=5, pady=(0, 10))

        self.history_results_label = ctk.CTkLabel(
            self.history_frame,
            text=""
        )
        self.history_results_label.pack(fill='x', padx=5)

        self.history_list_frame = ctk.CTkScrollableFrame(self.history_frame)
        self.history_list_frame.pack(fill='both', expand=True, padx=5, pady=(5, 5))

        self.refresh_history_filters()
        self.update_history_display()

        # Progress frame
        self.progress_frame = ctk.CTkFrame(self)
        self.progress_frame.pack(pady=(0, 10), fill='x', padx=20)

        self.progress_bar = ctk.CTkProgressBar(self.progress_frame)
        self.progress_bar.pack(side='left', fill='x', expand=True, padx=(0, 10))

        # self.processing_label = ctk.CTkLabel(self.progress_frame, text=self.tr("Procesando videos..."), font=("Arial", 12))
        # self.processing_label.pack(side='top', pady=(0, 10))
        # self.processing_label.pack_forget()

        self.progress_percentage = ctk.CTkLabel(self.progress_frame, text="0%")
        self.progress_percentage.pack(side='left')

        # Cargar el icono de descarga con un tamaño mayor
        self.download_icon = self.load_and_resize_image('resources/img/iconos/ui/download_icon.png', (24, 24))  # Cambiado a (24, 24)

        # Reemplazar el botón con una etiqueta que simule un botón
        self.toggle_details_button = ctk.CTkLabel(self.progress_frame, image=self.download_icon, text="", cursor="hand2")
        self.toggle_details_button.pack(side='left', padx=(5, 0))
        self.toggle_details_button.bind("<Button-1>", lambda e: self.toggle_progress_details())

        # Agregar efecto hover
        self.toggle_details_button.bind("<Enter>", lambda e: self.toggle_details_button.configure(fg_color="gray25"))
        self.toggle_details_button.bind("<Leave>", lambda e: self.toggle_details_button.configure(fg_color="transparent"))

        self.progress_details_frame = ctk.CTkFrame(self)
        self.progress_details_frame.place_forget()

        # Context menu
        self.context_menu = tk.Menu(self.url_entry, tearoff=0)
        self.context_menu.add_command(label=self.tr("Copiar"), command=self.copy_to_clipboard)
        self.context_menu.add_command(label=self.tr("Pegar"), command=self.paste_from_clipboard)
        self.context_menu.add_command(label=self.tr("Cortar"), command=self.cut_to_clipboard)

        self.url_entry.bind("<Button-3>", self.show_context_menu)
        self.bind("<Button-1>", self.on_click)

        footer = ctk.CTkFrame(self, height=30, corner_radius=0)
        footer.pack(side="bottom", fill="x")

        self.footer_eta_label = ctk.CTkLabel(footer, text="ETA: N/A", font=("Arial", 11))
        self.footer_eta_label.pack(side="left", padx=20)

        self.footer_speed_label = ctk.CTkLabel(footer, text="Speed: 0 KB/s", font=("Arial", 11))
        self.footer_speed_label.pack(side="right", padx=20)

        # Actualizar textos después de inicializar la UI
        self.update_ui_texts()

    # Update UI texts
    def update_ui_texts(self):

        # Actualizar textos de los botones del menú
        for widget in self.menu_bar.winfo_children():
            if isinstance(widget, ctk.CTkButton):
                text = widget.cget("text")
                if text.strip() in ["Archivo", "Ayuda", "Donaciones", "About", "Donors"]:
                    widget.configure(text=self.tr(text.strip()))

        # Si los menús están abiertos, recrearlos para actualizar los textos
        if self.archivo_menu_frame and self.archivo_menu_frame.winfo_exists():
            self.archivo_menu_frame.destroy()
            self.toggle_archivo_menu()

        self.url_label.configure(text=self.tr("URL de la página web:"))
        self.browse_button.configure(text=self.tr("Seleccionar Carpeta"))
        self.download_images_check.configure(text=self.tr("Descargar Imágenes"))
        self.download_videos_check.configure(text=self.tr("Descargar Vídeos"))
        self.download_compressed_check.configure(text=self.tr("Descargar Comprimidos"))
        self.download_button.configure(text=self.tr("Descargar"))
        self.pause_button.configure(text=self.tr("⏸"))
        self.resume_button.configure(text=self.tr("▶"))
        self.cancel_button.configure(text=self.tr("Cancelar Descarga"))
        self.enable_preflight_check.configure(text=self.tr("Preflight Post Selection"))
        # self.processing_label.configure(text=self.tr("Procesando videos..."))
        self.title(self.tr(f"Downloader [{VERSION}]"))
        self.update_download_button.configure(text=self.tr("Download Now"))

        if hasattr(self, "history_title_label"):
            self.history_title_label.configure(text=self.tr("Recent Sessions"))
        if hasattr(self, "history_search_entry"):
            self.history_search_entry.configure(placeholder_text=self.tr("Search sessions..."))
        self.refresh_history_filters()
        self.update_history_display()


    def open_download_folder(self, event=None):
        if self.download_folder and os.path.exists(self.download_folder):
            if sys.platform == "win32":
                os.startfile(self.download_folder)  # Para Windows
            elif sys.platform == "darwin":
                subprocess.Popen(["open", self.download_folder])  # Para macOS
            else:
                subprocess.Popen(["xdg-open", self.download_folder])  # Para Linux
        else:
            messagebox.showerror(self.tr("Error"), self.tr("La carpeta no existe o no es válida."))


    def on_click(self, event):
        # Obtener la lista de widgets que no deben cerrar el menú al hacer clic
        widgets_to_ignore = [self.menu_bar]

        # Añadir los frames de los menús desplegables si existen
        for frame in [self.archivo_menu_frame, self.ayuda_menu_frame, self.donaciones_menu_frame]:
            if frame and frame.winfo_exists():
                widgets_to_ignore.append(frame)
                widgets_to_ignore.extend(self.get_all_children(frame))

        # Si el widget en el que se hizo clic no es ninguno de los que debemos ignorar, cerramos los menús
        if event.widget not in widgets_to_ignore:
            self.close_all_menus()

    def get_all_children(self, widget):
        children = widget.winfo_children()
        all_children = list(children)
        for child in children:
            all_children.extend(self.get_all_children(child))
        return all_children

    def create_custom_menubar(self):
        # Botón Archivo
        archivo_button = ctk.CTkButton(
            self.menu_bar,
            text=self.tr("Archivo"),
            width=80,
            fg_color="transparent",
            hover_color="gray25",
            command=self.toggle_archivo_menu
        )
        archivo_button.pack(side="left")
        archivo_button.bind("<Button-1>", lambda e: "break")

        # Botón About
        about_button = ctk.CTkButton(
            self.menu_bar,
            text=self.tr("About"),
            width=80,
            fg_color="transparent",
            hover_color="gray25",
            command=self.about_window.show_about 
        )
        about_button.pack(side="left")
        about_button.bind("<Button-1>", lambda e: "break")

        # Botón Donors
        donors_button = ctk.CTkButton(
            self.menu_bar,
            text=self.tr("Donors"),
            width=80,
            fg_color="transparent",
            hover_color="gray25",
            command=self.show_donors_modal
        )
        donors_button.pack(side="left")
        donors_button.bind("<Button-1>", lambda e: "break")

        # Inicializar variables para los menús desplegables
        self.archivo_menu_frame = None
        self.ayuda_menu_frame = None
        self.donaciones_menu_frame = None

        # Función para cambiar el fondo al pasar el ratón
        def on_enter(event, frame):
            frame.configure(fg_color="gray25")

        def on_leave(event, frame):
            frame.configure(fg_color="transparent")

        # Añadir el icono de GitHub y el contador de estrellas
        if self.github_icon:
            resized_github_icon = self.github_icon.resize((16, 16), Image.Resampling.LANCZOS)
            resized_github_icon = ctk.CTkImage(resized_github_icon)
            github_frame = ctk.CTkFrame(self.menu_bar,cursor="hand2", fg_color="transparent", corner_radius=5)
            github_frame.pack(side="right", padx=5)
            github_label = ctk.CTkLabel(
                github_frame,
                image=resized_github_icon,
                text=f" Star {self.github_stars}",
                compound="left",
                font=("Arial", 12)
            )
            github_label.pack(padx=5, pady=5)
            github_frame.bind("<Enter>", lambda e: on_enter(e, github_frame))
            github_frame.bind("<Leave>", lambda e: on_leave(e, github_frame))
            github_label.bind("<Enter>", lambda e: on_enter(e, github_frame))
            github_label.bind("<Leave>", lambda e: on_leave(e, github_frame))
            github_label.bind("<Button-1>", lambda e: webbrowser.open("https://github.com/emy69/CoomerDL"))

        # Añadir el icono de Discord
        self.discord_icon = self.load_discord_icon()
        if self.discord_icon:
            resized_discord_icon = self.discord_icon.resize((16, 16), Image.Resampling.LANCZOS)
            resized_discord_icon = ctk.CTkImage(resized_discord_icon)
            discord_frame = ctk.CTkFrame(self.menu_bar,cursor="hand2", fg_color="transparent", corner_radius=5)
            discord_frame.pack(side="right", padx=5)
            discord_label = ctk.CTkLabel(
                discord_frame,
                image=resized_discord_icon,
                text="Discord",
                compound="left"
            )
            discord_label.pack(padx=5, pady=5)
            discord_frame.bind("<Enter>", lambda e: on_enter(e, discord_frame))
            discord_frame.bind("<Leave>", lambda e: on_leave(e, discord_frame))
            discord_label.bind("<Enter>", lambda e: on_enter(e, discord_frame))
            discord_label.bind("<Leave>", lambda e: on_leave(e, discord_frame))
            discord_label.bind("<Button-1>", lambda e: webbrowser.open("https://discord.gg/ku8gSPsesh"))

        # Añadir un nuevo icono PNG
        self.new_icon = self.load_patreon_icon()
        if self.new_icon:
            resized_new_icon = self.new_icon.resize((16, 16), Image.Resampling.LANCZOS)
            resized_new_icon = ctk.CTkImage(resized_new_icon)
            new_icon_frame = ctk.CTkFrame(self.menu_bar,cursor="hand2", fg_color="transparent", corner_radius=5)
            new_icon_frame.pack(side="right", padx=5)
            new_icon_label = ctk.CTkLabel(
                new_icon_frame,
                image=resized_new_icon,
                text="Patreon",
                compound="left"
            )
            new_icon_label.pack(padx=5, pady=5)
            new_icon_frame.bind("<Enter>", lambda e: on_enter(e, new_icon_frame))
            new_icon_frame.bind("<Leave>", lambda e: on_leave(e, new_icon_frame))
            new_icon_label.bind("<Enter>", lambda e: on_enter(e, new_icon_frame))
            new_icon_label.bind("<Leave>", lambda e: on_leave(e, new_icon_frame))
            new_icon_label.bind("<Button-1>", lambda e: webbrowser.open("https://www.patreon.com/Emy69"))
    
    def show_donors_modal(self):
        donors_modal = DonorsModal(self, self.tr)
        donors_modal.focus_set()

    def toggle_archivo_menu(self):
        if self.archivo_menu_frame and self.archivo_menu_frame.winfo_exists():
            self.archivo_menu_frame.destroy()
        else:
            self.close_all_menus()
            self.archivo_menu_frame = self.create_menu_frame([
                (self.tr("Configuraciones"), self.settings_window.open_settings),
                ("separator", None),
                (self.tr("Salir"), self.quit),
            ], x=0)


    def create_menu_frame(self, options, x):
        # Crear el marco del menú con fondo oscuro y borde de sombra para resaltar
        menu_frame = ctk.CTkFrame(self, corner_radius=5, fg_color="gray25", border_color="black", border_width=1)
        menu_frame.place(x=x, y=30)
        
        # Agregar sombra alrededor del menú
        menu_frame.configure(border_width=1, border_color="black")

        # Evitar la propagación del clic en el menú
        menu_frame.bind("<Button-1>", lambda e: "break")

        # Añadir opciones al menú con separación entre elementos
        for option in options:
            if option[0] == "separator":
                separator = ctk.CTkFrame(menu_frame, height=1, fg_color="gray50")
                separator.pack(fill="x", padx=5, pady=5)
                separator.bind("<Button-1>", lambda e: "break")
            elif option[1] is None:
                # Texto sin comando (por ejemplo, título de submenú)
                label = ctk.CTkLabel(menu_frame, text=option[0], anchor="w", fg_color="gray30")
                label.pack(fill="x", padx=5, pady=2)
                label.bind("<Button-1>", lambda e: "break")
            else:
                btn = ctk.CTkButton(
                    menu_frame,
                    text=option[0],
                    fg_color="transparent",
                    hover_color="gray35",
                    anchor="w",
                    text_color="white",
                    command=lambda cmd=option[1]: cmd()
                )
                btn.pack(fill="x", padx=5, pady=2)
                btn.bind("<Button-1>", lambda e: "break")

        return menu_frame

    def close_all_menus(self):
        for menu_frame in [self.archivo_menu_frame, self.ayuda_menu_frame, self.donaciones_menu_frame]:
            if menu_frame and menu_frame.winfo_exists():
                menu_frame.destroy()

    # Image processing
    def create_photoimage(self, path, size=(32, 32)):
        img = Image.open(path)
        img = img.resize(size, Image.Resampling.LANCZOS)
        photoimg = ImageTk.PhotoImage(img)
        return photoimg

    # Setup downloaders
    def setup_erome_downloader(self, is_profile_download=False):
        self.erome_downloader = self._register_downloader(EromeDownloader(
            root=self,
            enable_widgets_callback=self.enable_widgets,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, como Gecko) Chrome/58.0.3029.110 Safari/537.36',
                'Referer': 'https://www.erome.com/'
            },
            log_callback=self.add_log_message_safe,
            update_progress_callback=self.update_progress,
            update_global_progress_callback=self.update_global_progress,
            download_images=self.download_images_check.get(),
            download_videos=self.download_videos_check.get(),
            is_profile_download=is_profile_download,
            max_workers=self.max_downloads,
            tr=self.tr,
            request_timeout=self.request_timeout,
        ))

    def setup_simpcity_downloader(self):
        self.simpcity_downloader = self._register_downloader(SimpCity(
            download_folder=self.download_folder,
            log_callback=self.add_log_message_safe,
            enable_widgets_callback=self.enable_widgets,
            update_progress_callback=self.update_progress,
            update_global_progress_callback=self.update_global_progress,
            tr=self.tr,
            request_timeout=self.request_timeout,
            cookie_password_provider=self.get_simpcity_cookie_password,
            cookie_storage_allowed=bool(self.settings.get('save_simpcity_cookies', False)),
        ))

    def get_simpcity_cookie_password(self, purpose: Optional[str] = None):
        return self.simpcity_cookie_password

    def should_prompt_simpcity_cookie_password(self) -> bool:
        save_enabled = bool(self.settings.get('save_simpcity_cookies', False))
        return save_enabled or SIMPCITY_COOKIES_FILE.exists()

    def prompt_simpcity_cookie_password(self) -> bool:
        dialog = ctk.CTkInputDialog(
            title=self.tr("Contraseña de cookies de SimpCity"),
            text=self.tr("Introduce la contraseña usada para cifrar tus cookies de SimpCity. No se guardará."),
        )
        password = dialog.get_input()
        if password:
            self.simpcity_cookie_password = password.strip()
            return True
        return False

    def maybe_request_simpcity_cookie_password(self) -> bool:
        if not self.should_prompt_simpcity_cookie_password():
            return True
        if self.simpcity_cookie_password:
            return True
        return self.prompt_simpcity_cookie_password()

    def forget_simpcity_cookie_password(self):
        env_password = os.getenv("COOMERDL_COOKIES_PASSWORD")
        self.simpcity_cookie_password = env_password if env_password else None

    def setup_bunkr_downloader(self):
        self.bunkr_downloader = self._register_downloader(BunkrDownloader(
            download_folder=self.download_folder,
            log_callback=self.add_log_message_safe,
            enable_widgets_callback=self.enable_widgets,
            update_progress_callback=self.update_progress,
            update_global_progress_callback=self.update_global_progress,
            headers={
                'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)',
                'Referer': 'https://bunkr.site/',
            },
            max_workers=self.max_downloads,
            request_timeout=self.request_timeout,
        ))

    def setup_general_downloader(self):
        self.general_downloader = self._register_downloader(Downloader(
            download_folder=self.download_folder,
            log_callback=self.add_log_message_safe,
            enable_widgets_callback=self.enable_widgets,
            update_progress_callback=self.update_progress,
            update_global_progress_callback=self.update_global_progress,
            headers={
                'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)',
                'Referer': 'https://coomer.st/',
                "Accept": "text/css"
            },
            download_images=self.download_images_check.get(),
            download_videos=self.download_videos_check.get(),
            download_compressed=self.download_compressed_check.get(),
            tr=self.tr,
            max_workers=self.max_downloads,
            folder_structure=self.settings.get('folder_structure', 'default'),
            stream_read_timeout=self.request_timeout,
        ))
        self.general_downloader.file_naming_mode = self.settings.get('file_naming_mode', 0)

    def setup_jpg5_downloader(self):
        self.active_downloader = self._register_downloader(Jpg5Downloader(
            url=self.url_entry.get().strip(),
            carpeta_destino=self.download_folder,
            log_callback=self.add_log_message_safe,
            tr=self.tr,
            progress_manager=self.progress_manager,
            max_workers=self.max_downloads,
            request_timeout=self.request_timeout,
        ))

    # Folder selection
    def select_folder(self):
        folder_selected = filedialog.askdirectory()
        if folder_selected:
            self.download_folder = folder_selected
            self.folder_path.configure(text=folder_selected)
            self.save_download_folder(folder_selected)
    
    # Función para cargar y redimensionar imágenes
    def load_and_resize_image(self, path, size=(20, 20)):
        img = Image.open(path)
        return ctk.CTkImage(img, size=size)
    
    # Reemplaza las llamadas a los métodos de progreso con self.progress_manager
    def update_progress(self, downloaded, total,file_id=None, file_path=None,speed=None, eta=None, status=None):
        self.progress_manager.update_progress(downloaded, total,file_id, file_path,speed, eta, status=status)

    def remove_progress_bar(self, file_id):
        self.progress_manager.remove_progress_bar(file_id)

    def update_global_progress(self, completed_files, total_files):
        self.progress_manager.update_global_progress(completed_files, total_files)

    def toggle_progress_details(self):
        self.progress_manager.toggle_progress_details()

    def center_progress_details_frame(self):
        self.progress_manager.center_progress_details_frame()

    # Error logging
    def log_error(self, error_message):
        self.errors.append(error_message)
        self.add_log_message_safe(f"Error: {error_message}")

    def wrapped_download(self, download_method, *args):
        try:
            download_method(*args)
        finally:
            self.record_download_session()
            self.active_downloader = None
            self.enable_widgets()
            self.export_logs()
            self._unregister_current_download_thread()

    def record_download_session(self):
        context = self.current_download_context or {}
        downloader = self.active_downloader or self.cancelled_downloader_snapshot
        if not context and downloader is None:
            self.last_download_metadata = None
            self.last_history_session_id = None
            return

        finished_at = datetime.datetime.now()
        start_dt = context.get("started_at") or self.download_start_time
        if isinstance(start_dt, str):
            try:
                start_dt = datetime.datetime.fromisoformat(start_dt)
            except ValueError:
                start_dt = None
        duration_seconds = (finished_at - start_dt).total_seconds() if start_dt else None
        human_duration = None
        if duration_seconds is not None:
            human_duration = str(datetime.timedelta(seconds=int(duration_seconds)))

        total_files = int(getattr(downloader, "total_files", 0) or 0) if downloader else 0
        completed_files = int(getattr(downloader, "completed_files", 0) or 0) if downloader else 0

        skipped_attr = getattr(downloader, "skipped_files", []) if downloader else []
        failed_attr = getattr(downloader, "failed_files", []) if downloader else []

        def _normalize_list(value):
            if isinstance(value, list):
                return [str(item) for item in value]
            if isinstance(value, tuple) or isinstance(value, set):
                return [str(item) for item in value]
            return []

        skipped_files = _normalize_list(skipped_attr)
        failed_files = _normalize_list(failed_attr)

        skipped_count = len(skipped_files) if skipped_files else int(skipped_attr or 0) if isinstance(skipped_attr,
                                                                                                      int) else 0
        failed_count = len(failed_files) if failed_files else int(failed_attr or 0) if isinstance(failed_attr,
                                                                                                  int) else 0

        cancelled = False
        if downloader is not None:
            cancel_attr = getattr(downloader, "cancel_requested", None)
            if isinstance(cancel_attr, threading.Event):
                cancelled = cancel_attr.is_set()
            elif hasattr(cancel_attr, "is_set") and callable(getattr(cancel_attr, "is_set")):
                try:
                    cancelled = cancel_attr.is_set()
                except Exception:
                    cancelled = bool(cancel_attr)
            elif callable(cancel_attr):
                try:
                    cancelled = bool(cancel_attr())
                except TypeError:
                    cancelled = bool(cancel_attr)
            elif cancel_attr is not None:
                cancelled = bool(cancel_attr)

        status = "cancelled" if cancelled else "completed"

        session_id = finished_at.strftime("%Y%m%d%H%M%S%f")
        counts = {
            "total": total_files,
            "completed": completed_files,
            "skipped": skipped_count,
            "failed": failed_count,
        }

        options = context.get("options") or {}
        extra = context.get("extra") or {}

        metadata = {
            "session_id": session_id,
            "url": context.get("url"),
            "service": context.get("service"),
            "site": context.get("site"),
            "user": context.get("user"),
            "mode": context.get("mode"),
            "status": status,
            "started_at": start_dt.isoformat() if start_dt else None,
            "finished_at": finished_at.isoformat(),
            "duration_seconds": round(duration_seconds, 2) if duration_seconds is not None else None,
            "duration_human": human_duration,
            "counts": counts,
            "skipped_files": skipped_files,
            "failed_files": failed_files,
            "options": options,
            "extra": extra,
            "log_entries": len(self.all_logs) if hasattr(self, "all_logs") and self.all_logs else 0,
        }

        if self.download_history is None:
            self.download_history = []

        existing_entry = next((entry for entry in self.download_history if entry.get("session_id") == session_id), None)
        if existing_entry:
            existing_entry.update(metadata)
        else:
            self.download_history.append(metadata)

        self.last_download_metadata = metadata
        self.last_history_session_id = session_id
        self.save_download_history()
        self.refresh_history_filters()
        self.update_history_display()
        self.current_download_context = None
        self.download_start_time = None
        self.cancelled_downloader_snapshot = None

    def perform_ck_preflight(self, site, service, user, post_id, query, initial_offset):
        try:
            if post_id is not None:
                posts = self.general_downloader.fetch_user_posts(
                    site,
                    user,
                    service,
                    specific_post_id=post_id,
                    log_fetching=False,
                )
                if not posts:
                    messagebox.showwarning(
                        self.tr("Error"),
                        self.tr("No information was found for the requested post.")
                    )
                    return None
                post_info = posts[0]
                title = post_info.get('title') or self.tr("Untitled")
                attachments = post_info.get("attachments") or []
                main_file = post_info.get("file")
                attachments_count = len(attachments) + (1 if main_file else 0)
                message = self.tr(
                    "Service: {service}\nUser: {user}\nPost: {title} (ID: {post_id})\nAttachments: {count}\nDo you want to continue?",
                    service=service,
                    user=user,
                    title=title,
                    post_id=post_id,
                    count=attachments_count,
                )
                if not messagebox.askyesno(self.tr("Confirm Download"), message):
                    return None
                return {"selected_posts": [post_id], "total_posts": 1}

            posts = self.general_downloader.fetch_user_posts(
                site,
                user,
                service,
                query=query,
                initial_offset=initial_offset,
                log_fetching=False,
            )
            if not posts:
                messagebox.showerror(
                    self.tr("Error"),
                    self.tr("No posts were found for this user."),
                )
                return None

            selection_dialog = PostSelectionDialog(
                self,
                posts,
                self.tr,
                user,
                service,
                site,
                log_callback=self.add_log_message_safe,
            )
            confirmed, selected_posts = selection_dialog.show()
            if not confirmed:
                return None
            if len(selected_posts) == len(posts):
                selected_payload = None
            else:
                selected_payload = selected_posts
            return {"selected_posts": selected_payload, "total_posts": len(posts)}
        except Exception as exc:
            self.add_log_message_safe(self.tr("Error in previous verification: {error}", error=str(exc)))
            messagebox.showerror(self.tr("Error"), str(exc))
            return None

    # Download management
    def start_download(self):
        url = self.url_entry.get().strip()
        if not hasattr(self, 'download_folder') or not self.download_folder:
            messagebox.showerror(self.tr("Error"), self.tr("Por favor, selecciona una carpeta de descarga."))
            self.reset_pause_controls()
            return

        self.download_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.pause_button.configure(state="normal")
        self.resume_button.configure(state="disabled")
        self.download_start_time = datetime.datetime.now()
        self.last_download_metadata = None
        self.last_history_session_id = None
        self.errors = []
        download_all = True

        self.current_download_context = {
            "url": url,
            "started_at": self.download_start_time,
            "options": {
                "images": bool(self.download_images_check.get()),
                "videos": bool(self.download_videos_check.get()),
                "compressed": bool(self.download_compressed_check.get()),
            },
            "extra": {},
        }

        parsed_url = urlparse(url)
        
        if "erome.com" in url:
            self.add_log_message_safe(self.tr("Descargando Erome"))
            is_profile_download = "/a/" not in url
            self.setup_erome_downloader(is_profile_download=is_profile_download)
            self.active_downloader = self.erome_downloader
            self.current_download_context.update({
                "service": "Erome",
                "site": parsed_url.netloc,
                "mode": "profile" if is_profile_download else "album",
            })
            if "/a/" in url:
                self.add_log_message_safe(self.tr("URL del álbum"))
                download_thread = threading.Thread(target=self.wrapped_download, args=(self.active_downloader.process_album_page, url, self.download_folder, self.download_images_check.get(), self.download_videos_check.get()))
            else:
                self.add_log_message_safe(self.tr("URL del perfil"))
                download_thread = threading.Thread(target=self.wrapped_download, args=(self.active_downloader.process_profile_page, url, self.download_folder, self.download_images_check.get(), self.download_videos_check.get()))
        
        elif re.search(r"https?://([a-z0-9-]+\.)?bunkr\.[a-z]{2,}", url):
            self.add_log_message_safe(self.tr("Descargando Bunkr"))
            self.setup_bunkr_downloader()
            self.active_downloader = self.bunkr_downloader
            self.current_download_context.update({
                "service": "Bunkr",
                "site": parsed_url.netloc,
            })
            # Si la URL contiene "/v/", "/i/" o "/f/", la tratamos como un post individual.
            if any(sub in url for sub in ["/v/", "/i/", "/f/"]):
                self.add_log_message_safe(self.tr("URL del post"))
                self.current_download_context["mode"] = "post"
                download_thread = threading.Thread(target=self.wrapped_download, args=(self.bunkr_downloader.descargar_post_bunkr, url))
            else:
                self.add_log_message_safe(self.tr("URL del perfil"))
                self.current_download_context["mode"] = "profile"
                download_thread = threading.Thread(target=self.wrapped_download, args=(self.bunkr_downloader.descargar_perfil_bunkr, url))
        
        elif parsed_url.netloc in ["coomer.st", "kemono.cr"]:
            self.add_log_message_safe(self.tr("Iniciando descarga..."))
            self.setup_general_downloader()
            self.active_downloader = self.general_downloader

            site = f"{parsed_url.netloc}"
            service, user, post = extract_ck_parameters(parsed_url)
            if service is None or user is None:
                if service is None:
                    self.add_log_message_safe(self.tr("No se pudo extraer el servicio."))
                    messagebox.showerror(self.tr("Error"), self.tr("No se pudo extraer el servicio."))
                else:
                    self.add_log_message_safe(self.tr("No se pudo extraer el ID del usuario."))
                    messagebox.showerror(self.tr("Error"), self.tr("No se pudo extraer el ID del usuario."))

                self.add_log_message_safe(self.tr("URL no válida"))
                self.download_button.configure(state="normal")
                self.cancel_button.configure(state="disabled")
                self.reset_pause_controls()
                self.current_download_context = None
                return

            self.add_log_message_safe(self.tr("Servicio extraído: {service} del sitio: {site}", service=service, site=site))

            self.current_download_context.update({
                "service": service,
                "site": site,
                "user": user,
                "mode": "post" if post is not None else "profile",
            })

            query, offset = extract_ck_query(parsed_url)

            self.current_download_context["extra"].update({
                "query": query,
                "initial_offset": offset,
            })

            preflight_enabled = bool(self.enable_preflight_check.get())
            preflight_data = None
            selected_posts = None
            download_all = True
            if preflight_enabled:
                preflight_data = self.perform_ck_preflight(site, service, user, post, query, offset)
                if not preflight_data:
                    self.download_button.configure(state="normal")
                    self.cancel_button.configure(state="disabled")
                    self.active_downloader = None
                    self.current_download_context = None
                    self.reset_pause_controls()
                    return
                selected_posts = preflight_data.get("selected_posts")
                total_posts = preflight_data.get("total_posts", 0)
                download_all = selected_posts is None or len(selected_posts) == total_posts
                self.current_download_context["extra"].update({
                    "preflight_total_posts": total_posts,
                    "selected_post_count": len(selected_posts) if selected_posts else 0,
                    "download_all": download_all,
                })

            if post is not None:
                self.add_log_message_safe(self.tr("Descargando post único..."))
                download_thread = threading.Thread(target=self.wrapped_download, args=(self.start_ck_post_download, site, service, user, post))
            else:
                self.add_log_message_safe(self.tr("Descargando todo el contenido del usuario..."))
                total_posts = preflight_data.get("total_posts", 0) if preflight_data else 0
                self.current_download_context["extra"].update({"target_post_count": total_posts})
                download_thread = threading.Thread(
                    target=self.wrapped_download,
                    args=(
                        self.start_ck_profile_download,
                        site,
                        service,
                        user,
                        query,
                        download_all,
                        offset,
                        selected_posts,
                    ),
                )
        
        elif "simpcity.su" in url:
            self.add_log_message_safe(self.tr("Descargando SimpCity"))
            if not self.maybe_request_simpcity_cookie_password():
                self.add_log_message_safe(
                    self.tr("Descarga de SimpCity cancelada: no se proporcionó contraseña de cookies."))
                self.download_button.configure(state="normal")
                self.cancel_button.configure(state="disabled")
                self.reset_pause_controls()
                self.current_download_context = None
                return
            self.setup_simpcity_downloader()
            self.active_downloader = self.simpcity_downloader
            self.current_download_context.update({
                "service": "SimpCity",
                "site": parsed_url.netloc,
                "mode": "profile",
            })
            # Iniciar la descarga en un hilo separado
            download_thread = threading.Thread(target=self.wrapped_download, args=(self.active_downloader.download_images_from_simpcity, url))
        
        elif "jpg5.su" in url:
            self.add_log_message_safe(self.tr("Descargando desde Jpg5"))
            self.setup_jpg5_downloader()
            self.current_download_context.update({
                "service": "Jpg5",
                "site": parsed_url.netloc,
                "mode": "gallery",
            })
            
            # Usar wrapped_download para manejar la descarga
            download_thread = threading.Thread(target=self.wrapped_download, args=(self.active_downloader.descargar_imagenes,))
        
        else:
            self.add_log_message_safe(self.tr("URL no válida"))
            self.download_button.configure(state="normal")
            self.cancel_button.configure(state="disabled")
            self.reset_pause_controls()
            self.current_download_context = None
            return

        self._register_download_thread(download_thread)
        download_thread.start()

    def start_ck_profile_download(self, site, service, user, query, download_all, initial_offset, selected_posts):
        download_info = self.active_downloader.download_media(
            site,
            user,
            service,
            query=query,
            download_all=download_all,
            initial_offset=initial_offset,
            selected_post_ids=selected_posts,
        )
        if download_info:
            self.add_log_message_safe(f"Download info: {download_info}")

    
    def start_ck_post_download(self, site, service, user, post):
        download_info = self.active_downloader.download_single_post(site, post, service, user)
        if download_info:
            self.add_log_message_safe(f"Download info: {download_info}")

    def extract_user_id(self, url):
        self.add_log_message_safe(self.tr("Extrayendo ID del usuario del URL: {url}", url=url))
        match = re.search(r'/user/([^/?]+)', url)
        if match:
            user_id = match.group(1)
            self.add_log_message_safe(self.tr("ID del usuario extraído: {user_id}", user_id=user_id))
            return user_id
        else:
            self.add_log_message_safe(self.tr("No se pudo extraer el ID del usuario."))
            messagebox.showerror(self.tr("Error"), self.tr("No se pudo extraer el ID del usuario."))
            return None

    def extract_post_id(self, url):
        match = re.search(r'/post/([^/?]+)', url)
        if match:
            post_id = match.group(1)
            self.add_log_message_safe(self.tr("ID del post extraído: {post_id}", post_id=post_id))
            return post_id
        else:
            self.add_log_message_safe(self.tr("No se pudo extraer el ID del post."))
            messagebox.showerror(self.tr("Error"), self.tr("No se pudo extraer el ID del post."))
            return None

    def cancel_download(self):
        if self.active_downloader:
            self.cancelled_downloader_snapshot = self.active_downloader
            if self.current_download_context and isinstance(self.current_download_context, dict):
                self.current_download_context.setdefault("extra", {})["cancel_requested"] = True
            self.active_downloader.request_cancel()
            self.active_downloader = None
            self.clear_progress_bars()
        else:
            self.add_log_message_safe(self.tr("No hay una descarga en curso para cancelar."))
        self.reset_pause_controls()
        self.enable_widgets()

    def pause_download(self):
        if not self.active_downloader:
            self.add_log_message_safe(self.tr("There is no active download to pause."))
            return
        if hasattr(self.active_downloader, "request_pause"):
            self.active_downloader.request_pause()
            if getattr(self.active_downloader, "is_paused", False):
                self.pause_button.configure(state="disabled")
                self.resume_button.configure(state="normal")
                self.add_log_message_safe(self.tr("Download paused."))
            else:
                self.add_log_message_safe(self.tr("The current download could not be paused."))
        else:
            self.add_log_message_safe(self.tr("The current download does not support pausing."))

    def resume_download(self):
        if not self.active_downloader:
            self.add_log_message_safe(self.tr("There is no active download to resume."))
            return
        if hasattr(self.active_downloader, "request_resume"):
            self.active_downloader.request_resume()
            if not getattr(self.active_downloader, "is_paused", False):
                self.pause_button.configure(state="normal")
                self.resume_button.configure(state="disabled")
                self.add_log_message_safe(self.tr("Download resumed."))
            else:
                self.add_log_message_safe(self.tr("The current download could not be resumed."))
        else:
            self.add_log_message_safe(self.tr("The current download cannot be resumed."))

    def clear_progress_bars(self):
        for file_id in list(self.progress_bars.keys()):
            self.remove_progress_bar(file_id)

    # Log messages safely
    def add_log_message_safe(self, message: str):
        # Asegura estructuras
        if not hasattr(self, "errors") or self.errors is None:
            self.errors = []
        self.errors.append(message)
        if hasattr(self, "all_logs") and self.all_logs is not None:
            self.all_logs.append(message)

        # Intenta escribir en el textbox si existe; si no, bufferiza
        try:
            if hasattr(self, "log_textbox") and self.log_textbox:
                self.log_textbox.configure(state="normal")
                self.log_textbox.insert("end", message + "\n")
                # self.log_textbox.configure(state="disabled")
                self.log_textbox.see("end")
                self.log_textbox.configure(state="disabled")
            else:
                # aún no existe el textbox; guardamos
                if not hasattr(self, "_log_buffer") or self._log_buffer is None:
                    self._log_buffer = []
                self._log_buffer.append(message)
        except Exception:
            # ante cualquier problema, también bufferiza
            if not hasattr(self, "_log_buffer") or self._log_buffer is None:
                self._log_buffer = []
            self._log_buffer.append(message)


    def limit_log_lines(self):
        log_lines = self.log_textbox.get("1.0", "end-1c").split("\n")
        if len(log_lines) > MAX_LOG_LINES:
            # Quitamos solo las líneas que sobran
            overflow = len(log_lines) - MAX_LOG_LINES
            self.log_textbox.delete("1.0", f"{overflow}.0")


    # Export logs to a file
    def export_logs(self):
        log_folder = Path("resources/config/logs/")
        log_folder.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        log_file_path = log_folder / f"log_{timestamp}.txt"
        metadata = copy.deepcopy(self.last_download_metadata) if self.last_download_metadata else None
        try:
            summary_lines = []
            skipped_files = []
            failed_files = []
            counts = {}

            if metadata:
                counts = metadata.get("counts", {}) or {}
                skipped_files = metadata.get("skipped_files") or []
                failed_files = metadata.get("failed_files") or []

                summary_lines.extend([
                    f"Service: {metadata.get('service', 'Unknown')}",
                    f"Site: {metadata.get('site', 'Unknown')}",
                    f"URL: {metadata.get('url', 'N/A')}",
                    f"User: {metadata.get('user', 'N/A')}",
                    f"Mode: {metadata.get('mode', 'N/A')}",
                    f"Status: {metadata.get('status', 'completed')}",
                    f"Started at: {metadata.get('started_at', 'N/A')}",
                    f"Finished at: {metadata.get('finished_at', 'N/A')}",
                    f"Duration (seconds): {metadata.get('duration_seconds', 'N/A')}",
                    f"Duration (human): {metadata.get('duration_human', 'N/A')}",
                    f"Total files: {counts.get('total', 0)}",
                    f"Completed files: {counts.get('completed', 0)}",
                    f"Skipped files: {counts.get('skipped', 0)}",
                    f"Failed files: {counts.get('failed', 0)}",
                ])

                options = metadata.get("options") or {}
                if options:
                    summary_lines.append("Options:")
                    for key, value in options.items():
                        summary_lines.append(f"  {key}: {value}")

                extra = metadata.get("extra") or {}
                if extra:
                    summary_lines.append("Extra:")
                    for key, value in extra.items():
                        summary_lines.append(f"  {key}: {value}")
            else:
                summary_lines.append("No metadata available for this session.")

                skipped_summary = "\n".join(skipped_files) if skipped_files else "None"
                failed_summary = "\n".join(failed_files) if failed_files else "None"

                summary_lines.append("")
                summary_lines.append("Skipped files:")
                summary_lines.append(skipped_summary)
                summary_lines.append("")
                summary_lines.append("Failed files:")
                summary_lines.append(failed_summary)

            with open(log_file_path, 'w', encoding='utf-8') as file:
                file.write("\n".join(summary_lines))
                file.write("\n\n--- COMPLETE LOGS ---\n")
                if hasattr(self, "all_logs") and self.all_logs:
                    file.write("\n".join(self.all_logs))

            metadata_file_path = None
            if metadata:
                metadata.setdefault("counts", counts)
                metadata["log_file"] = str(log_file_path)
                metadata_file_path = log_folder / f"log_{timestamp}.json"
                with open(metadata_file_path, 'w', encoding='utf-8') as metadata_file:
                    json.dump(metadata, metadata_file, ensure_ascii=False, indent=2)
                self.last_download_metadata = metadata

                if self.last_history_session_id:
                    for entry in reversed(self.download_history):
                        if entry.get("session_id") == self.last_history_session_id:
                            entry["log_file"] = str(log_file_path)
                            if metadata_file_path:
                                entry["metadata_file"] = str(metadata_file_path)
                            break
                    self.save_download_history()

            self.add_log_message_safe(f"Logs exportados exitosamente a {log_file_path}")
        except Exception as e:
            self.add_log_message_safe(f"No se pudo exportar los logs: {e}")

    def load_download_history(self):
        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            if self.history_file.exists():
                with open(self.history_file, 'r', encoding='utf-8') as file:
                    data = json.load(file)
                    if isinstance(data, list):
                        return data
        except Exception as exc:
            try:
                self.add_log_message_safe(f"No se pudo cargar el historial de descargas: {exc}")
            except Exception:
                print(f"No se pudo cargar el historial de descargas: {exc}")
        return []

    def save_download_history(self):
        if self.download_history is None:
            return
        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.history_file, 'w', encoding='utf-8') as file:
                json.dump(self.download_history, file, ensure_ascii=False, indent=2)
        except Exception as exc:
            try:
                self.add_log_message_safe(f"No se pudo guardar el historial de descargas: {exc}")
            except Exception:
                print(f"No se pudo guardar el historial de descargas: {exc}")

    def on_history_service_selected(self, choice):
        selected_value = self.history_service_display_map.get(choice, "__all__") if hasattr(self,
                                                                                            "history_service_display_map") else "__all__"
        self.history_service_filter_value = selected_value or "__all__"
        self.update_history_display()

    def refresh_history_filters(self):
        if not hasattr(self, "history_service_filter"):
            return
        services = sorted({entry.get("service") or "" for entry in (self.download_history or [])})
        display_map = {self.tr("All Services"): "__all__"}
        for service in services:
            display_name = service if service else self.tr("Unknown Service")
            display_map[display_name] = service
        self.history_service_display_map = display_map
        values = list(display_map.keys())
        current_display = next(
            (display for display, value in display_map.items() if value == self.history_service_filter_value), None)
        if not current_display and values:
            current_display = values[0]
            self.history_service_filter_value = display_map[current_display]
        self.history_service_filter.configure(values=values)
        if current_display:
            self.history_service_filter.set(current_display)

    def update_history_display(self):
        if not hasattr(self, "history_list_frame"):
            return

        for widget in self.history_list_frame.winfo_children():
            widget.destroy()

        entries = list(self.download_history or [])
        total_entries = len(entries)
        query = ""
        if hasattr(self, "history_search_var") and self.history_search_var:
            query = self.history_search_var.get().strip().lower()

        service_filter = getattr(self, "history_service_filter_value", "__all__")

        filtered_entries = []
        for entry in reversed(entries):
            service_value = entry.get("service") or ""
            matches_service = service_filter == "__all__" or service_value == service_filter
            if not matches_service:
                continue
            if query:
                searchable = [
                    service_value,
                    entry.get("site", ""),
                    entry.get("user", ""),
                    entry.get("status", ""),
                    entry.get("url", ""),
                ]
                if not any(query in str(value).lower() for value in searchable):
                    continue
            filtered_entries.append(entry)

        if hasattr(self, "history_results_label"):
            if total_entries:
                results_text = self.tr("Showing {count} of {total} sessions").format(count=len(filtered_entries),
                                                                                     total=total_entries)
            else:
                results_text = self.tr("No sessions recorded yet.")
            self.history_results_label.configure(text=results_text)

        if not filtered_entries:
            if total_entries:
                empty_text = self.tr("No sessions match the current filters.")
            else:
                empty_text = self.tr("No sessions recorded yet.")
            empty_label = ctk.CTkLabel(self.history_list_frame, text=empty_text, justify='left', anchor='w')
            empty_label.pack(fill='x', padx=5, pady=5)
            return

        for entry in filtered_entries:
            item_frame = ctk.CTkFrame(self.history_list_frame)
            item_frame.pack(fill='x', padx=5, pady=5)

            status = entry.get("status", "completed")
            header = f"{entry.get('service', 'Unknown')} - {status.title()}"
            finished_at = entry.get("finished_at")
            if finished_at:
                try:
                    finished_display = datetime.datetime.fromisoformat(finished_at).strftime('%Y-%m-%d %H:%M:%S')
                except ValueError:
                    finished_display = finished_at
                header += f"\n{finished_display}"

            header_label = ctk.CTkLabel(item_frame, text=header, justify='left', anchor='w')
            header_label.pack(fill='x', padx=5, pady=(5, 2))

            url_value = entry.get("url") or "N/A"
            url_display = url_value if len(url_value) <= 100 else f"{url_value[:97]}..."
            url_label = ctk.CTkLabel(item_frame, text=url_display, justify='left', anchor='w', font=("Arial", 11))
            url_label.pack(fill='x', padx=5, pady=(0, 2))

            counts = entry.get("counts") or {}
            duration_text = entry.get("duration_human") or entry.get("duration_seconds") or "N/A"
            counts_text = self.tr(
                "Completed {completed}/{total} • Skipped {skipped} • Failed {failed} • Duration {duration}").format(
                completed=counts.get('completed', 0),
                total=counts.get('total', 0),
                skipped=counts.get('skipped', 0),
                failed=counts.get('failed', 0),
                duration=duration_text,
            )
            counts_label = ctk.CTkLabel(item_frame, text=counts_text, justify='left', anchor='w', font=("Arial", 11))
            counts_label.pack(fill='x', padx=5, pady=(0, 5))

    # Clipboard operations
    def copy_to_clipboard(self):
        try:
            selected_text = self.url_entry.selection_get()
            if selected_text:
                self.clipboard_clear()
                self.clipboard_append(selected_text)
            else:
                self.add_log_message_safe(self.tr("No hay texto seleccionado para copiar."))
        except tk.TclError:
            self.add_log_message_safe(self.tr("No hay texto seleccionado para copiar."))

    def paste_from_clipboard(self):
        try:
            clipboard_text = self.clipboard_get()
            if clipboard_text:
                try:
                    self.url_entry.delete("sel.first", "sel.last")  # Elimina el texto seleccionado si hay alguno
                except tk.TclError:
                    pass
                self.url_entry.insert(tk.INSERT, clipboard_text)
            else:
                self.add_log_message_safe(self.tr("No hay texto en el portapapeles para pegar."))
        except tk.TclError as e:
            self.add_log_message_safe(self.tr(f"Error al pegar desde el portapapeles: {e}"))

    def cut_to_clipboard(self):
        try:
            selected_text = self.url_entry.selection_get()
            if selected_text:
                self.clipboard_clear()
                self.clipboard_append(selected_text)
                self.url_entry.delete("sel.first", "sel.last")
            else:
                self.add_log_message_safe(self.tr("No hay texto seleccionado para cortar."))
        except tk.TclError:
            self.add_log_message_safe(self.tr("No hay texto seleccionado para cortar."))


    # Show context menu
    def show_context_menu(self, event):
        self.context_menu.tk_popup(event.x_root, event.y_root)
        self.context_menu.grab_release()

    # Update queue
    def check_update_queue(self):
        while not self.update_queue.empty():
            task = self.update_queue.get_nowait()
            task()
        self.after(100, self.check_update_queue)

    # Enable widgets
    def enable_widgets(self):
        self.update_queue.put(lambda: self.download_button.configure(state="normal"))
        self.update_queue.put(lambda: self.cancel_button.configure(state="disabled"))
        self.update_queue.put(self.reset_pause_controls)

    def reset_pause_controls(self):
        self.pause_button.configure(state="disabled")
        self.resume_button.configure(state="disabled")
    
    # Save and load download folder
    def save_download_folder(self, folder_path):
        config = {'download_folder': folder_path}
        with open('resources/config/download_path/download_folder.json', 'w') as config_file:
            json.dump(config, config_file)

    def load_download_folder(self):
        config_path = 'resources/config/download_path/download_folder.json'
        config_dir = Path(config_path).parent
        if not config_dir.exists():
            config_dir.mkdir(parents=True)
        if not Path(config_path).exists():
            with open(config_path, 'w') as config_file:
                json.dump({'download_folder': ''}, config_file)
        try:
            with open(config_path, 'r') as config_file:
                config = json.load(config_file)
                return config.get('download_folder', '')
        except json.JSONDecodeError:
            return ''

    # Update max downloads
    def update_max_downloads(self, max_downloads):
        self.max_downloads = max_downloads
        if hasattr(self, 'general_downloader'):
            self.general_downloader.max_workers = max_downloads
        if hasattr(self, 'erome_downloader'):
            self.erome_downloader.max_workers = max_downloads
        if hasattr(self, 'bunkr_downloader'):
            self.bunkr_downloader.max_workers = max_downloads

    def on_hover_enter(self, event):
        self.folder_path.configure(font=("Arial", 13, "underline"))  # Subrayar el texto al pasar el ratón

    def on_hover_leave(self, event):
        self.folder_path.configure(font=("Arial", 13))  # Quitar el subrayado al salir el ratón

    def get_github_stars(self, user: str, repo: str, timeout: float = 2.5) -> int:
        try:
            url = f"https://api.github.com/repos/{user}/{repo}"
            headers = {
                "User-Agent": "CoomerDL",
                "Accept": "application/vnd.github+json",
            }
            r = requests.get(url, headers=headers, timeout=timeout)
            r.raise_for_status()
            data = r.json()
            return int(data.get("stargazers_count", 0))
        except Exception:
            # No rompas el arranque si no hay internet
            self.add_log_message_safe(self.tr("Offline mode: GitHub stars could not be retrieved."))
            return 0

    def load_icon(self, icon_path, icon_name):
        try:
            img = Image.open(icon_path)
            return img  # Devuelve la imagen de PIL
        except Exception as e:
            self.add_log_message_safe(f"Error al cargar el icono {icon_name}: {e}")
            return None

    # Uso de la función genérica para cargar íconos específicos
    def load_github_icon(self):
        return self.load_icon("resources/img/iconos/ui/social/github-logo-24.png", "GitHub")

    def load_discord_icon(self):
        return self.load_icon("resources/img/iconos/ui/social/discord-alt-logo-24.png", "Discord")

    def load_patreon_icon(self):
        return self.load_icon("resources/img/iconos/ui/social/patreon-logo-24.png", "New Icon")

    def parse_version_string(self, version_str):
      # Removes 'V' prefix and splits by '.'
      try:
          return tuple(int(p) for p in version_str[1:].split('.'))
      except (ValueError, IndexError):
          return (0, 0, 0) # Fallback for invalid format

    def check_for_new_version(self, startup_check=False):
        repo_owner = "emy69"
        repo_name = "CoomerDL"
        github_api_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/releases/latest"
        
        try:
            response = requests.get(github_api_url, timeout=self.request_timeout)
            response.raise_for_status() # Raise an exception for HTTP errors
            latest_release = response.json()
            
            latest_tag = latest_release.get("tag_name")
            latest_url = latest_release.get("html_url")

            if latest_tag and latest_url:
                # Use the global VERSION constant directly
                current_version_parsed = self.parse_version_string(VERSION) 
                latest_version_parsed = self.parse_version_string(latest_tag)

                if latest_version_parsed > current_version_parsed:
                    self.latest_release_url = latest_url
                    # Use functools.partial to ensure 'self' is correctly bound
                    self.after(0, functools.partial(self.show_update_alert, latest_tag))
                    if not startup_check:
                        self.after(0, lambda: messagebox.showinfo(
                            self.tr("Update Available"),
                            self.tr("A new version ({latest_tag}) is available! Please download it from GitHub.", latest_tag=latest_tag)
                        ))
                else:
                    if not startup_check:
                        self.after(0, lambda: messagebox.showinfo(
                            self.tr("No Updates"),
                            self.tr("You are running the latest version.")
                        ))
            else:
                if not startup_check:
                    self.after(0, lambda: messagebox.showwarning(
                        self.tr("Update Check Failed"),
                        self.tr("Could not retrieve latest version information from GitHub.")
                    ))
        except requests.exceptions.RequestException as e:
            if self._is_offline_error(e):
                self.add_log_message_safe(self.tr("Offline mode: could not check for updates."))
                self.after(0, lambda: messagebox.showinfo(
                    self.tr("No Internet connection"),
                    self.tr("We couldn't check for updates. You may not be connected to the Internet right now.\n\nThe app will continue to work in offline mode.")
                ))
            else:
                self.add_log_message_safe(f"Error checking for updates: {e}")
                if not startup_check:
                    self.after(0, lambda: messagebox.showerror(
                        self.tr("Network Error"),
                        self.tr("Could not connect to GitHub to check for updates. Please check your internet connection.")
                    ))
        except Exception as e:
            self.add_log_message_safe(f"An unexpected error occurred during update check: {e}")
            if not startup_check:
                self.after(0, lambda: messagebox.showerror(
                    self.tr("Error"),
                    self.tr("An unexpected error occurred during update check.")
                ))

    def show_update_alert(self, latest_tag):
        self.update_alert_label.configure(text=self.tr("New version ({latest_tag}) available!", latest_tag=latest_tag))
        self.update_alert_frame.pack(side="top", fill="x")
        # Re-pack other elements to ensure they are below the alert
        self.input_frame.pack_forget()
        self.input_frame.pack(fill='x', padx=20, pady=20)
        self.options_frame.pack_forget()
        self.options_frame.pack(pady=10, fill='x', padx=20)
        self.action_frame.pack_forget()
        self.action_frame.pack(pady=10, fill='x', padx=20)
        # self.log_textbox.pack_forget()
        # self.log_textbox.pack(pady=(10, 0), padx=20, fill='both', expand=True)
        if hasattr(self, "log_history_container"):
            self.log_history_container.pack_forget()
            self.log_history_container.pack(pady=(10, 0), padx=20, fill='both', expand=True)
        self.progress_frame.pack_forget()
        self.progress_frame.pack(pady=(0, 10), fill='x', padx=20)

    def open_latest_release(self):
        if hasattr(self, 'latest_release_url'):
            webbrowser.open(self.latest_release_url)
        else:
            messagebox.showwarning(self.tr("No Release Found"), self.tr("No latest release URL available."))

    def _is_offline_error(self, err: Exception) -> bool:
        s = str(err)
        return (
            isinstance(err, requests.exceptions.ConnectionError)
            or "NameResolutionError" in s
            or "getaddrinfo failed" in s
            or "Failed to establish a new connection" in s
            or "Max retries exceeded" in s
        )

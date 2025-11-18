import datetime
import os
import re
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

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
    def __init__(self, parent, posts, tr, user_id, service, site):
        super().__init__(parent)
        self.parent = parent
        self.title(tr("Select posts"))
        self.geometry("700x900")
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
        self._post_tags = {}
        self._available_tags = set()
        self._post_entries = []
        self._no_results_label = None
        self._selected_tags = []

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
            normalized_tags = []
            if isinstance(tags, (list, tuple)):
                normalized_tags = [str(tag).strip() for tag in tags if str(tag).strip()]
                search_fragments.extend(normalized_tags)
                self._available_tags.update(normalized_tags)
            self._post_tags[post_id] = [tag.lower() for tag in normalized_tags]
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
            tr("Attachments (↓)"): ("attachments", True),
            tr("Attachments (↑)"): ("attachments", False),
            tr("Images (↓)"): ("images", True),
            tr("Images (↑)"): ("images", False),
            tr("Videos (↓)"): ("videos", True),
            tr("Videos (↑)"): ("videos", False),
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

        keyword_label = ctk.CTkLabel(keyword_filter_frame, text=tr("Keyword"))
        keyword_label.grid(row=0, column=0, padx=(0, 10), pady=(10, 5), sticky="w")

        self.keyword_entry = ctk.CTkEntry(keyword_filter_frame)
        self.keyword_entry.grid(row=0, column=1, padx=(0, 10), pady=(10, 5), sticky="ew")

        self.keyword_match_all = tk.BooleanVar(value=False)
        match_all_checkbox = ctk.CTkCheckBox(
            keyword_filter_frame,
            text=tr("Match all"),
            variable=self.keyword_match_all,
            onvalue=True,
            offvalue=False,
        )
        match_all_checkbox.grid(row=0, column=2, padx=(0, 10), pady=(10, 5), sticky="w")

        self.keyword_regex_mode = tk.BooleanVar(value=False)
        regex_checkbox = ctk.CTkCheckBox(
            keyword_filter_frame,
            text=tr("Use regex"),
            variable=self.keyword_regex_mode,
            onvalue=True,
            offvalue=False,
        )
        regex_checkbox.grid(row=0, column=3, padx=(0, 10), pady=(10, 5), sticky="w")

        keyword_filter_button = ctk.CTkButton(
            keyword_filter_frame,
            text=tr("Filter"),
            command=self.select_by_keywords,
            width=160,
        )
        keyword_filter_button.grid(row=0, column=4, padx=(0, 10), pady=(10, 5), sticky="ew")

        clear_keyword_button = ctk.CTkButton(
            keyword_filter_frame,
            text=tr("Clear"),
            command=self.clear_keyword_filter,
            width=140,
        )
        clear_keyword_button.grid(row=0, column=5, padx=(0, 10), pady=(10, 5), sticky="ew")

        save_preset_button = ctk.CTkButton(
            keyword_filter_frame,
            text=tr("Save preset"),
            command=self.save_filter_preset,
            width=160,
        )
        save_preset_button.grid(row=0, column=6, padx=(0, 10), pady=(10, 5), sticky="ew")

        keyword_filter_frame.grid_columnconfigure(1, weight=1)
        keyword_filter_frame.grid_columnconfigure(4, weight=1)
        keyword_filter_frame.grid_columnconfigure(5, weight=1)
        keyword_filter_frame.grid_columnconfigure(6, weight=1)

        if self._available_tags:
            tag_filter_frame = ctk.CTkFrame(self)
            tag_filter_frame.pack(fill="x", padx=20, pady=(0, 10))

            tag_label = ctk.CTkLabel(tag_filter_frame, text=tr("Tag filter"))
            tag_label.grid(row=0, column=0, padx=(0, 10), pady=(10, 5), sticky="w")

            tag_values = sorted(self._available_tags)
            self.tag_combobox = ctk.CTkComboBox(
                tag_filter_frame,
                values=tag_values,
                width=200,
            )
            self.tag_combobox.set(tag_values[0])
            self.tag_combobox.grid(row=0, column=1, padx=(0, 10), pady=(10, 5), sticky="w")

            add_tag_button = ctk.CTkButton(
                tag_filter_frame,
                text=tr("Add tag"),
                command=self.add_selected_tag,
                width=120,
            )
            add_tag_button.grid(row=0, column=2, padx=(0, 10), pady=(10, 5), sticky="ew")

            self.tag_match_all = tk.BooleanVar(value=False)
            tag_logic_checkbox = ctk.CTkCheckBox(
                tag_filter_frame,
                text=tr("Match all selected tags"),
                variable=self.tag_match_all,
                onvalue=True,
                offvalue=False,
            )
            tag_logic_checkbox.grid(row=0, column=3, padx=(0, 10), pady=(10, 5), sticky="w")

            tag_filter_button = ctk.CTkButton(
                tag_filter_frame,
                text=tr("Filter by tags"),
                command=self.select_by_tags,
                width=140,
            )
            tag_filter_button.grid(row=0, column=4, padx=(0, 10), pady=(10, 5), sticky="ew")

            clear_tag_button = ctk.CTkButton(
                tag_filter_frame,
                text=tr("Clear tags"),
                command=self.clear_tag_filter,
                width=120,
            )
            clear_tag_button.grid(row=0, column=5, padx=(0, 10), pady=(10, 5), sticky="ew")

            self.selected_tags_var = tk.StringVar(value="")
            selected_tags_label = ctk.CTkLabel(tag_filter_frame, textvariable=self.selected_tags_var, anchor="w")
            selected_tags_label.grid(row=1, column=0, columnspan=6, padx=(0, 10), pady=(0, 10), sticky="ew")

            tag_filter_frame.grid_columnconfigure(1, weight=1)
            tag_filter_frame.grid_columnconfigure(4, weight=1)
            tag_filter_frame.grid_columnconfigure(5, weight=1)

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
            text=tr("Select by range"),
            command=self.select_by_date_range,
            width=160,
        )
        filter_button.grid(row=0, column=2, rowspan=2, padx=(10, 0), pady=5, sticky="ew")

        clear_filter_button = ctk.CTkButton(
            date_filter_frame,
            text=tr("Clear"),
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
                text=tr("Year filter"),
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
        self._load_filter_preset()

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

        use_regex = bool(self.keyword_regex_mode.get())
        patterns = []
        if use_regex:
            for keyword in keywords:
                try:
                    patterns.append(re.compile(keyword, re.IGNORECASE))
                except re.error as exc:
                    messagebox.showerror(
                        self._tr("Error"),
                        self._tr("Invalid regular expression: {error}", error=str(exc)),
                    )
                    return

        match_all = bool(self.keyword_match_all.get())
        matched = False
        for post_id, var in self._checkbox_vars.items():
            haystack = self._post_search_texts.get(post_id, "")
            if not haystack:
                var.set(False)
                continue

            if use_regex:
                if match_all:
                    is_match = all(pattern.search(haystack) for pattern in patterns)
                else:
                    is_match = any(pattern.search(haystack) for pattern in patterns)
            else:
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
        self.keyword_regex_mode.set(False)
        self.select_all()

    def add_selected_tag(self):
        if not hasattr(self, "tag_combobox"):
            return
        selected_tag = self.tag_combobox.get().strip()
        if not selected_tag:
            return
        if selected_tag not in self._selected_tags:
            self._selected_tags.append(selected_tag)
            self._refresh_selected_tags_display()

    def select_by_tags(self):
        if not self._selected_tags:
            messagebox.showinfo(
                self._tr("Info"),
                self._tr("Please select at least one tag."),
            )
            return

        match_all = bool(getattr(self, "tag_match_all", tk.BooleanVar(value=False)).get())
        matched = False
        selected_tags_lower = [tag.lower() for tag in self._selected_tags]
        for post_id, var in self._checkbox_vars.items():
            post_tags = self._post_tags.get(post_id, [])
            if match_all:
                is_match = all(tag in post_tags for tag in selected_tags_lower)
            else:
                is_match = any(tag in post_tags for tag in selected_tags_lower)

            var.set(is_match)
            if is_match:
                matched = True

        if not matched:
            messagebox.showinfo(
                self._tr("Info"),
                self._tr("No posts matched the selected tags."),
            )

    def clear_tag_filter(self):
        self._selected_tags = []
        if hasattr(self, "selected_tags_var"):
            self.selected_tags_var.set("")
        if hasattr(self, "tag_match_all"):
            self.tag_match_all.set(False)
        self.select_all()

    def _refresh_selected_tags_display(self):
        if hasattr(self, "selected_tags_var"):
            self.selected_tags_var.set(self._tr("Selected tags: {tags}", tags=", ".join(self._selected_tags)))

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

        metrics = {"attachments": len(media_entries), "images": 0, "videos": 0}
        for entry in media_entries:
            source = str(entry.get("path") or entry.get("url") or entry.get("name") or "").lower()
            path = source.split("?")[0]
            _, ext = os.path.splitext(path)
            if ext in IMAGE_EXTENSIONS:
                metrics["images"] += 1
            elif ext in VIDEO_EXTENSIONS:
                metrics["videos"] += 1
        return metrics

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

    def _load_filter_preset(self):
        settings = getattr(self, "parent", None)
        settings_data = getattr(settings, "settings", None)
        if not isinstance(settings_data, dict):
            return

        preset = settings_data.get("post_selection_filter_preset")
        if not isinstance(preset, dict):
            return

        keyword_text = preset.get("keywords", "")
        if keyword_text:
            self.keyword_entry.insert(0, keyword_text)
        self.keyword_match_all.set(bool(preset.get("match_all_keywords", False)))
        self.keyword_regex_mode.set(bool(preset.get("regex_mode", False)))

        tag_logic = preset.get("match_all_tags", False)
        if hasattr(self, "tag_match_all"):
            self.tag_match_all.set(bool(tag_logic))

        preset_tags = preset.get("tags") or []
        for tag in preset_tags:
            if tag in self._available_tags and tag not in self._selected_tags:
                self._selected_tags.append(tag)
        self._refresh_selected_tags_display()

    def save_filter_preset(self):
        settings_host = getattr(self, "parent", None)
        settings_data = getattr(settings_host, "settings", None)
        if not isinstance(settings_data, dict):
            messagebox.showerror(self._tr("Error"), self._tr("Settings are unavailable."))
            return

        preset = {
            "keywords": self.keyword_entry.get().strip(),
            "match_all_keywords": bool(self.keyword_match_all.get()),
            "regex_mode": bool(self.keyword_regex_mode.get()),
            "tags": list(self._selected_tags),
            "match_all_tags": bool(getattr(self, "tag_match_all", tk.BooleanVar(value=False)).get()),
        }

        settings_data["post_selection_filter_preset"] = preset

        settings_window = getattr(settings_host, "settings_window", None)
        if settings_window is not None:
            if hasattr(settings_window, "settings"):
                settings_window.settings.update(settings_data)
            if hasattr(settings_window, "save_settings"):
                settings_window.save_settings()
                messagebox.showinfo(self._tr("Info"), self._tr("Filter preset saved."))
                return

        messagebox.showwarning(
            self._tr("Warning"),
            self._tr("Filter preset was stored temporarily but could not be persisted."),
        )

    def show(self):
        self.wait_window()
        return self._confirmed, self._result


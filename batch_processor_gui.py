import tkinter as tk
from tkinter import filedialog, messagebox
import os
import sys
import time
import threading
import json
import base64
import mimetypes

# Note: Integration with Google Cloud DLP (Data Loss Prevention)
from dlp_processor import ClinicalDocumentProcessor
import subprocess
import threading

HISTORY_FILE = "performance_history.json"

class LocalFileProcessorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Clinical Document Processor - Google DLP")
        self.root.geometry("600x700")

        self.source_folder = ""
        self.is_processing = False
        self.should_stop = False
        self.history_calibrated = False
        self.keywords_mapping = {None: []} # None key stores Global keywords
        self.current_selected_file = None
        
        # Window Close Protocol
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        # Load Config first
        self.config = self.load_config()
        
        # Estimation State & Buffering
        app_settings = self.config.get('app_settings', {})
        metrics = app_settings.get('performance_metrics', {})
        
        self.stats = {
            "avg_time_per_page": metrics.get("avg_time_per_page", 2.5),
            "avg_time_per_page_save": metrics.get("avg_time_per_page_save", 0.05),
            "avg_time_per_mb_load": metrics.get("avg_time_per_mb_load", 0.1),
            "last_ping": metrics.get("avg_ping_ms", 50),
            "total_pages_global": 0,
            "pages_done_global": 0,
            "total_size_mb_global": 0,
            "size_done_mb_global": 0
        }
        
        # Load History to refine statistics
        self.load_history()
        
        self.current_ping = 50
        self.gpu_name = "Detecting..."
        
        self.measurement_buffers = {
            "page_times": [],
            "save_times_per_mb": []
        }
        self.steps_since_calibration = 0
        
        self.detect_environment()

    def load_config(self):
        try:
            with open('config.json', 'r') as f:
                return json.load(f)
        except Exception as e:
            return {"app_settings": {}}

    def save_config(self):
        try:
            with open('config.json', 'w') as f:
                json.dump(self.config, f, indent=4)
        except Exception as e:
            print(f"Error saving config: {e}")

    def load_history(self):
        """Load past performance data and calculate Linear Regression coefficients (y = mx + b)"""
        try:
            if os.path.exists(HISTORY_FILE):
                with open(HISTORY_FILE, 'r') as f:
                    history = json.load(f)
                    if len(history) >= 2:
                        samples = history[-50:] # Use last 50 for relevancy
                        
                        # 1. Regression for Page Processing (m1, b1)
                        m1, b1 = self.calculate_regression([(s['pages'], s['pages'] * s['page_avg']) for s in samples])
                        self.stats["slope_page"] = m1
                        self.stats["intercept_page"] = b1
                        
                        # 2. Regression for Compiling/Saving (m2, b2)
                        m2, b2 = self.calculate_regression([(s['pages'], s['pages'] * s.get('save_pg_avg', 0.05)) for s in samples])
                        self.stats["slope_save"] = m2
                        self.stats["intercept_save"] = b2

                        # 3. Regression for Translation (m3, b3) - Based on Payload Size MB
                        trans_samples = [(s['trans_mb_total'], s['trans_time_total']) for s in samples if s.get('trans_mb_total', 0) > 0]
                        m3, b3 = self.calculate_regression(trans_samples) if trans_samples else (1.5, 0.5)
                        self.stats["slope_trans"] = m3
                        self.stats["intercept_trans"] = b3

                        # 4. Load MB average (mostly linear)
                        self.stats["avg_time_per_mb_load"] = sum(s.get('load_mb_avg', 0.1) for s in samples) / len(samples)
                        self.stats["last_ping"] = sum(s['ping'] for s in samples) / len(samples)
                        self.history_calibrated = True
                        print(f"Regression Calibration: {round(m1, 2)}s/pg (redact) + {round(m3, 2)}s/mb (trans)")
        except: pass

    def calculate_regression(self, data):
        """Perform Ordinary Least Squares: returns (slope, intercept)"""
        n = len(data)
        if n < 2: return 2.5, 0.5 # Defaults
        
        sum_x = sum(d[0] for d in data)
        sum_y = sum(d[1] for d in data)
        sum_xx = sum(d[0]**2 for d in data)
        sum_xy = sum(d[0]*d[1] for d in data)
        
        denominator = (n * sum_xx - sum_x**2)
        if denominator == 0: return (sum_y/sum_x if sum_x != 0 else 2.5), 0.5
        
        slope = (n * sum_xy - sum_x * sum_y) / denominator
        intercept = (sum_y - slope * sum_x) / n
        
        # Clamp to realistic values
        return max(0.1, slope), max(0.1, intercept)

    def append_history_sample(self, pages, size_mb, page_avg, save_pg_avg, load_mb_avg):
        """Store a new document's data into the history file"""
        sample = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "pages": pages,
            "size_mb": round(size_mb, 2),
            "page_avg": round(page_avg, 3),
            "save_pg_avg": round(save_pg_avg, 3),
            "load_mb_avg": round(load_mb_avg, 3),
            "trans_mb_total": round(self.stats.get("current_doc_trans_mb", 0), 2),
            "trans_time_total": round(self.stats.get("current_doc_trans_time", 0), 2),
            "trans_flatten_time": round(self.stats.get("current_doc_trans_flatten_time", 0), 2),
            "trans_api_time": round(self.stats.get("current_doc_trans_api_time", 0), 2),
            "ping": self.current_ping,
            "gpu": self.gpu_name
        }
        history = []
        try:
            if os.path.exists(HISTORY_FILE):
                with open(HISTORY_FILE, 'r') as f:
                    history = json.load(f)
        except: pass
        
        history.append(sample)
        try:
            with open(HISTORY_FILE, 'w') as f:
                json.dump(history, f, indent=4)
        except: pass

    def create_widgets(self):
        # 1. Folder Selection
        select_frame = tk.Frame(self.root, pady=10)
        select_frame.pack(fill=tk.X, padx=10)
        
        self.btn_select = tk.Button(select_frame, text="Select Data Folder", command=self.select_folder)
        self.btn_select.pack(side=tk.LEFT)

        self.lbl_folder = tk.Label(select_frame, text="No folder selected", fg="gray")
        self.lbl_folder.pack(side=tk.LEFT, padx=10)

        # 1.5 Custom Keywords Section (Chips UI)
        kw_section = tk.Frame(self.root, pady=5)
        kw_section.pack(fill=tk.X, padx=10)
        
        self.lbl_kw_target = tk.Label(kw_section, text="Global Redaction Keywords:", font=("Segoe UI", 9, "bold"), fg="#0277bd")
        self.lbl_kw_target.pack(anchor="w")
        
        input_frame = tk.Frame(kw_section)
        input_frame.pack(fill=tk.X, pady=2)
        
        self.entry_keyword = tk.Entry(input_frame, font=("Segoe UI", 10))
        self.entry_keyword.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.entry_keyword.bind("<Return>", self.add_keyword_event)
        self.entry_keyword.bind("<KeyRelease-,>", self.add_keyword_event)
        
        self.btn_add_kw = tk.Button(input_frame, text="Add", command=self.add_keyword, bg="#e1e1e1")
        self.btn_add_kw.pack(side=tk.LEFT, padx=5)
        
        # Chip Container
        self.chip_container = tk.Frame(kw_section)
        self.chip_container.pack(fill=tk.X, pady=5)
        
        tk.Label(kw_section, text="Press Enter or Comma to add. Click [X] to remove.", fg="gray", font=("Segoe UI", 8)).pack(anchor="w")

        # 2. Controls
        ctrl_frame = tk.Frame(self.root, pady=10)
        ctrl_frame.pack(fill=tk.X, padx=10)

        self.btn_start = tk.Button(ctrl_frame, text="Start Batch Processing", command=self.start_processing_thread, state=tk.DISABLED, bg="#dddddd", font=("Segoe UI", 10, "bold"))
        self.btn_start.pack(side=tk.LEFT)

        self.btn_stop = tk.Button(ctrl_frame, text="Stop", command=self.confirm_stop, state=tk.DISABLED, bg="#f5f5f5", font=("Segoe UI", 10, "bold"))
        self.btn_stop.pack(side=tk.LEFT, padx=10)
        
        # 3. Status Lists
        list_frame = tk.Frame(self.root, pady=10)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10)

        # Pending Files
        tk.Label(list_frame, text="Documents to Process:").grid(row=0, column=0, sticky="w")
        self.list_pending = tk.Listbox(list_frame, height=8, width=40, exportselection=False)
        self.list_pending.grid(row=1, column=0, padx=5, sticky="news")
        self.list_pending.bind("<<ListboxSelect>>", self.on_file_selected)

        # Processed Files
        tk.Label(list_frame, text="Completed Documents:").grid(row=0, column=1, sticky="w")
        self.list_processed = tk.Listbox(list_frame, height=8, width=40, exportselection=False)
        self.list_processed.grid(row=1, column=1, padx=5, sticky="news")
        self.list_processed.bind("<<ListboxSelect>>", self.on_file_selected)
        
        list_frame.columnconfigure(0, weight=1)
        list_frame.columnconfigure(1, weight=1)

        # 4. Log Box
        log_frame = tk.Frame(self.root, pady=5)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10)
        
        log_header = tk.Frame(log_frame)
        log_header.pack(fill=tk.X)
        
        tk.Label(log_header, text="Execution Log:").pack(side=tk.LEFT)
        
        self.time_var = tk.StringVar()
        self.time_var.set("")
        self.time_bar = tk.Label(log_header, textvariable=self.time_var, fg="#0277bd", font=("Consolas", 9, "bold"))
        self.time_bar.pack(side=tk.RIGHT)

        self.text_log = tk.Text(log_frame, height=20, state=tk.DISABLED)
        self.text_log.pack(fill=tk.BOTH, expand=True)

        # 5. Status Bar
        status_frame = tk.Frame(self.root, bd=1, relief=tk.SUNKEN)
        status_frame.pack(side=tk.BOTTOM, fill=tk.X)
        
        self.status_var = tk.StringVar()
        self.status_var.set("Ready")
        self.status_bar = tk.Label(status_frame, textvariable=self.status_var, anchor=tk.W)
        self.status_bar.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 6. Env Info
        self.env_var = tk.StringVar(value="GPU: Scanning... | Ping: --ms")
        self.env_bar = tk.Label(self.root, textvariable=self.env_var, fg="#757575", font=("Segoe UI", 8))
        self.env_bar.pack(side=tk.BOTTOM, anchor="e", padx=10)

    def on_closing(self):
        """Handle window X button click"""
        if self.is_processing:
            if messagebox.askyesno("Exit?", "Processing is still active. Are you sure you want to stop everything and exit?"):
                self.should_stop = True
                self.root.destroy()
        else:
            self.root.destroy()

    def confirm_stop(self):
        """Handle Stop button click"""
        if self.is_processing:
            if messagebox.askyesno("Stop Processing", "Are you sure you want to stop the batch processing?"):
                self.should_stop = True
                self.log_message("Stopping... finishing current document.")

    def detect_environment(self):
        """Detect GPU and Ping to adjust estimation formula"""
        def task():
            # 1. Detect GPU
            try:
                cmd = 'wmic path win32_VideoController get name'
                res = subprocess.check_output(cmd, shell=True).decode()
                lines = [l.strip() for l in res.split('\n') if l.strip() and 'Name' not in l]
                if lines: self.gpu_name = lines[0]
                else: self.gpu_name = "Software Rendering"
            except: self.gpu_name = "Standard VGA"

            # 2. Detect Ping to Google DLP
            try:
                # Ping dlp.googleapis.com (using 1 packet for speed)
                cmd = 'ping dlp.googleapis.com -n 1'
                res = subprocess.check_output(cmd, shell=True).decode()
                if "time=" in res:
                    ping = res.split("time=")[1].split("ms")[0]
                    self.current_ping = int(ping)
                elif "Average =" in res:
                     ping = res.split("Average =")[1].split("ms")[0]
                     self.current_ping = int(ping)
            except: self.current_ping = 100 # Default if blocked
            
            self.env_var.set(f"GPU: {self.gpu_name} | Ping: {self.current_ping}ms")
            
        threading.Thread(target=task, daemon=True).start()

    def log_message(self, message):
        # Intercept Metadata for estimation
        metadata = None
        if "[METADATA:" in message:
            try:
                msg_parts = message.split(" [METADATA:")
                message = msg_parts[0]
                meta_str = msg_parts[1].rstrip("]")
                import ast
                metadata = ast.literal_eval(meta_str)
            except: pass

        self.status_var.set(message)
        self.text_log.config(state=tk.NORMAL)
        
        # Smart Scroll: Check if we are at the very bottom before adding content
        y_scroll = self.text_log.yview()
        is_at_bottom = y_scroll[1] >= 0.995 # Stricter threshold for better control

        self.text_log.insert(tk.END, f"{time.strftime('%H:%M:%S')} - {message}\n")
        
        if is_at_bottom:
            self.text_log.see(tk.END)
            
        self.text_log.config(state=tk.DISABLED)
        
        if metadata:
            self.handle_metadata(metadata)
            
        # Trigger Recalibration every 10 log messages
        self.steps_since_calibration += 1
        if self.steps_since_calibration >= 10:
            self.recalibrate_estimation()
            self.steps_since_calibration = 0

        try:
            self.root.update()
        except: pass

    def recalibrate_estimation(self):
        """Refreshes the regression model with new samples from this session"""
        if self.measurement_buffers["page_times"] or self.measurement_buffers["save_times_per_mb"]:
            # Trigger a full history reload and regression calc
            self.load_history()
            
        if self.is_processing:
            pass

    def save_performance_metrics(self):
        """Saves current learned stats to config file for next startup"""
        if 'app_settings' not in self.config: self.config['app_settings'] = {}
        self.config['app_settings']['performance_metrics'] = {
            "avg_time_per_page": round(self.stats["avg_time_per_page"], 3),
            "avg_time_per_page_save": round(self.stats["avg_time_per_page_save"], 3),
            "avg_time_per_mb_load": round(self.stats["avg_time_per_mb_load"], 3),
            "avg_ping_ms": self.current_ping,
            "last_gpu": self.gpu_name
        }
        self.save_config()

    def handle_metadata(self, metadata):
        now = time.time()
        if "page_done" in metadata:
            self.stats["pages_done_global"] += 1
            if hasattr(self, '_page_start_time'):
                duration = now - self._page_start_time
                self.measurement_buffers["page_times"].append(duration)
            self._page_start_time = now
        
        elif "save_start" in metadata:
            self._save_start_time = now
            self._save_size_mb = metadata["save_start"]
            
        elif "save_done" in metadata:
            if hasattr(self, '_save_start_time'):
                duration = now - self._save_start_time
                if self._current_doc_pages > 0:
                    time_per_page_save = duration / self._current_doc_pages
                    self.measurement_buffers["save_times_per_mb"].append(time_per_page_save) # Renamed conceptually in buffer
                    self.stats["size_done_mb_global"] += self._save_size_mb
                    
                    if self.measurement_buffers["page_times"]:
                        # Look at the last block of pages for this doc
                        pages_to_avg = self.measurement_buffers["page_times"][-self._current_doc_pages:]
                        doc_page_avg = sum(pages_to_avg) / len(pages_to_avg) if pages_to_avg else 2.5
                        
                        # Use a fixed guess for load time per MB if it's the first run
                        load_avg = self.stats.get("avg_time_per_mb_load", 0.1)
                        
                        self.append_history_sample(
                            self._current_doc_pages, 
                            self._save_size_mb, 
                            doc_page_avg, 
                            time_per_page_save,
                            load_avg
                        )
                        # Reset translation counters for next doc
                        self.stats["current_doc_trans_mb"] = 0
                        self.stats["current_doc_trans_time"] = 0
                        self.stats["current_doc_trans_flatten_time"] = 0
                        self.stats["current_doc_trans_api_time"] = 0

        elif "trans_api_start" in metadata:
            self._trans_api_chunk_start = now
            chunk_size_mb = metadata["trans_api_start"] / (1024 * 1024)
            self.stats["current_doc_trans_mb"] = self.stats.get("current_doc_trans_mb", 0) + chunk_size_mb

        elif "trans_api_done" in metadata:
            if hasattr(self, '_trans_api_chunk_start'):
                duration = now - self._trans_api_chunk_start
                self.stats["current_doc_trans_api_time"] = self.stats.get("current_doc_trans_api_time", 0) + duration
                self.stats["current_doc_trans_time"] = self.stats.get("current_doc_trans_time", 0) + duration

        elif "trans_flatten_start" in metadata:
            self._trans_flatten_start = now

        elif "trans_flatten_done" in metadata:
            if hasattr(self, '_trans_flatten_start'):
                duration = now - self._trans_flatten_start
                self.stats["current_doc_trans_flatten_time"] = self.stats.get("current_doc_trans_flatten_time", 0) + duration
                self.stats["current_doc_trans_time"] = self.stats.get("current_doc_trans_time", 0) + duration

        elif "pages" in metadata:
            # Document started
            self._page_start_time = now
            self._current_doc_pages = metadata["pages"]
            # Track load time from document initialization start to first metadata page report
            if hasattr(self, '_doc_load_start_time'):
                load_duration = now - self._doc_load_start_time
                if self._save_size_mb > 0:
                    self.stats["avg_time_per_mb_load"] = (self.stats["avg_time_per_mb_load"] * 0.9) + ((load_duration / self._save_size_mb) * 0.1)

        # Update visual estimation if processing
        if self.is_processing:
            self.update_estimation_ui()

    def update_estimation_ui(self):
        # We can update the UI even before hitting Start if we have global stats
        elapsed = (time.time() - self.start_time_global) if hasattr(self, 'start_time_global') else 0
        
        pages_left = self.stats["total_pages_global"] - self.stats["pages_done_global"]
        mb_left = self.stats["total_size_mb_global"] - self.stats["size_done_mb_global"]
        
        # Safeguard for start
        if pages_left < 0: pages_left = 0
        
        # REGRESSION FORMULA (y = mx + b)
        ping_ratio = self.current_ping / max(1, self.stats.get("last_ping", 50))
        ping_ratio = max(0.5, min(2.0, ping_ratio))
        
        # Use slopes and intercepts from stats
        # m1/b1 for processing, m2/b2 for saving
        # Processed via balanced API/Local weighting
        m1 = self.stats.get("slope_page", 2.5)
        m2 = self.stats.get("slope_save", 0.05)
        b1 = self.stats.get("intercept_page", 0.5)
        b2 = self.stats.get("intercept_save", 0.5)
        
        # Apply balanced ping correction
        balanced_m1 = (m1 * 0.6 * ping_ratio) + (m1 * 0.4)
        
        # Load cost (MB based)
        load_time = mb_left * self.stats.get("avg_time_per_mb_load", 0.1)
        
        # Total Remaining = Remaining Docs * Fixed_Costs + Remaining Pages * Variable_Costs
        files_left = len(self.files_to_process)
        
        # Predicted Translation Cost
        # Since we use Flattened Image (Zoom 2.0), each page is ~1.5MB to 2.5MB
        avg_mb_per_page = 2.0
        projected_trans_mb = pages_left * avg_mb_per_page if self.config.get('translation', {}).get('enabled', False) else 0
        m3 = self.stats.get("slope_trans", 1.5)
        b3 = self.stats.get("intercept_trans", 0.5)
        translation_time = (projected_trans_mb * m3) + (files_left * b3)

        remaining = load_time + (files_left * (b1 + b2)) + (pages_left * (balanced_m1 + m2)) + translation_time
        
        if not self.history_calibrated:
            status_text = "Est. Remaining: Calibrating..."
        else:
            status_text = f"Est. Remaining: {self.format_time(remaining)}"
        
        if self.is_processing and pages_left > 0:
             status_text = f"Elapsed: {self.format_time(elapsed)} | {status_text}"
        
        self.time_var.set(status_text)

    def format_time(self, seconds):
        if seconds < 0: return "Calculating..."
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        return f"{h:02}h {m:02}m {s:02}s"

    def update_file_ui_status(self, filename, success=True, simulated=False):
        """Moves a file from pending to processed listbox"""
        try:
            # Remove from pending
            items = self.list_pending.get(0, tk.END)
            for idx, item in enumerate(items):
                if item == filename:
                    self.list_pending.delete(idx)
                    break
            
            # Add to processed
            status = "Success" if success else "Failed"
            tag = " (Simulated)" if simulated else f" ({status})"
            self.list_processed.insert(tk.END, f"{filename}{tag}")
            self.list_processed.see(tk.END)
            self.root.update()
        except Exception as e:
            print(f"UI Update error: {e}")

    def on_file_selected(self, event):
        w = event.widget
        selection = w.curselection()
        if selection:
            filename = w.get(selection[0])
            # Clean up status tags
            for tag in [" (Completed)", " (Success)", " (Failed)", " (Simulated)"]:
                filename = filename.replace(tag, "")
            
            self.current_selected_file = filename
            self.lbl_kw_target.config(text=f"Keywords for: {filename}", fg="#e65100")
        else:
            self.current_selected_file = None
            self.lbl_kw_target.config(text="Global Redaction Keywords:", fg="#0277bd")
        
        self.render_chips()

    def add_keyword_event(self, event):
        self.add_keyword()
        return "break"

    def add_keyword(self):
        val = self.entry_keyword.get().replace(",", "").strip()
        target = self.current_selected_file
        if val:
            if target not in self.keywords_mapping:
                self.keywords_mapping[target] = []
            if val not in self.keywords_mapping[target]:
                self.keywords_mapping[target].append(val)
                self.render_chips()
        self.entry_keyword.delete(0, tk.END)

    def remove_keyword(self, val):
        target = self.current_selected_file
        # Check current specific target or global
        if target in self.keywords_mapping and val in self.keywords_mapping[target]:
            self.keywords_mapping[target].remove(val)
        elif val in self.keywords_mapping.get(None, []):
             self.keywords_mapping[None].remove(val)
        self.render_chips()

    def render_chips(self):
        for widget in self.chip_container.winfo_children():
            widget.destroy()
            
        row, col = 0, 0
        max_cols = 4
        
        # Show both global AND target-specific chips
        to_render = []
        if None in self.keywords_mapping:
            for kw in self.keywords_mapping[None]:
                to_render.append((kw, True)) # is_global=True
        
        if self.current_selected_file and self.current_selected_file in self.keywords_mapping:
            for kw in self.keywords_mapping[self.current_selected_file]:
                # Don't duplicate if already in global
                if kw not in [x[0] for x in to_render]:
                    to_render.append((kw, False))
        
        for kw, is_global in to_render:
            bg_color = "#e1f5fe" if is_global else "#fff3e0"
            border_color = "#03a9f4" if is_global else "#ff9800"
            
            chip = tk.Frame(self.chip_container, bg=bg_color, padx=5, pady=2, highlightbackground=border_color, highlightthickness=1)
            chip.grid(row=row, column=col, padx=3, pady=3, sticky="w")
            
            label_text = f"{kw} (G)" if is_global else kw
            tk.Label(chip, text=label_text, bg=bg_color, font=("Segoe UI", 9)).pack(side=tk.LEFT)
            
            btn_del = tk.Button(chip, text="×", bg=bg_color, fg="red", bd=0, 
                               command=lambda v=kw: self.remove_keyword(v), font=("Segoe UI", 10, "bold"),
                               activebackground=bg_color, cursor="hand2")
            btn_del.pack(side=tk.LEFT, padx=(5, 0))
            
            col += 1
            if col >= max_cols:
                col = 0
                row += 1

    def select_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.source_folder = folder
            self.lbl_folder.config(text=folder)
            self.load_files()
            self.btn_start.config(state=tk.NORMAL, bg="#90ee90")

    def load_files(self):
        self.files_to_process = []
        self.list_pending.delete(0, tk.END)
        self.list_processed.delete(0, tk.END)
        self.processed_files = [] 
        
        # Reset Stats for new workload analysis
        self.stats["total_pages_global"] = 0
        self.stats["total_size_mb_global"] = 0
        self.stats["pages_done_global"] = 0
        self.stats["size_done_mb_global"] = 0

        try:
            import fitz
            output_folder = os.path.join(self.source_folder, "processed")
            
            # Filter files first
            raw_files = [f for f in os.listdir(self.source_folder) if not f.startswith('.') and f.lower().endswith(('.pdf', '.png', '.jpg', '.jpeg', '.tiff'))]
            
            if not raw_files:
                self.log_message("No supported documents found in selected folder.")
                return

            self.log_message(f"Analyzing {len(raw_files)} documents for workload estimation...")

            def scan_task():
                for f in raw_files:
                    full_path = os.path.join(self.source_folder, f)
                    if os.path.isdir(full_path): continue
                    
                    # Check for already processed files
                    expected_output = os.path.join(output_folder, f"anonymized_{f}")
                    if os.path.exists(expected_output):
                        self.processed_files.append(f)
                        self.list_processed.insert(tk.END, f"{f} (Completed)")
                    else:
                        # Update Weight & Pages only for documents we will actually process
                        size_mb = os.path.getsize(full_path) / (1024 * 1024)
                        self.stats["total_size_mb_global"] += size_mb

                        try:
                            if f.lower().endswith('.pdf'):
                                with fitz.open(full_path) as doc:
                                    self.stats["total_pages_global"] += len(doc)
                            else:
                                self.stats["total_pages_global"] += 1
                        except: self.stats["total_pages_global"] += 1

                        self.files_to_process.append(f)
                        self.list_pending.insert(tk.END, f)

                    # Update UI Estimation incrementally
                    self.update_estimation_ui()
                
                self.log_message(f"Ready! Added {len(self.files_to_process)} files. Total Workload: {self.stats['total_pages_global']} pgs | {round(self.stats['total_size_mb_global'], 1)} MB")

            threading.Thread(target=scan_task, daemon=True).start()
                    
        except Exception as e:
            self.log_message(f"Error listing files: {e}")
            messagebox.showerror("Error", f"Failed to list files: {e}")

    def start_processing_thread(self):
        # Run in thread to not freeze UI during long API calls
        thread = threading.Thread(target=self.start_processing)
        thread.start()

    def start_processing(self):
        if not self.files_to_process:
            messagebox.showinfo("Info", "No files to process.")
            return
        
        self.is_processing = True
        self.should_stop = False
        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL, bg="#ffcdd2")
        
        # Reset Stats for the current run
        self.stats["pages_done_global"] = 0
        self.stats["size_done_mb_global"] = 0
        
        # We already pre-scanned, so we can start immediately
        self.log_message(f"Starting batch: {self.stats['total_pages_global']} pages total.")
        
        # Get Custom Terms from chips
        global_kws = self.keywords_mapping.get(None, [])
        
        files_snapshot = list(self.files_to_process)
        self.start_time_global = time.time()
        
        try:
            # REAL MODE - Direct DLP (Transient)
            self.log_message("Initializing DLP Processor...")
            cloud_config = self.config.get('google_cloud', {})
            processor = ClinicalDocumentProcessor(
                project_id=cloud_config.get('project_id'),
                location=cloud_config.get('location'),
                credentials_file=cloud_config.get('service_account_key_file'),
                log_callback=self.log_message
            )
            
            # Setup output folder
            output_folder = os.path.join(self.source_folder, "processed")
            os.makedirs(output_folder, exist_ok=True)

            total_files = len(files_snapshot)
            success_count = 0
            
            for idx, filename in enumerate(files_snapshot):
                if self.should_stop:
                    self.log_message("Processing halted by user.")
                    break
                    
                self.log_message(f"Processing {idx+1}/{total_files}: {filename}")
                file_path = os.path.join(self.source_folder, filename)
                file_size = os.path.getsize(file_path) / (1024 * 1024)
                output_path = os.path.join(output_folder, f"anonymized_{filename}")
                
                # Mark start of doc for load time tracking
                self._doc_load_start_time = time.time()
                self._save_size_mb = file_size 
                
                success = False
                try:
                    # Merge keywords for this specific file
                    specific_kws = self.keywords_mapping.get(filename, [])
                    merged_terms = list(set(global_kws + specific_kws))
                    
                    # Direct RAM-only processing
                    redacted_bytes = processor.process_document(file_path, custom_terms=merged_terms)
                    
                    if redacted_bytes:
                        with open(output_path, 'wb') as f:
                            f.write(redacted_bytes)
                        
                        # Translation Step (after anonymization and digitalization)
                        trans_config = self.config.get('translation', {})
                        if trans_config.get('enabled', False) and filename.lower().endswith('.pdf'):
                            try:
                                target_lang = trans_config.get('target_language_code', 'en')
                                # translate_document now returns a list of (label, bytes)
                                results = processor.translate_document(redacted_bytes, target_language=target_lang)
                                
                                if len(results) == 1 and results[0][0] == "":
                                    # Case A: Small document (<= 20 pages) - Save normally in /processed
                                    _, trans_bytes = results[0]
                                    trans_output_path = os.path.join(output_folder, f"translated_{target_lang}_{filename}")
                                    with open(trans_output_path, 'wb') as f:
                                        f.write(trans_bytes)
                                    self.log_message(f"Translated copy saved: {os.path.basename(trans_output_path)}")
                                else:
                                    # Case B: Large document (> 20 pages) - Save in a dedicated subfolder
                                    folder_base = os.path.splitext(filename)[0]
                                    subfolder_name = f"{target_lang}_anonymized_{folder_base}"
                                    subfolder_path = os.path.join(output_folder, subfolder_name)
                                    os.makedirs(subfolder_path, exist_ok=True)
                                    
                                    for label, trans_bytes in results:
                                        # Naming convention: 00-20_translated_en_filename.pdf
                                        chunk_filename = f"{label}_translated_{target_lang}_{filename}"
                                        trans_output_path = os.path.join(subfolder_path, chunk_filename)
                                        with open(trans_output_path, 'wb') as f:
                                            f.write(trans_bytes)
                                            
                                    self.log_message(f"Large document split into {len(results)} translated chunks in: {subfolder_name}")
                                    
                            except Exception as te:
                                self.log_message(f"Translation error: {str(te)}")
                        
                        success = True
                        success_count += 1
                    else:
                         self.log_message(f"Completed {filename} but no content returned?")

                except Exception as e:
                    print(f"Error processing {filename}: {e}")
                    self.log_message(f"Failed {filename}: {str(e)[:50]}...")
                
                # Update UI status immediately after each file
                self.update_file_ui_status(filename, success=success)
                self.update_estimation_ui()
                
                # Persist metrics after each document so progress isn't lost on cancel
                self.save_performance_metrics()

            # Final persistence
            self.save_performance_metrics()

            self.log_message(f"Batch Processing Complete! ({success_count} success, {total_files - success_count} failed)")
            self.time_var.set(f"Finished in {self.format_time(time.time() - self.start_time_global)}")

        except Exception as e:
            full_error = str(e)
            print(f"FULL ERROR TRACEBACK: {full_error}") 
            self.log_message(f"Error: {full_error}")
            
        finally:
            self.is_processing = False
            self.should_stop = False
            self.files_to_process = []
            try:
                self.root.after(0, lambda: self.btn_start.config(state=tk.NORMAL))
                self.root.after(0, lambda: self.btn_stop.config(state=tk.DISABLED, bg="#f5f5f5"))
            except:
                pass
            
if __name__ == "__main__":
    root = tk.Tk()
    app = LocalFileProcessorApp(root)
    app.create_widgets()
    root.mainloop()

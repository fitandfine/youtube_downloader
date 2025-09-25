#!/usr/bin/env python3
"""
yt_downloader_gui.py

A user-friendly YouTube downloader GUI using pytube and tkinter.

Features:
 - Enter a YouTube URL
 - Fetch available resolutions (progressive mp4 streams)
 - Choose download folder
 - Start download with live progress bar and status
 - Comprehensive inline comments for learning and modification

Target platform: Linux (Ubuntu).
"""

import threading
import queue
import os
import sys
import traceback

# Import tkinter GUI building blocks.
# Note: DoubleVar, StringVar etc. are in the base tkinter module.
from tkinter import (
    Tk,
    StringVar,
    DoubleVar,
    Button,
    Entry,
    Label,
    filedialog,
    N,
    S,
    E,
    W,
    END,
)
# ttk is the themed widgets submodule
from tkinter import ttk

# pytube for YouTube downloads
from pytube import YouTube  # ensure pytube is installed: pip install pytube

# ---------------------------
# Worker queue for thread-safe UI updates
# ---------------------------
# We'll use a queue to send progress updates from background threads back
# to the main tkinter thread so that UI updates are only performed in the main thread.
ui_queue = queue.Queue()


# ---------------------------
# Progress callback for pytube
# ---------------------------
def on_progress(stream, chunk, bytes_remaining):
    """
    pytube calls this during download. We compute percent and push to UI queue.
    - stream: the Stream object being downloaded
    - chunk: the latest bytes chunk (unused here)
    - bytes_remaining: how many bytes are left to download
    """
    try:
        total_size = stream.filesize  # total size in bytes
        bytes_downloaded = total_size - bytes_remaining
        percent = (bytes_downloaded / total_size) * 100 if total_size else 0.0
        # Put progress update into queue for the main thread
        ui_queue.put(("progress", percent))
    except Exception:
        # If anything goes wrong, send a status update for visibility
        ui_queue.put(("status", "Error computing progress"))


# ---------------------------
# Core download logic (runs in background thread)
# ---------------------------
def download_video(url, resolution, dest_folder):
    """
    Download the YouTube video at `url` with the chosen `resolution` to `dest_folder`.
    Sends status/progress updates into ui_queue.
    """
    try:
        ui_queue.put(("status", "Connecting to YouTube..."))

        # Create YouTube object and register the progress callback
        yt = YouTube(url, on_progress_callback=on_progress)

        ui_queue.put(("status", f"Video found: {yt.title}"))

        # Filter streams to progressive mp4 streams (contain both video+audio)
        streams = yt.streams.filter(progressive=True, file_extension="mp4").order_by("resolution").desc()

        if not streams:
            ui_queue.put(("error", "No progressive mp4 streams available for this video."))
            return

        # If resolution provided, try to match it
        chosen_stream = None
        if resolution:
            for s in streams:
                if s.resolution == resolution:
                    chosen_stream = s
                    break

        # Fallback to best available progressive stream
        if not chosen_stream:
            chosen_stream = streams.first()
            ui_queue.put(("status", f"Selected best available resolution: {chosen_stream.resolution}"))

        # Ensure destination folder exists
        os.makedirs(dest_folder, exist_ok=True)

        ui_queue.put(("status", f"Starting download to: {dest_folder}"))
        # Start the blocking download (safe inside background thread)
        out_file = chosen_stream.download(output_path=dest_folder)

        ui_queue.put(("progress", 100.0))
        ui_queue.put(("status", f"Download complete: {os.path.basename(out_file)}"))
        ui_queue.put(("done", out_file))
    except Exception as e:
        tb = traceback.format_exc()
        ui_queue.put(("error", f"Download error: {str(e)}\n{tb}"))


# ---------------------------
# GUI application class
# ---------------------------
class YTDownloaderApp:
    def __init__(self, root):
        self.root = root
        root.title("YouTube Downloader - pytube + tkinter")
        root.geometry("640x320")
        root.resizable(False, False)

        # --- Tk variables for two-way binding to widgets ---
        self.url_var = StringVar()
        self.resolution_var = StringVar()
        self.folder_var = StringVar()
        self.status_var = StringVar(value="Idle")
        self.progress_var = DoubleVar(value=0.0)
        self.percent_var = StringVar(value="0.0%")

        # This will store available resolution options AFTER fetching
        # IMPORTANT: define this BEFORE building widgets that reference it
        self.available_resolutions = []

        # Build UI layout
        self._build_widgets()

        # Start the periodic UI queue processor (to consume ui_queue)
        self.root.after(100, self.process_ui_queue)

    def _build_widgets(self):
        """Construct all UI widgets and layout using grid geometry."""
        # URL label + entry + fetch button
        Label(self.root, text="YouTube URL:").grid(row=0, column=0, padx=8, pady=(12, 6), sticky=E)
        Entry(self.root, textvariable=self.url_var, width=56).grid(row=0, column=1, columnspan=2, padx=8, pady=(12, 6), sticky=W)
        Button(self.root, text="Fetch Resolutions", command=self.fetch_resolutions).grid(row=0, column=3, padx=8, pady=(12, 6))

        # Resolution selection
        Label(self.root, text="Resolution:").grid(row=1, column=0, padx=8, pady=6, sticky=E)
        self.res_combo = ttk.Combobox(self.root, textvariable=self.resolution_var, values=self.available_resolutions, state="readonly", width=20)
        self.res_combo.grid(row=1, column=1, padx=8, pady=6, sticky=W)
        Label(self.root, text="(progressive mp4 streams only)").grid(row=1, column=2, padx=8, pady=6, sticky=W)

        # Destination folder selection
        Label(self.root, text="Download folder:").grid(row=2, column=0, padx=8, pady=6, sticky=E)
        Entry(self.root, textvariable=self.folder_var, width=44).grid(row=2, column=1, columnspan=2, padx=8, pady=6, sticky=W)
        Button(self.root, text="Choose...", command=self.choose_folder).grid(row=2, column=3, padx=8, pady=6)

        # Progress bar and percent label
        self.progress_bar = ttk.Progressbar(self.root, orient="horizontal", length=480, mode="determinate", variable=self.progress_var, maximum=100)
        self.progress_bar.grid(row=3, column=0, columnspan=4, padx=12, pady=(12, 6))
        self.percent_label = Label(self.root, textvariable=self.percent_var)
        self.percent_label.grid(row=4, column=3, padx=8, pady=(0, 6), sticky=E)

        # Status text label
        Label(self.root, text="Status:").grid(row=4, column=0, padx=8, pady=(0, 6), sticky=E)
        Label(self.root, textvariable=self.status_var, anchor="w", justify="left").grid(row=4, column=1, columnspan=2, padx=8, pady=(0, 6), sticky=W)

        # Download button
        Button(self.root, text="Download", command=self.start_download_thread, width=12).grid(row=5, column=3, padx=8, pady=(6, 12), sticky=E)

        # Configure grid weights for a bit of responsive spacing
        self.root.grid_columnconfigure(1, weight=1)

    # ---------------------------
    # UI actions
    # ---------------------------
    def choose_folder(self):
        """Open a folder chooser and set the folder_var if the user picked one."""
        folder = filedialog.askdirectory()
        if folder:
            self.folder_var.set(folder)

    def fetch_resolutions(self):
        """
        Fetch available progressive mp4 resolutions for the provided URL.
        Runs fetch in a background thread to avoid freezing the GUI.
        """
        url = self.url_var.get().strip()
        if not url:
            self.status_var.set("Please enter a YouTube URL first.")
            return

        # Launch background thread for fetching stream list
        threading.Thread(target=self._fetch_resolutions_background, args=(url,), daemon=True).start()
        self.status_var.set("Fetching available resolutions...")

    def _fetch_resolutions_background(self, url):
        """Background worker: query YouTube for progressive mp4 streams and send results to ui_queue."""
        try:
            yt = YouTube(url)  # no progress callback needed for this query
            streams = yt.streams.filter(progressive=True, file_extension="mp4").order_by("resolution").desc()
            res_list = []
            for s in streams:
                if s.resolution and s.resolution not in res_list:
                    res_list.append(s.resolution)

            if not res_list:
                ui_queue.put(("error", "No progressive mp4 streams found for this video."))
                return

            # Put resolutions and video title into the UI queue for the main thread to consume
            ui_queue.put(("resolutions", res_list, yt.title))
        except Exception as e:
            tb = traceback.format_exc()
            ui_queue.put(("error", f"Error fetching resolutions: {e}\n{tb}"))

    def start_download_thread(self):
        """Validate inputs and start the download in a background thread."""
        url = self.url_var.get().strip()
        resolution = self.resolution_var.get()
        dest_folder = self.folder_var.get().strip() or os.path.expanduser("~")  # default to home

        if not url:
            self.status_var.set("Enter a YouTube URL before downloading.")
            return

        # Ensure destination folder exists or can be created
        if not os.path.isdir(dest_folder):
            try:
                os.makedirs(dest_folder, exist_ok=True)
            except Exception:
                self.status_var.set("Invalid download folder. Choose a valid folder.")
                return

        # Reset progress UI
        self.progress_var.set(0.0)
        self.percent_var.set("0.0%")
        self.status_var.set("Queued for download...")

        # Start download in background
        thread = threading.Thread(target=download_video, args=(url, resolution, dest_folder), daemon=True)
        thread.start()

    # ---------------------------
    # UI queue processor (runs periodically in main thread)
    # ---------------------------
    def process_ui_queue(self):
        """
        Called periodically via tkinter's `after` to process queued UI updates
        from other threads (download + fetch workers).
        """
        try:
            while not ui_queue.empty():
                item = ui_queue.get_nowait()
                if not item:
                    continue
                tag = item[0]

                if tag == "progress":
                    percent = float(item[1])
                    # Update progress bar and percent label
                    self.progress_var.set(percent)
                    self.percent_var.set(f"{percent:.1f}%")
                elif tag == "status":
                    msg = item[1]
                    self.status_var.set(msg)
                elif tag == "resolutions":
                    res_list = item[1]
                    title = item[2] if len(item) > 2 else ""
                    self.available_resolutions = res_list
                    # Safely update combobox values on main thread
                    self.res_combo["values"] = self.available_resolutions
                    if self.available_resolutions:
                        self.res_combo.current(0)  # select first (highest) by default
                        self.resolution_var.set(self.available_resolutions[0])
                    self.status_var.set(f"Found {len(res_list)} resolution(s) for '{title}'")
                elif tag == "error":
                    msg = item[1]
                    # Update status and print detailed error to stderr for debugging
                    self.status_var.set("Error: " + (msg.splitlines()[0] if msg else "Unknown error"))
                    print("ERROR:", msg, file=sys.stderr)
                elif tag == "done":
                    out_path = item[1]
                    self.status_var.set(f"Done: {os.path.basename(out_path)}")
                else:
                    # Unknown message tag: display it as status for visibility
                    self.status_var.set(str(item))
        except Exception as e:
            # Avoid crashing the periodic callback silently
            print("Exception in process_ui_queue:", e, file=sys.stderr)

        # Schedule the next check 100 ms later
        self.root.after(100, self.process_ui_queue)


# ---------------------------
# Main entrypoint
# ---------------------------
def main():
    # Friendly check for pytube (although we've already imported it at top)
    try:
        import pytube  # noqa: F401
    except Exception:
        print("pytube is not installed. Please install with: pip install -r requirements.txt")
        sys.exit(1)

    root = Tk()
    app = YTDownloaderApp(root)

    # Prefill download folder to ~/Downloads if available
    downloads_folder = os.path.join(os.path.expanduser("~"), "Downloads")
    app.folder_var.set(downloads_folder if os.path.isdir(downloads_folder) else os.path.expanduser("~"))

    root.mainloop()


if __name__ == "__main__":
    main()

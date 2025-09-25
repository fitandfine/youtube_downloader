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

Target platform: Linux (Ubuntu). Tested conceptually with pytube and tkinter.
"""

import threading
import queue
import os
import sys
import traceback
from tkinter import (
    Tk, StringVar, Button, Entry, Label, filedialog, ttk, N, S, E, W, END
)
from pytube import YouTube  # make sure pytube is installed

# ---------------------------
# Helper: Worker queue for thread-safe UI updates
# ---------------------------
# We'll use a queue to send progress updates from the download thread
# back to the main tkinter thread so we don't perform UI updates from
# background threads (which can be unsafe).
ui_queue = queue.Queue()


# ---------------------------
# Progress callback for pytube
# ---------------------------
def on_progress(stream, chunk, bytes_remaining):
    """
    pytube calls this during download. We compute percent and push to UI queue.
    stream: the Stream object being downloaded
    chunk: the latest bytes chunk (unused)
    bytes_remaining: how many bytes are left to download
    """
    try:
        total_size = stream.filesize  # total size in bytes
        bytes_downloaded = total_size - bytes_remaining
        percent = (bytes_downloaded / total_size) * 100 if total_size else 0
        # Put progress update into queue
        ui_queue.put(("progress", percent))
    except Exception:
        # If anything goes wrong, send an error message to UI queue
        ui_queue.put(("status", "Error computing progress"))


# ---------------------------
# Core download logic (runs in background thread)
# ---------------------------
def download_video(url, resolution, dest_folder):
    """
    Download the YouTube video at `url` with the chosen `resolution` to `dest_folder`.
    Puts status updates and progress into ui_queue.
    """
    try:
        ui_queue.put(("status", "Connecting to YouTube..."))
        # Create YouTube object and register progress callback
        yt = YouTube(url, on_progress_callback=on_progress)

        ui_queue.put(("status", f"Video found: {yt.title}"))

        # Filter streams to progressive mp4 streams (contain both video+audio)
        streams = yt.streams.filter(progressive=True, file_extension='mp4').order_by('resolution').desc()

        if not streams:
            ui_queue.put(("error", "No progressive mp4 streams available for this video."))
            return

        # If resolution is None or not found, pick highest progressive
        chosen_stream = None
        if resolution:
            # Try to match resolution exactly like '720p' string
            for s in streams:
                if s.resolution == resolution:
                    chosen_stream = s
                    break

        if not chosen_stream:
            # fallback to first (highest) progressive stream
            chosen_stream = streams.first()
            ui_queue.put(("status", f"Selected best available resolution: {chosen_stream.resolution}"))

        # Ensure destination folder exists
        os.makedirs(dest_folder, exist_ok=True)

        ui_queue.put(("status", f"Starting download to: {dest_folder}"))
        # Start the download (this will trigger on_progress repeatedly)
        # The 'download' method is blocking, so it's safe to call in this thread.
        out_file = chosen_stream.download(output_path=dest_folder)

        ui_queue.put(("progress", 100.0))
        ui_queue.put(("status", f"Download complete: {os.path.basename(out_file)}"))
        ui_queue.put(("done", out_file))
    except Exception as e:
        # Pass error message back to main thread; include traceback for debugging
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

        # Tk variables for value binding
        self.url_var = StringVar()
        self.resolution_var = StringVar()
        self.folder_var = StringVar()
        self.status_var = StringVar(value="Idle")
        self.progress_var = ttk.DoubleVar(value=0.0)

        # Build UI layout
        self._build_widgets()

        # This will store available resolution options after fetching
        self.available_resolutions = []

        # Start the periodic UI queue processor
        self.root.after(100, self.process_ui_queue)

    def _build_widgets(self):
        # URL label + entry + fetch button
        Label(self.root, text="YouTube URL:").grid(row=0, column=0, padx=8, pady=(12, 6), sticky=E)
        Entry(self.root, textvariable=self.url_var, width=56).grid(row=0, column=1, columnspan=2, padx=8, pady=(12, 6), sticky=W)
        Button(self.root, text="Fetch Resolutions", command=self.fetch_resolutions).grid(row=0, column=3, padx=8, pady=(12, 6))

        # Resolution selection
        Label(self.root, text="Resolution:").grid(row=1, column=0, padx=8, pady=6, sticky=E)
        self.res_combo = ttk.Combobox(self.root, textvariable=self.resolution_var, values=self.available_resolutions, state="readonly", width=20)
        self.res_combo.grid(row=1, column=1, padx=8, pady=6, sticky=W)
        # Add small helper label
        Label(self.root, text="(progressive mp4 streams only)").grid(row=1, column=2, padx=8, pady=6, sticky=W)

        # Destination folder selection
        Label(self.root, text="Download folder:").grid(row=2, column=0, padx=8, pady=6, sticky=E)
        Entry(self.root, textvariable=self.folder_var, width=44).grid(row=2, column=1, columnspan=2, padx=8, pady=6, sticky=W)
        Button(self.root, text="Choose...", command=self.choose_folder).grid(row=2, column=3, padx=8, pady=6)

        # Progress bar and status
        self.progress_bar = ttk.Progressbar(self.root, orient="horizontal", length=480, mode="determinate", variable=self.progress_var, maximum=100)
        self.progress_bar.grid(row=3, column=0, columnspan=4, padx=12, pady=(12, 6))
        self.percent_label = Label(self.root, textvariable=StringVar(value="0.0%"))
        self.percent_label.grid(row=4, column=3, padx=8, pady=(0, 6), sticky=E)

        # Status text
        Label(self.root, text="Status:").grid(row=4, column=0, padx=8, pady=(0, 6), sticky=E)
        Label(self.root, textvariable=self.status_var, anchor="w", justify="left").grid(row=4, column=1, columnspan=2, padx=8, pady=(0, 6), sticky=W)

        # Download button
        Button(self.root, text="Download", command=self.start_download_thread, width=12).grid(row=5, column=3, padx=8, pady=(6, 12), sticky=E)

        # Configure grid weights for minor neatness
        self.root.grid_columnconfigure(1, weight=1)

    # ---------------------------
    # UI actions
    # ---------------------------
    def choose_folder(self):
        """Open a folder chooser and set the folder_var."""
        folder = filedialog.askdirectory()
        if folder:
            self.folder_var.set(folder)

    def fetch_resolutions(self):
        """
        Fetch available progressive mp4 resolutions for the provided URL.
        This function runs briefly in main thread (network call) so we run it in a thread.
        """
        url = self.url_var.get().strip()
        if not url:
            self.status_var.set("Please enter a YouTube URL first.")
            return

        # Run fetch in background to avoid UI freeze
        threading.Thread(target=self._fetch_resolutions_background, args=(url,), daemon=True).start()
        self.status_var.set("Fetching available resolutions...")

    def _fetch_resolutions_background(self, url):
        """Background thread: query YouTube and populate the resolutions combobox."""
        try:
            yt = YouTube(url)  # no progress callback here
            streams = yt.streams.filter(progressive=True, file_extension='mp4').order_by('resolution').desc()
            res_list = []
            for s in streams:
                # resolution property like '720p'
                if s.resolution and s.resolution not in res_list:
                    res_list.append(s.resolution)

            if not res_list:
                ui_queue.put(("error", "No progressive mp4 streams found for this video."))
                return

            # Send list back to main thread
            ui_queue.put(("resolutions", res_list, yt.title))
        except Exception as e:
            tb = traceback.format_exc()
            ui_queue.put(("error", f"Error fetching resolutions: {e}\n{tb}"))

    def start_download_thread(self):
        """Start the download in a background thread. Validate inputs first."""
        url = self.url_var.get().strip()
        resolution = self.resolution_var.get()
        dest_folder = self.folder_var.get().strip() or os.path.expanduser("~")  # default to home if not provided

        if not url:
            self.status_var.set("Enter a YouTube URL before downloading.")
            return

        # Note: We allow empty resolution (meaning: pick best available progressive)
        if not os.path.isdir(dest_folder):
            # try to create or show error
            try:
                os.makedirs(dest_folder, exist_ok=True)
            except Exception:
                self.status_var.set("Invalid download folder. Choose a valid folder.")
                return

        # Reset progress UI
        self.progress_var.set(0.0)
        self.percent_label.config(text="0.0%")
        self.status_var.set("Queued for download...")

        # Start the download thread
        thread = threading.Thread(target=download_video, args=(url, resolution, dest_folder), daemon=True)
        thread.start()

    # ---------------------------
    # UI queue processor (periodic)
    # ---------------------------
    def process_ui_queue(self):
        """
        Called periodically via tkinter's `after` to process queued UI updates from other threads.
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
                    # Use formatted string with 1 decimal
                    self.percent_label.config(text=f"{percent:.1f}%")
                elif tag == "status":
                    msg = item[1]
                    self.status_var.set(msg)
                elif tag == "resolutions":
                    res_list = item[1]
                    title = item[2] if len(item) > 2 else ""
                    self.available_resolutions = res_list
                    # update combobox safely in main thread
                    self.res_combo['values'] = self.available_resolutions
                    if self.available_resolutions:
                        # select first (highest) by default
                        self.res_combo.current(0)
                        self.resolution_var.set(self.available_resolutions[0])
                    self.status_var.set(f"Found {len(res_list)} resolution(s) for '{title}'")
                elif tag == "error":
                    msg = item[1]
                    # Set status and show brief message — in full app you could show a popup
                    self.status_var.set("Error: " + (msg.splitlines()[0] if msg else "Unknown error"))
                    # Log full error to console for debugging
                    print("ERROR:", msg, file=sys.stderr)
                elif tag == "done":
                    out_path = item[1]
                    self.status_var.set(f"Done: {os.path.basename(out_path)}")
                else:
                    # unknown tag
                    self.status_var.set(str(item))
        except Exception as e:
            # Ensure the UI queue processor doesn't crash silently
            print("Exception in process_ui_queue:", e, file=sys.stderr)

        # Schedule next call
        self.root.after(100, self.process_ui_queue)


# ---------------------------
# Main entrypoint
# ---------------------------
def main():
    # Check that pytube is available; helpful friendly error for users
    try:
        import pytube  # noqa: F401
    except Exception:
        print("pytube is not installed. Please install with: pip install -r requirements.txt")
        sys.exit(1)

    root = Tk()
    app = YTDownloaderApp(root)
    # Optionally prefill folder to Downloads
    downloads_folder = os.path.join(os.path.expanduser("~"), "Downloads")
    app.folder_var.set(downloads_folder if os.path.isdir(downloads_folder) else os.path.expanduser("~"))
    root.mainloop()


if __name__ == "__main__":
    main()

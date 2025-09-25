#!/usr/bin/env python3
"""
app.py

YouTube Downloader (GUI) — single-author, full-featured implementation.

This program:
 - Accepts a YouTube URL
 - Fetches all available video resolutions (adaptive + progressive)
 - Lets the user choose a resolution up to 4K (if available)
 - Downloads the chosen video-only stream and the best audio stream
 - Shows combined progress for the whole operation (video + audio)
 - Uses ffmpeg to merge video + audio into a single MP4 (fast copy where possible)
 - Cleans up temporary files and reports final output path

Notes:
 - This code uses `pytubefix` (a reliable pytube fork). You can switch to the
   official pytube if you prefer (pip install -U git+https://github.com/pytube/pytube).
 - Requires `ffmpeg` installed on the system (apt install ffmpeg on Ubuntu).
 - GUI built with tkinter (install python3-tk on Debian/Ubuntu).
 - Designed to be readable and consistent as if written by one developer over time.
"""

from __future__ import annotations

import os
import sys
import threading
import traceback
import queue
import subprocess
from typing import Optional, Dict

# tkinter standard widgets + ttk for themed widgets
from tkinter import (
    Tk,
    StringVar,
    DoubleVar,
    Button,
    Entry,
    Label,
    filedialog,
    END,
    E,
    W,
)
from tkinter import ttk

# Use pytubefix as our YouTube backend for more resilience to site changes.
# If you prefer the official pytube, import from pytube instead.
from pytubefix import YouTube  # pip install pytubefix

# -----------------------------------------------------------------------------
# Global queue used for safely passing data from worker threads back to the UI
# -----------------------------------------------------------------------------
ui_queue = queue.Queue()

# -----------------------------------------------------------------------------
# Download tracking state
#
# We download two streams (video-only + audio-only). pytube/pytubefix calls our
# on_progress callback with (stream, chunk, bytes_remaining). To compute a
# combined percent, we keep a small tracker keyed by stream.itag:
#
#   DOWNLOAD_TRACKER = {
#       <itag>: {"total": <bytes_total>, "downloaded": <bytes_downloaded>}
#   }
#
# Combined percent = sum(downloaded) / sum(total) * 100
# -----------------------------------------------------------------------------
DOWNLOAD_TRACKER: Dict[int, Dict[str, float]] = {}

# -----------------------------------------------------------------------------
# Helper: sanitize filename to avoid problematic characters
# -----------------------------------------------------------------------------
def sanitize_filename(name: str) -> str:
    # Keep alnum, space, dot, dash, underscore; replace others with underscore
    return "".join(c if (c.isalnum() or c in " ._-") else "_" for c in name).strip()

# -----------------------------------------------------------------------------
# Progress callback used by pytubefix
# -----------------------------------------------------------------------------
def on_progress(stream, chunk, bytes_remaining):
    """
    Called by pytube/pytubefix while a stream is downloading.

    We update DOWNLOAD_TRACKER[itag]['downloaded'] and push an aggregated
    progress update into ui_queue so the GUI can update the progress bar.
    """
    try:
        itag = int(stream.itag)
        total = getattr(stream, "filesize", None) or getattr(stream, "filesize_approx", None) or 0
        # bytes downloaded so far for this stream
        downloaded = (total - bytes_remaining) if total else 0

        # Update tracker (create if necessary)
        if itag not in DOWNLOAD_TRACKER:
            DOWNLOAD_TRACKER[itag] = {"total": float(total), "downloaded": float(downloaded)}
        else:
            DOWNLOAD_TRACKER[itag]["total"] = float(total)
            DOWNLOAD_TRACKER[itag]["downloaded"] = float(downloaded)

        # Compute combined percent across all tracked streams
        sum_total = sum(v["total"] for v in DOWNLOAD_TRACKER.values())
        sum_downloaded = sum(v["downloaded"] for v in DOWNLOAD_TRACKER.values())
        percent = (sum_downloaded / sum_total * 100.0) if sum_total else 0.0

        ui_queue.put(("progress", percent))
    except Exception:
        # Don't crash the downloader because of UI update bugs; report status.
        tb = traceback.format_exc()
        ui_queue.put(("status", f"Progress callback error: {tb.splitlines()[-1]}"))

# -----------------------------------------------------------------------------
# Worker: download chosen streams and merge with ffmpeg
# -----------------------------------------------------------------------------
def download_and_merge(url: str, resolution: Optional[str], dest_folder: str):
    """
    End-to-end worker that:
      1. Creates a YouTube object (with progress callback)
      2. Selects the requested video-only stream and the best audio-only stream
      3. Downloads both streams while reporting combined progress
      4. Uses ffmpeg to merge into final file
      5. Cleans up temp files and notifies UI of completion
    """
    try:
        ui_queue.put(("status", "Connecting to YouTube..."))
        yt = YouTube(url, on_progress_callback=on_progress)
        ui_queue.put(("status", f"Found video: {yt.title}"))

        # Build lists of candidate streams (video-only prioritized)
        # include mp4 container video-only streams (adaptive). We order by resolution desc.
        video_candidates = yt.streams.filter(file_extension="mp4", only_video=True).order_by("resolution").desc()
        if not video_candidates:
            # fallback to progressive mp4 streams (contain audio). This should rarely happen,
            # but we keep fallback logic for robustness.
            video_candidates = yt.streams.filter(progressive=True, file_extension="mp4").order_by("resolution").desc()

        if not video_candidates:
            ui_queue.put(("error", "No downloadable video streams found for this video."))
            return

        # Determine which resolution to pick
        selected_video_stream = None
        if resolution:
            for s in video_candidates:
                if s.resolution == resolution:
                    selected_video_stream = s
                    break

        # If the exact resolution wasn't found, pick the highest available (first in desc list)
        if not selected_video_stream:
            selected_video_stream = video_candidates.first()
            ui_queue.put(("status", f"Selected best available video: {selected_video_stream.resolution}"))

        # Pick the best audio-only stream (highest abr)
        audio_stream = yt.streams.filter(only_audio=True, file_extension="mp4").order_by("abr").desc().first()
        if not audio_stream:
            # Some videos may not have mp4 audio; fallback to any audio stream
            audio_stream = yt.streams.filter(only_audio=True).order_by("abr").desc().first()

        if not audio_stream:
            ui_queue.put(("error", "No downloadable audio stream found for this video."))
            return

        # Prepare destination and filenames
        os.makedirs(dest_folder, exist_ok=True)
        safe_base = sanitize_filename(yt.title)
        video_temp_name = os.path.join(dest_folder, f"{safe_base}.video.temp.mp4")
        audio_temp_name = os.path.join(dest_folder, f"{safe_base}.audio.temp.m4a")
        final_name = os.path.join(dest_folder, f"{safe_base}.mp4")

        # Clear any previous tracker entries
        DOWNLOAD_TRACKER.clear()

        # We will instruct pytube to write to the exact temp filenames. pytube's download()
        # optionally accepts a 'filename' (without path) and output_path; to avoid name
        # collisions we pass full path via output_path and filename.
        ui_queue.put(("status", f"Downloading video track ({selected_video_stream.resolution})..."))
        # Note: pytube's download uses stream.default_filename if no filename given; to force a name
        # that we control we pass filename=... (but pytube will append extension if necessary).
        # Some stream.download implementations append extension automatically; to be safe,
        # use the stream.download(output_path=dest_folder, filename=...) pattern and then rename
        # if needed. We'll use a small helper: download returns the actual path it wrote.
        video_written = selected_video_stream.download(output_path=dest_folder, filename=f"{safe_base}.video.temp")
        # download returns path; ensure it matches expected extension
        if not video_written.endswith(".mp4"):
            # rename to .mp4 for ffmpeg compatibility
            new_video_written = os.path.splitext(video_written)[0] + ".mp4"
            try:
                os.replace(video_written, new_video_written)
                video_written = new_video_written
            except Exception:
                pass

        # Update our temp name to the actual written file
        video_temp_name = video_written

        ui_queue.put(("status", "Downloading audio track (best available)..."))
        audio_written = audio_stream.download(output_path=dest_folder, filename=f"{safe_base}.audio.temp")
        # audio may be .mp4 or .m4a depending on stream; normalize to .m4a for ffmpeg
        if not (audio_written.endswith(".m4a") or audio_written.endswith(".mp4") or audio_written.endswith(".aac")):
            new_audio_written = os.path.splitext(audio_written)[0] + ".m4a"
            try:
                os.replace(audio_written, new_audio_written)
                audio_written = new_audio_written
            except Exception:
                pass

        audio_temp_name = audio_written

        # At this point the on_progress callbacks would have been firing and updating the UI.
        # Now perform the merge with ffmpeg. We use stream copy for video and encode audio to aac
        # if necessary. Use -y to overwrite any existing file with same name.
        ui_queue.put(("status", "Merging video & audio with ffmpeg..."))

        # Build ffmpeg command:
        # - try to copy the video stream (-c:v copy) and set audio to aac (-c:a aac)
        # - ensure fast operations by not re-encoding video
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",  # show only errors — we capture return code
            "-i",
            video_temp_name,
            "-i",
            audio_temp_name,
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-strict",
            "-2",
            final_name,
        ]

        # Run ffmpeg and check for issues
        proc = subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode != 0:
            # Merge failed — inform user and keep temp files for debugging
            ui_queue.put(("error", f"ffmpeg merge failed: {proc.stderr.decode('utf-8', errors='replace')[:300]}"))
            return

        # If merge succeeded, remove temp files
        try:
            if os.path.exists(video_temp_name):
                os.remove(video_temp_name)
            if os.path.exists(audio_temp_name):
                os.remove(audio_temp_name)
        except Exception:
            # Non-fatal — but inform in logs
            ui_queue.put(("status", "Merged but failed to remove temp files."))

        ui_queue.put(("progress", 100.0))
        ui_queue.put(("status", f"Download complete: {os.path.basename(final_name)}"))
        ui_queue.put(("done", final_name))

    except Exception as e:
        tb = traceback.format_exc()
        ui_queue.put(("error", f"Download error: {str(e)}\n{tb}"))

# -----------------------------------------------------------------------------
# GUI application class (single-author style, consistent comments)
# -----------------------------------------------------------------------------
class YTDownloaderApp:
    """
    The main GUI class encapsulates UI construction, event handlers, and the
    periodic UI queue processing loop.
    """

    def __init__(self, root: Tk):
        self.root = root
        root.title("YouTube Downloader — Full-Featured (video+audio, ffmpeg)")
        root.geometry("720x360")
        root.resizable(False, False)

        # Bindable variables used by widgets
        self.url_var = StringVar()
        self.resolution_var = StringVar()
        self.folder_var = StringVar()
        self.status_var = StringVar(value="Idle")
        self.progress_var = DoubleVar(value=0.0)
        self.percent_var = StringVar(value="0.0%")

        # Holds the resolutions available after a fetch; defined before widgets so the combobox can reference it
        self.available_resolutions = []

        # Build the widgets
        self._build_widgets()

        # Start timer that consumes ui_queue (updates UI from worker threads)
        self.root.after(100, self._process_ui_queue)

    def _build_widgets(self):
        """
        Create labels, entries, comboboxes, progressbar and buttons.
        Layout uses grid with small padding to look tidy.
        """
        # URL input row
        Label(self.root, text="YouTube URL:").grid(row=0, column=0, padx=10, pady=(12, 6), sticky=E)
        Entry(self.root, textvariable=self.url_var, width=68).grid(row=0, column=1, columnspan=2, padx=6, pady=(12, 6), sticky=W)
        Button(self.root, text="Fetch Resolutions", command=self.fetch_resolutions).grid(row=0, column=3, padx=10, pady=(12, 6))

        # Resolution selection row
        Label(self.root, text="Resolution:").grid(row=1, column=0, padx=10, pady=6, sticky=E)
        self.res_combo = ttk.Combobox(self.root, textvariable=self.resolution_var, values=self.available_resolutions, state="readonly", width=20)
        self.res_combo.grid(row=1, column=1, padx=6, pady=6, sticky=W)
        Label(self.root, text="(adaptive video-only streams shown; ffmpeg merges audio)").grid(row=1, column=2, columnspan=2, padx=6, pady=6, sticky=W)

        # Destination folder row
        Label(self.root, text="Download folder:").grid(row=2, column=0, padx=10, pady=6, sticky=E)
        Entry(self.root, textvariable=self.folder_var, width=52).grid(row=2, column=1, columnspan=2, padx=6, pady=6, sticky=W)
        Button(self.root, text="Choose...", command=self.choose_folder).grid(row=2, column=3, padx=10, pady=6)

        # Progress bar
        self.progress_bar = ttk.Progressbar(self.root, orient="horizontal", length=620, mode="determinate", variable=self.progress_var, maximum=100)
        self.progress_bar.grid(row=3, column=0, columnspan=4, padx=12, pady=(12, 6))
        Label(self.root, textvariable=self.percent_var).grid(row=4, column=3, padx=10, pady=(0, 6), sticky=E)

        # Status line
        Label(self.root, text="Status:").grid(row=4, column=0, padx=10, pady=(0, 6), sticky=E)
        Label(self.root, textvariable=self.status_var, anchor="w", justify="left", width=60).grid(row=4, column=1, columnspan=2, padx=6, pady=(0, 6), sticky=W)

        # Download button
        Button(self.root, text="Download & Merge", command=self.start_download, width=18).grid(row=5, column=3, padx=12, pady=(6, 12), sticky=E)

        # set sensible default downloads folder
        downloads_folder = os.path.join(os.path.expanduser("~"), "Downloads")
        self.folder_var.set(downloads_folder if os.path.isdir(downloads_folder) else os.path.expanduser("~"))

    # ---------------------------
    # UI action helpers
    # ---------------------------
    def choose_folder(self):
        """Open the directory chooser and set the folder_var if user selects one."""
        folder = filedialog.askdirectory()
        if folder:
            self.folder_var.set(folder)

    def fetch_resolutions(self):
        """
        Trigger a background worker that fetches available video resolutions
        (adaptive video-only streams are prioritized).
        """
        url = self.url_var.get().strip()
        if not url:
            self.status_var.set("Please paste a YouTube URL first.")
            return

        # Run the fetch in a background thread to avoid blocking the UI
        threading.Thread(target=self._fetch_resolutions_worker, args=(url,), daemon=True).start()
        self.status_var.set("Fetching available resolutions...")

    def _fetch_resolutions_worker(self, url: str):
        """
        Background worker that queries YouTube for streams and sends a
        'resolutions' message into ui_queue for the main thread to consume.
        """
        try:
            yt = YouTube(url)
            # prefer adaptive video-only mp4 streams, order highest resolution first
            video_streams = yt.streams.filter(file_extension="mp4", only_video=True).order_by("resolution").desc()

            # Fallback to progressive mp4 streams if no adaptive found
            if not video_streams:
                video_streams = yt.streams.filter(progressive=True, file_extension="mp4").order_by("resolution").desc()

            res_list = []
            for s in video_streams:
                # s.resolution is like '1080p' or None (skip None)
                if s.resolution and s.resolution not in res_list:
                    res_list.append(s.resolution)

            if not res_list:
                ui_queue.put(("error", "No video streams (resolutions) found for this video."))
                return

            ui_queue.put(("resolutions", res_list, yt.title))
        except Exception as e:
            tb = traceback.format_exc()
            ui_queue.put(("error", f"Failed to fetch resolutions: {e}\n{tb}"))

    def start_download(self):
        """
        Validate inputs and start the download+merge operation in a background thread.
        This keeps the GUI responsive while the heavy lifting is done off the main thread.
        """
        url = self.url_var.get().strip()
        resolution = self.resolution_var.get()
        dest = self.folder_var.get().strip() or os.path.expanduser("~")

        if not url:
            self.status_var.set("Enter a YouTube URL before trying to download.")
            return

        if not os.path.isdir(dest):
            try:
                os.makedirs(dest, exist_ok=True)
            except Exception:
                self.status_var.set("Invalid download folder — choose a valid directory.")
                return

        # Reset tracker & UI progress
        DOWNLOAD_TRACKER.clear()
        self.progress_var.set(0.0)
        self.percent_var.set("0.0%")
        self.status_var.set("Queued for download...")

        # Start the background worker
        threading.Thread(target=download_and_merge, args=(url, resolution, dest), daemon=True).start()

    # ---------------------------
    # UI queue consumer (runs on main thread)
    # ---------------------------
    def _process_ui_queue(self):
        """
        Periodically run on the tkinter mainloop to process messages from ui_queue.
        Messages:
          - ("progress", percent_float)
          - ("status", string)
          - ("resolutions", [list_of_res], video_title)
          - ("error", string)
          - ("done", final_path)
        """
        try:
            while not ui_queue.empty():
                msg = ui_queue.get_nowait()
                if not msg:
                    continue
                tag = msg[0]

                if tag == "progress":
                    percent = float(msg[1])
                    self.progress_var.set(percent)
                    self.percent_var.set(f"{percent:.1f}%")
                elif tag == "status":
                    self.status_var.set(str(msg[1]))
                elif tag == "resolutions":
                    res_list, title = msg[1], msg[2] if len(msg) > 2 else ""
                    self.available_resolutions = res_list
                    self.res_combo["values"] = self.available_resolutions
                    if self.available_resolutions:
                        self.res_combo.current(0)
                        self.resolution_var.set(self.available_resolutions[0])
                    self.status_var.set(f"Found {len(res_list)} resolution(s) for '{title}'")
                elif tag == "error":
                    # show short error in status (longer detail is logged to stderr)
                    err = str(msg[1])
                    short = err.splitlines()[0] if err else "Unknown error"
                    self.status_var.set("Error: " + short)
                    print("ERROR:", err, file=sys.stderr)
                elif tag == "done":
                    final = msg[1]
                    self.status_var.set(f"Done: {final}")
                    # set progress to full if not already
                    self.progress_var.set(100.0)
                    self.percent_var.set("100.0%")
                else:
                    # Unknown message -> display for debugging
                    self.status_var.set(str(msg))
        except Exception as e:
            # Avoid the callback dying silently; log to stderr
            print("Exception in UI queue processing:", e, file=sys.stderr)

        # Re-schedule this method to run again after 100 ms
        self.root.after(100, self._process_ui_queue)

# -----------------------------------------------------------------------------
# Entrypoint
# -----------------------------------------------------------------------------
def main():
    # Quick sanity check for ffmpeg availability (we rely on it for merging)
    try:
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        print("Warning: ffmpeg not found or not runnable. Please install ffmpeg for merging.", file=sys.stderr)

    root = Tk()
    app = YTDownloaderApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()

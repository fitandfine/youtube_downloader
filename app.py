#!/usr/bin/env python3
"""
app.py

YouTube Downloader (Full-Featured GUI, Single Author)

Features:
 - Download video (adaptive + progressive) up to 4K
 - Download audio-only in different formats (mp3, m4a, etc.)
 - Download video in different formats (mp4, webm)
 - Uses ffmpeg to merge video + audio automatically
 - Progress bar shows combined progress
 - Alerts when download is finished with option to open folder
 - Fully responsive Tkinter GUI
 - Clear single-author comments for maintainability
"""

import os
import sys
import queue
import threading
import subprocess
import traceback
from tkinter import Tk, StringVar, DoubleVar, Label, Entry, Button, filedialog, END, E, W, messagebox
from tkinter import ttk
from pytubefix import YouTube  # pip install pytubefix

# UI queue for thread-safe GUI updates
ui_queue = queue.Queue()

# Tracker for multiple streams to calculate combined progress
DOWNLOAD_TRACKER = {}

# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------
def sanitize_filename(name: str) -> str:
    """Remove unsafe characters from filenames."""
    return "".join(c if c.isalnum() or c in " ._-()" else "_" for c in name).strip()

def on_progress(stream, chunk, bytes_remaining):
    """Combined progress callback for video+audio streams."""
    try:
        itag = int(stream.itag)
        total = getattr(stream, "filesize", 0) or getattr(stream, "filesize_approx", 0)
        downloaded = total - bytes_remaining
        if itag not in DOWNLOAD_TRACKER:
            DOWNLOAD_TRACKER[itag] = {"total": float(total), "downloaded": float(downloaded)}
        else:
            DOWNLOAD_TRACKER[itag]["total"] = float(total)
            DOWNLOAD_TRACKER[itag]["downloaded"] = float(downloaded)

        total_bytes = sum(v["total"] for v in DOWNLOAD_TRACKER.values())
        downloaded_bytes = sum(v["downloaded"] for v in DOWNLOAD_TRACKER.values())
        percent = (downloaded_bytes / total_bytes * 100.0) if total_bytes else 0.0
        ui_queue.put(("progress", percent))
    except Exception:
        ui_queue.put(("status", "Progress callback error"))

# -----------------------------------------------------------------------------
# Download Worker
# -----------------------------------------------------------------------------
def download_worker(url: str, resolution: str, video_format: str, audio_format: str, folder: str, download_type: str):
    """
    Main worker function:
    - download_type: "video", "audio", or "both"
    - resolution, formats selected by user
    """
    try:
        yt = YouTube(url, on_progress_callback=on_progress)
        ui_queue.put(("status", f"Video: {yt.title}"))

        safe_title = sanitize_filename(yt.title)
        os.makedirs(folder, exist_ok=True)

        # -------------------
        # Download audio-only
        # -------------------
        audio_file = None
        if download_type in ("audio", "both"):
            audio_stream = yt.streams.filter(only_audio=True).order_by("abr").desc().first()
            if audio_stream:
                # Download original audio first (likely webm/opus)
                temp_audio = os.path.join(folder, f"{safe_title}.temp.{audio_stream.subtype}")
                DOWNLOAD_TRACKER.clear()
                audio_stream.download(output_path=folder, filename=os.path.basename(temp_audio))

                # Convert to requested format using ffmpeg
                audio_file = os.path.join(folder, f"{safe_title}.{audio_format}")
                ffmpeg_cmd = [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-i", temp_audio,
                    audio_file
                ]
                proc = subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                if proc.returncode != 0:
                    ui_queue.put(("error", f"ffmpeg conversion failed for audio."))
                    audio_file = temp_audio  # fallback
                else:
                    os.remove(temp_audio)  # remove temp file
                ui_queue.put(("status", f"Audio ready: {audio_file}"))
        # -------------------
        # Download video-only
        # -------------------
        video_file = None
        if download_type in ("video", "both"):
            if download_type == "video":
                # get highest resolution with selected format
                video_streams = yt.streams.filter(file_extension=video_format, progressive=True)
            else:
                # for "both", get adaptive video-only
                video_streams = yt.streams.filter(file_extension=video_format, only_video=True)

            # pick requested resolution or highest
            selected_video = None
            for v in video_streams.order_by("resolution").desc():
                if resolution and v.resolution == resolution:
                    selected_video = v
                    break
            if not selected_video:
                selected_video = video_streams.order_by("resolution").desc().first()

            if selected_video:
                video_file = os.path.join(folder, f"{safe_title}.video.{video_format}")
                DOWNLOAD_TRACKER.clear()
                selected_video.download(output_path=folder, filename=os.path.basename(video_file))
                ui_queue.put(("status", f"Video downloaded: {video_file}"))

        # -------------------
        # Merge if needed
        # -------------------
        final_file = None
        if download_type == "both" and video_file and audio_file:
            final_file = os.path.join(folder, f"{safe_title}.mp4")
            ui_queue.put(("status", "Merging video + audio with ffmpeg..."))
            ffmpeg_cmd = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", video_file,
                "-i", audio_file,
                "-c:v", "copy", "-c:a", "aac", "-strict", "-2",
                final_file
            ]
            proc = subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if proc.returncode != 0:
                ui_queue.put(("error", "ffmpeg merge failed"))
            else:
                ui_queue.put(("status", f"Merged file ready: {final_file}"))
                # cleanup
                os.remove(video_file)
                os.remove(audio_file)

        # Final notification
        output_file = final_file or video_file or audio_file
        if output_file:
            ui_queue.put(("done", output_file))

    except Exception as e:
        tb = traceback.format_exc()
        ui_queue.put(("error", f"Download error: {e}\n{tb}"))

# -----------------------------------------------------------------------------
# GUI Class
# -----------------------------------------------------------------------------
class YTDownloaderApp:
    def __init__(self, root):
        self.root = root
        root.title("YouTube Downloader Full-Featured")
        root.geometry("950x400")
        root.resizable(True, True)

        # Tk variables
        self.url_var = StringVar()
        self.res_var = StringVar()
        self.video_format_var = StringVar(value="mp4")
        self.audio_format_var = StringVar(value="m4a")
        self.download_type_var = StringVar(value="both")
        self.folder_var = StringVar()
        self.status_var = StringVar(value="Idle")
        self.progress_var = DoubleVar(value=0.0)
        self.percent_var = StringVar(value="0.0%")
        self.available_resolutions = []

        self._build_widgets()
        self.root.after(100, self._process_ui_queue)

    def _build_widgets(self):
        # URL
        Label(self.root, text="YouTube URL:").grid(row=0, column=0, padx=10, pady=6, sticky=E)
        Entry(self.root, textvariable=self.url_var, width=70).grid(row=0, column=1, columnspan=3, sticky=W)
        Button(self.root, text="Fetch Resolutions", command=self.fetch_resolutions).grid(row=0, column=4, padx=10)

        # Resolution
        Label(self.root, text="Resolution:").grid(row=1, column=0, sticky=E, padx=10)
        self.res_combo = ttk.Combobox(self.root, textvariable=self.res_var, values=self.available_resolutions, state="readonly", width=18)
        self.res_combo.grid(row=1, column=1, sticky=W)
        Label(self.root, text="Video format:").grid(row=1, column=2, sticky=E)
        ttk.Combobox(self.root, textvariable=self.video_format_var, values=["mp4","webm"], state="readonly", width=10).grid(row=1, column=3, sticky=W)
        Label(self.root, text="Audio format:").grid(row=1, column=4, sticky=E)
        ttk.Combobox(self.root, textvariable=self.audio_format_var, values=["m4a","mp3","webm"], state="readonly", width=10).grid(row=1, column=5, sticky=W)

        # Download type
        Label(self.root, text="Download type:").grid(row=2, column=0, sticky=E, padx=10)
        ttk.Combobox(self.root, textvariable=self.download_type_var, values=["video","audio","both"], state="readonly", width=12).grid(row=2, column=1, sticky=W)

        # Folder
        Label(self.root, text="Save folder:").grid(row=2, column=2, sticky=E)
        Entry(self.root, textvariable=self.folder_var, width=50).grid(row=2, column=3, columnspan=2, sticky=W)
        Button(self.root, text="Choose...", command=self.choose_folder).grid(row=2, column=5)

        # Progress bar
        self.progress_bar = ttk.Progressbar(self.root, orient="horizontal", length=700, mode="determinate", variable=self.progress_var, maximum=100)
        self.progress_bar.grid(row=3, column=0, columnspan=6, padx=10, pady=12)
        Label(self.root, textvariable=self.percent_var).grid(row=4, column=5, sticky=E, padx=10)

        # Status
        Label(self.root, text="Status:").grid(row=4, column=0, sticky=E, padx=10)
        Label(self.root, textvariable=self.status_var, width=60, anchor="w").grid(row=4, column=1, columnspan=4, sticky=W)

        # Download button
        Button(self.root, text="Start Download", command=self.start_download, width=18).grid(row=5, column=5, sticky=E, padx=10, pady=12)

        # Default folder
        downloads = os.path.join(os.path.expanduser("~"), "Downloads")
        self.folder_var.set(downloads if os.path.isdir(downloads) else os.path.expanduser("~"))

    # ---------------------------
    # UI Actions
    # ---------------------------
    def choose_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.folder_var.set(folder)

    def fetch_resolutions(self):
        url = self.url_var.get().strip()
        if not url:
            self.status_var.set("Enter a YouTube URL first.")
            return
        threading.Thread(target=self._fetch_worker, args=(url,), daemon=True).start()
        self.status_var.set("Fetching resolutions...")

    def _fetch_worker(self, url):
        try:
            yt = YouTube(url)
            streams = yt.streams.filter(file_extension=self.video_format_var.get(), only_video=True).order_by("resolution").desc()
            if not streams:
                streams = yt.streams.filter(progressive=True, file_extension=self.video_format_var.get()).order_by("resolution").desc()
            res_list = [s.resolution for s in streams if s.resolution]
            if not res_list:
                ui_queue.put(("error", "No resolutions found"))
                return
            ui_queue.put(("resolutions", res_list, yt.title))
        except Exception as e:
            tb = traceback.format_exc()
            ui_queue.put(("error", f"Failed to fetch: {e}\n{tb}"))

    def start_download(self):
        url = self.url_var.get().strip()
        res = self.res_var.get()
        v_fmt = self.video_format_var.get()
        a_fmt = self.audio_format_var.get()
        dtype = self.download_type_var.get()
        folder = self.folder_var.get().strip() or os.path.expanduser("~")
        if not url:
            self.status_var.set("Enter a YouTube URL first.")
            return
        threading.Thread(target=download_worker, args=(url,res,v_fmt,a_fmt,folder,dtype), daemon=True).start()
        self.progress_var.set(0.0)
        self.percent_var.set("0.0%")
        self.status_var.set("Download started...")

    # ---------------------------
    # UI Queue Processor
    # ---------------------------
    def _process_ui_queue(self):
        try:
            while not ui_queue.empty():
                msg = ui_queue.get_nowait()
                if not msg: continue
                tag = msg[0]
                if tag == "progress":
                    p = float(msg[1])
                    self.progress_var.set(p)
                    self.percent_var.set(f"{p:.1f}%")
                elif tag == "status":
                    self.status_var.set(msg[1])
                elif tag == "resolutions":
                    res_list, title = msg[1], msg[2]
                    self.available_resolutions = res_list
                    self.res_combo["values"] = self.available_resolutions
                    if res_list:
                        self.res_combo.current(0)
                        self.res_var.set(res_list[0])
                    self.status_var.set(f"Found {len(res_list)} resolutions for '{title}'")
                elif tag == "error":
                    self.status_var.set("Error: " + str(msg[1]).splitlines()[0])
                    print("ERROR:", msg[1], file=sys.stderr)
                elif tag == "done":
                    file_path = msg[1]
                    self.status_var.set(f"Done: {file_path}")
                    self.progress_var.set(100)
                    self.percent_var.set("100%")
                    # Show alert with option to open folder
                    if messagebox.askyesno("Download Complete", f"File saved:\n{file_path}\n\nOpen containing folder?"):
                        folder = os.path.dirname(file_path)
                        if sys.platform == "win32":
                            os.startfile(folder)
                        elif sys.platform == "darwin":
                            subprocess.Popen(["open", folder])
                        else:
                            subprocess.Popen(["xdg-open", folder])
        except Exception as e:
            print("UI queue error:", e, file=sys.stderr)
        finally:
            self.root.after(100, self._process_ui_queue)

# -----------------------------------------------------------------------------
# Entry Point
# -----------------------------------------------------------------------------
def main():
    # Quick ffmpeg check
    try:
        subprocess.run(["ffmpeg","-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        print("Warning: ffmpeg not found. Install ffmpeg for merging.", file=sys.stderr)

    root = Tk()
    app = YTDownloaderApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()

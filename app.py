#!/usr/bin/env python3
"""
app.py
YouTube Downloader with Separate Progress Bars (Audio / Video / Processing)

Features:
 - Download audio-only, video-only, or both (merged with FFmpeg)
 - Supports progressive + adaptive streams up to 4K
 - Separate progress bars for:
     * Audio download
     * Video download
     * Processing (merging / converting)
 - User-friendly Tkinter GUI
 - Clear single-author comments for Git history consistency
"""

import os
import sys
import queue
import threading
import subprocess
import traceback
from tkinter import Tk, StringVar, DoubleVar, Label, Entry, Button, filedialog, E, W, messagebox
from tkinter import ttk
from pytubefix import YouTube  # pytubefix handles current YouTube API changes

# -----------------------------------------------------------------------------
# Globals
# -----------------------------------------------------------------------------
ui_queue = queue.Queue()  # thread-safe queue for updating UI from worker threads
DOWNLOAD_TRACKER = {}     # keeps track of download progress per stream


# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------
def sanitize_filename(name: str) -> str:
    """Remove illegal characters for file names."""
    return "".join(c if c.isalnum() or c in " ._-()" else "_" for c in name).strip()


def on_progress(stream, chunk, bytes_remaining):
    """
    Progress callback from pytubefix.
    Updates percentage per stream (audio/video).
    """
    try:
        itag = int(stream.itag)
        total = getattr(stream, "filesize", 0) or getattr(stream, "filesize_approx", 0)
        downloaded = total - bytes_remaining
        DOWNLOAD_TRACKER[itag] = {"total": float(total), "downloaded": float(downloaded)}

        # Push stream-specific progress
        percent = (downloaded / total * 100.0) if total else 0.0
        ui_queue.put(("stream_progress", stream.type, percent))
    except Exception:
        ui_queue.put(("status", "Progress callback error"))


# -----------------------------------------------------------------------------
# Download Worker (runs in background thread)
# -----------------------------------------------------------------------------
def download_worker(url, resolution, video_format, audio_format, folder, download_type):
    try:
        yt = YouTube(url, on_progress_callback=on_progress)
        ui_queue.put(("status", f"Video: {yt.title}"))
        safe_title = sanitize_filename(yt.title)
        os.makedirs(folder, exist_ok=True)

        audio_file, video_file = None, None

        # ---------------------------------------------------------------------
        # AUDIO DOWNLOAD
        # ---------------------------------------------------------------------
        if download_type in ("audio", "both"):
            audio_stream = yt.streams.filter(only_audio=True).order_by("abr").desc().first()
            if audio_stream:
                temp_audio = os.path.join(folder, f"{safe_title}.temp.{audio_stream.subtype}")
                DOWNLOAD_TRACKER.clear()
                ui_queue.put(("status", "Downloading audio..."))
                audio_stream.download(output_path=folder, filename=os.path.basename(temp_audio))

                # Convert to requested audio format with ffmpeg
                audio_file = os.path.join(folder, f"{safe_title}.{audio_format}")
                ui_queue.put(("processing_start", "audio"))
                ffmpeg_cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", temp_audio, audio_file]
                proc = subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                ui_queue.put(("processing_end", "audio"))
                if proc.returncode != 0:
                    ui_queue.put(("error", f"ffmpeg conversion failed for audio"))
                    audio_file = temp_audio
                else:
                    os.remove(temp_audio)
                ui_queue.put(("status", f"Audio ready: {audio_file}"))

        # ---------------------------------------------------------------------
        # VIDEO DOWNLOAD
        # ---------------------------------------------------------------------
        if download_type in ("video", "both"):
            if download_type == "video":
                video_streams = yt.streams.filter(progressive=True, file_extension=video_format).order_by("resolution").desc()
            else:
                video_streams = yt.streams.filter(only_video=True, file_extension=video_format).order_by("resolution").desc()

            # Select requested resolution or highest available
            selected_video = None
            if resolution:
                for v in video_streams:
                    if v.resolution == resolution:
                        selected_video = v
                        break
            if not selected_video and video_streams:
                selected_video = video_streams.first()

            if selected_video:
                video_file = os.path.join(folder, f"{safe_title}.video.{video_format}")
                DOWNLOAD_TRACKER.clear()
                ui_queue.put(("status", f"Downloading video ({selected_video.resolution})..."))
                selected_video.download(output_path=folder, filename=os.path.basename(video_file))
                ui_queue.put(("status", f"Video downloaded: {video_file}"))

        # ---------------------------------------------------------------------
        # MERGING (if both audio + video selected)
        # ---------------------------------------------------------------------
        final_file = None
        if download_type == "both" and video_file and audio_file:
            final_file = os.path.join(folder, f"{safe_title}.mp4")
            ui_queue.put(("processing_start", "merge"))
            ui_queue.put(("status", "Merging video + audio with ffmpeg..."))
            ffmpeg_cmd = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", video_file,
                "-i", audio_file,
                "-c:v", "copy", "-c:a", "aac", "-strict", "-2",
                final_file
            ]
            proc = subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            ui_queue.put(("processing_end", "merge"))
            if proc.returncode != 0:
                ui_queue.put(("error", "ffmpeg merge failed"))
            else:
                ui_queue.put(("status", f"Merged file ready: {final_file}"))
                os.remove(video_file)
                os.remove(audio_file)

        # ---------------------------------------------------------------------
        # FINAL FILE
        # ---------------------------------------------------------------------
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
        root.title("YouTube Downloader - Separate Progress Bars")
        root.geometry("950x450")
        root.resizable(True, True)

        # Tkinter Variables
        self.url_var = StringVar()
        self.res_var = StringVar()
        self.video_format_var = StringVar(value="mp4")
        self.audio_format_var = StringVar(value="m4a")
        self.download_type_var = StringVar(value="both")
        self.folder_var = StringVar()
        self.status_var = StringVar(value="Idle")
        self.available_resolutions = []

        # Progress bar variables
        self.audio_progress = DoubleVar(value=0.0)
        self.video_progress = DoubleVar(value=0.0)
        self.process_progress = DoubleVar(value=0.0)

        self._build_widgets()
        self.root.after(100, self._process_ui_queue)

    # -------------------------------------------------------------------------
    # BUILD WIDGETS
    # -------------------------------------------------------------------------
    def _build_widgets(self):
        Label(self.root, text="YouTube URL:").grid(row=0, column=0, padx=10, pady=6, sticky=E)
        Entry(self.root, textvariable=self.url_var, width=70).grid(row=0, column=1, columnspan=3, sticky=W)
        Button(self.root, text="Fetch Resolutions", command=self.fetch_resolutions).grid(row=0, column=4, padx=10)

        Label(self.root, text="Resolution:").grid(row=1, column=0, sticky=E, padx=10)
        self.res_combo = ttk.Combobox(self.root, textvariable=self.res_var, values=self.available_resolutions, state="readonly", width=18)
        self.res_combo.grid(row=1, column=1, sticky=W)

        Label(self.root, text="Video format:").grid(row=1, column=2, sticky=E)
        ttk.Combobox(self.root, textvariable=self.video_format_var, values=["mp4","webm"], state="readonly", width=10).grid(row=1, column=3, sticky=W)

        Label(self.root, text="Audio format:").grid(row=1, column=4, sticky=E)
        ttk.Combobox(self.root, textvariable=self.audio_format_var, values=["m4a","mp3","webm"], state="readonly", width=10).grid(row=1, column=5, sticky=W)

        Label(self.root, text="Download type:").grid(row=2, column=0, sticky=E, padx=10)
        ttk.Combobox(self.root, textvariable=self.download_type_var, values=["video","audio","both"], state="readonly", width=12).grid(row=2, column=1, sticky=W)

        Label(self.root, text="Save folder:").grid(row=2, column=2, sticky=E)
        Entry(self.root, textvariable=self.folder_var, width=50).grid(row=2, column=3, columnspan=2, sticky=W)
        Button(self.root, text="Choose...", command=self.choose_folder).grid(row=2, column=5)

        # AUDIO progress bar
        self.audio_label = Label(self.root, text="Audio Progress:")
        self.audio_bar = ttk.Progressbar(self.root, orient="horizontal", length=700, mode="determinate", variable=self.audio_progress, maximum=100)

        # VIDEO progress bar
        self.video_label = Label(self.root, text="Video Progress:")
        self.video_bar = ttk.Progressbar(self.root, orient="horizontal", length=700, mode="determinate", variable=self.video_progress, maximum=100)

        # PROCESSING progress bar
        self.proc_label = Label(self.root, text="Processing:")
        self.proc_bar = ttk.Progressbar(self.root, orient="horizontal", length=700, mode="indeterminate", variable=self.process_progress, maximum=100)

        # Place bars dynamically later
        self.audio_label.grid(row=3, column=0, padx=10, sticky=E)
        self.audio_bar.grid(row=3, column=1, columnspan=5, pady=4, sticky=W)

        self.video_label.grid(row=4, column=0, padx=10, sticky=E)
        self.video_bar.grid(row=4, column=1, columnspan=5, pady=4, sticky=W)

        self.proc_label.grid(row=5, column=0, padx=10, sticky=E)
        self.proc_bar.grid(row=5, column=1, columnspan=5, pady=4, sticky=W)

        Label(self.root, text="Status:").grid(row=6, column=0, sticky=E, padx=10)
        Label(self.root, textvariable=self.status_var, width=70, anchor="w").grid(row=6, column=1, columnspan=4, sticky=W)

        Button(self.root, text="Start Download", command=self.start_download, width=18).grid(row=6, column=5, sticky=E, padx=10, pady=12)

        downloads = os.path.join(os.path.expanduser("~"), "Downloads")
        self.folder_var.set(downloads if os.path.isdir(downloads) else os.path.expanduser("~"))

    # -------------------------------------------------------------------------
    # ACTIONS
    # -------------------------------------------------------------------------
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
            progressive = yt.streams.filter(progressive=True, file_extension=self.video_format_var.get())
            adaptive = yt.streams.filter(only_video=True, file_extension=self.video_format_var.get())
            res_list = sorted({s.resolution for s in progressive if s.resolution} | {s.resolution for s in adaptive if s.resolution},
                              key=lambda x: int(x.replace("p","")))
            if not res_list:
                ui_queue.put(("error", "No resolutions found"))
                return
            ui_queue.put(("resolutions", res_list, yt.title))
        except Exception as e:
            tb = traceback.format_exc()
            ui_queue.put(("error", f"Failed to fetch: {e}\n{tb}"))

    def start_download(self):
        url = self.url_var.get().strip()
        if not url:
            self.status_var.set("Enter a YouTube URL first.")
            return
        # Reset progress bars
        self.audio_progress.set(0.0)
        self.video_progress.set(0.0)
        self.process_progress.set(0.0)

        threading.Thread(target=download_worker,
                         args=(url, self.res_var.get(), self.video_format_var.get(),
                               self.audio_format_var.get(), self.folder_var.get().strip() or os.path.expanduser("~"),
                               self.download_type_var.get()), daemon=True).start()
        self.status_var.set("Download started...")

    # -------------------------------------------------------------------------
    # PROCESS UI QUEUE
    # -------------------------------------------------------------------------
    def _process_ui_queue(self):
        try:
            while not ui_queue.empty():
                msg = ui_queue.get_nowait()
                if not msg: continue
                tag = msg[0]

                if tag == "stream_progress":
                    stype, percent = msg[1], msg[2]
                    if stype == "audio":
                        self.audio_progress.set(percent)
                    elif stype == "video":
                        self.video_progress.set(percent)

                elif tag == "processing_start":
                    if msg[1] == "audio" or msg[1] == "merge":
                        self.proc_bar.start(20)

                elif tag == "processing_end":
                    self.proc_bar.stop()
                    self.process_progress.set(100)

                elif tag == "status":
                    self.status_var.set(msg[1])

                elif tag == "resolutions":
                    res_list, title = msg[1], msg[2]
                    self.available_resolutions = res_list
                    self.res_combo["values"] = res_list
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
                    if messagebox.askyesno("Download Complete", f"File saved:\n{file_path}\n\nOpen containing folder?"):
                        folder = os.path.dirname(file_path)
                        if sys.platform == "win32": os.startfile(folder)
                        elif sys.platform == "darwin": subprocess.Popen(["open", folder])
                        else: subprocess.Popen(["xdg-open", folder])

        except Exception as e:
            print("UI queue error:", e, file=sys.stderr)
        finally:
            self.root.after(100, self._process_ui_queue)


# -----------------------------------------------------------------------------
# Main Entrypoint
# -----------------------------------------------------------------------------
def main():
    try:
        subprocess.run(["ffmpeg","-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        print("Warning: ffmpeg not found. Install ffmpeg for full features.", file=sys.stderr)

    root = Tk()
    app = YTDownloaderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

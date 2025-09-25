
# YouTube Downloader with Separate Progress Bars

A **Python 3 GUI application** to download YouTube videos and/or audio with **separate progress bars** for audio, video, and processing tasks. Built using **Tkinter** and **pytubefix**, with **FFmpeg** support for merging audio and video streams.

---

## Features

- Download **audio-only**, **video-only**, or **both (merged)**.
- Supports **progressive** and **adaptive streams** up to 4K resolution.
- **Separate progress bars** for:
  - Audio download
  - Video download
  - Processing (converting or merging)
- Fetch available resolutions dynamically from the video URL.
- **User-friendly GUI** with Tkinter.
- Cross-platform: Works on **Windows, macOS, and Linux**.
- Automatically sanitizes filenames to avoid illegal characters.
- Option to open the containing folder after download completes.

---

## Libraries Used

| Library        | Purpose                                                                 | Notes / Features |
|----------------|-------------------------------------------------------------------------|----------------|
| `pytubefix`    | Handles YouTube video fetching and downloading, including adaptive streams | A fork of `pytube` that fixes current YouTube API issues |
| `Tkinter`      | GUI framework for Python                                                 | Provides windows, buttons, entries, labels, progress bars |
| `ttk`          | Styled Tkinter widgets (Progressbar, Combobox)                           | Used for better GUI appearance |
| `subprocess`   | Runs FFmpeg commands for audio/video conversion and merging             | Required to merge video + audio and convert formats |
| `queue`        | Thread-safe communication between worker threads and UI thread          | Ensures safe updates to Tkinter widgets from background threads |
| `threading`    | Handles download tasks in background threads                             | Prevents GUI from freezing during downloads |
| `os` / `sys`   | File path handling, OS-specific folder opening                           | Required for cross-platform file handling |

---

## How It Works

1. **URL Input**: User enters a YouTube URL.
2. **Fetch Resolutions**: Clicking "Fetch Resolutions" queries available video resolutions.
3. **Select Options**: User can choose:
   - Resolution (e.g., 1080p, 720p)
   - Video format (`mp4`, `webm`)
   - Audio format (`m4a`, `mp3`, `webm`)
   - Download type (`video`, `audio`, or `both`)
4. **Download Process**:
   - **Audio-only**: Downloads the highest bitrate audio stream, converts it with FFmpeg.
   - **Video-only**: Downloads the selected resolution video stream.
   - **Both**: Downloads audio + video separately, then merges using FFmpeg.
5. **Progress Bars**:
   - Audio bar tracks audio download.
   - Video bar tracks video download.
   - Processing bar shows FFmpeg conversion/merge progress.
6. **Completion**:
   - Displays a message with file path.
   - Option to open the containing folder.

---

## Detailed Concepts

### 1. Tkinter GUI Programming

**Tkinter** is the standard GUI library for Python. Key components used in this project:

- **Widgets**: Basic GUI elements like `Label`, `Entry`, `Button`, `Progressbar`, and `Combobox`.
- **Grid Layout**: Positions widgets in a table-like grid using `row` and `column`. Example:

```python
Label(root, text="YouTube URL:").grid(row=0, column=0, sticky=E)
Entry(root, textvariable=url_var).grid(row=0, column=1, columnspan=3, sticky=W)
````

* **StringVar / DoubleVar**: Special Tkinter variables that **automatically update widgets when their value changes**. Examples:

  * `StringVar` for text (URL, status, selected resolution)
  * `DoubleVar` for numeric values (progress bars)

```python
progress_var = DoubleVar(value=0.0)
Progressbar(root, variable=progress_var, maximum=100)
progress_var.set(50.0)  # Updates bar visually
```

---

### 2. Threading & Queues for Background Tasks

Downloading video/audio can **block the GUI**. To prevent freezing:

* **Threading**: Run download tasks in a **separate background thread**.
* **Queue**: Safe communication channel from the background thread to the main GUI thread.

```python
ui_queue = queue.Queue()
threading.Thread(target=download_worker, args=(url,), daemon=True).start()
```

* Worker thread puts messages in the queue (`ui_queue.put(...)`).
* GUI polls the queue periodically (`root.after(100, _process_ui_queue)`), updating progress bars and status safely.

---

### 3. Subprocess Module for Running External Commands (FFmpeg)

**Subprocess** allows executing system commands from Python:

* Used for **audio conversion** and **merging audio/video**.
* Example of converting audio:

```python
ffmpeg_cmd = ["ffmpeg", "-y", "-i", "temp_audio.m4a", "final_audio.mp3"]
subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
```

* Example of merging audio + video:

```python
ffmpeg_cmd = [
    "ffmpeg", "-y",
    "-i", "video.mp4",
    "-i", "audio.m4a",
    "-c:v", "copy", "-c:a", "aac",
    "output.mp4"
]
```

* `stdout` and `stderr` are captured to prevent cluttering the console.

---

### 4. YouTube Streaming Concepts

**YouTube videos** are available in different stream types:

* **Progressive Streams**: Contain **both video and audio** in one file. Easier to download but often limited in resolution.
* **Adaptive Streams**: Separate **audio-only** and **video-only** streams. Allows high-resolution video (4K+) but requires **merging** using FFmpeg.

Example using `pytubefix`:

```python
yt.streams.filter(progressive=True, file_extension="mp4")  # video+audio
yt.streams.filter(only_video=True, file_extension="mp4")    # video only
yt.streams.filter(only_audio=True).order_by("abr").desc()   # audio only
```

---

### 5. File Handling and OS Paths

* **Cross-platform paths**: Use `os.path.join()` instead of hardcoding slashes.
* **Sanitize filenames** to avoid illegal characters:

```python
def sanitize_filename(name):
    return "".join(c if c.isalnum() or c in " ._-()" else "_" for c in name).strip()
```

* **Open folder after download** depending on OS:

```python
if sys.platform == "win32": os.startfile(folder)
elif sys.platform == "darwin": subprocess.Popen(["open", folder])
else: subprocess.Popen(["xdg-open", folder])
```

---

### 6. FFmpeg Basics

**FFmpeg** is a command-line tool for audio/video processing:

1. **Merging audio + video**:

```bash
ffmpeg -i video.mp4 -i audio.m4a -c:v copy -c:a aac output.mp4
```

2. **Converting audio format**:

```bash
ffmpeg -i temp_audio.m4a final_audio.mp3
```

3. **Options Used in Code**:

* `-y`: Overwrite output without asking
* `-loglevel error`: Only show errors
* `-c:v copy`: Copy video stream without re-encoding (fast)
* `-c:a aac`: Encode audio to AAC format for MP4 compatibility

---

## Installation

1. **Clone the repo**:

```bash
git clone https://github.com/fitandfine/youtube_downloader.git
cd youtube_downloader
```

2. **Install dependencies**:

```bash
pip install pytubefix
```

> Tkinter is included with Python; install via OS package manager if missing.

3. **Install FFmpeg**:

* Ubuntu: `sudo apt install ffmpeg`
* Windows: Download [FFmpeg](https://ffmpeg.org/download.html) and add to PATH
* macOS: `brew install ffmpeg`

---

## Usage

```bash
python app.py
```

1. Enter YouTube URL.
2. Fetch resolutions.
3. Choose resolution, video/audio format, and download type.
4. Select save folder.
5. Start download.
6. Watch separate progress bars for audio, video, and processing.
7. Open folder when prompted.

---

## Author

**Anup Chapain** – Created with clarity, separate progress bars, and comments for maintainability.

---

## License

MIT License – free to use and modify.




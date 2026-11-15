# yt-dlp-gui

A front-end for the command line downloader [yt-dlp](https://github.com/yt-dlp/yt-dlp), in two flavours that share the same presets, settings and download history:

* **`yt-dlp-gui`**: a desktop app (PySide6 / Qt)
* **`yt-dlp-tui`**: a terminal app (Textual) for consoles and SSH, plus a **batch mode** for scripts

**Highlights**

* **Music done right**: `mp3`, `m4a` and `opus` files get title / artist / album / track / date tags and **square cover art**. Messy YouTube titles like `Artist - Song (Official Video) [4K]` become *Artist* + *Song*. Files are named `Artist - Title.mp3`, playlists become album folders with track numbers, and there is an optional mp3 bitrate (320k…128k) and **volume normalization**.
* **Video quality control**: maximum **resolution** (4K…360p), **frame rate** and **codec** (H.264 for compatibility, VP9, AV1), optional **subtitles** and **SponsorBlock**.
* **Know before you download**: each link is checked first, showing the real title, length and **what quality you'll actually get** (e.g. `best 1080p → 720p30`). Playlists open a **picker** to choose items.
* **A proper queue**: a parallel download limit, **pause / resume** (partial downloads are kept, even across restarts), **retry**, readable **error messages**, a live **yt-dlp log**, and **history** that survives restarts with *Open* / *Show in folder*.
* **Convenience**: settings screen, clipboard watching, drag & drop, notifications, dark theme.

## Contents

- [Installation](#installation)
- [First run](#first-run)
- [Desktop app](#desktop-app)
- [Terminal app](#terminal-app)
- [Batch mode](#batch-mode)
- [Music downloads](#music-downloads)
- [Video quality](#video-quality)
- [Playlists](#playlists)
- [The download queue](#the-download-queue)
- [Settings](#settings)
- [Custom presets](#custom-presets)
- [Files and folders](#files-and-folders)
- [Troubleshooting](#troubleshooting)
- [Development](#development)

## Installation

### Portable (Windows, Linux, macOS)

1. Download the latest [stable](https://github.com/dsymbol/yt-dlp-gui/releases/latest) or [nightly](https://github.com/dsymbol/yt-dlp-gui/releases/tag/nightly) build:
   * Windows: `yt-dlp-gui-win64.zip`
   * Linux: `yt-dlp-gui-linux64.tar.gz`
   * macOS (Apple Silicon): `yt-dlp-gui-macos-arm64.zip`
2. Extract it anywhere.
3. Run **`yt-dlp-gui`** (desktop) or **`yt-dlp-tui`** (terminal). Both live in the same folder and share one configuration.

Python is not needed.

> **Linux:** the desktop app needs the usual Qt/X11 libraries. If it doesn't start, install `libxcb-cursor0` (Debian/Ubuntu) or `xcb-util-cursor` (Fedora/Arch).
> **macOS:** the app is unsigned. The first time, right-click it and choose *Open*, or run `xattr -dr com.apple.quarantine yt-dlp-gui`.

### From source

Requires [Python](https://www.python.org/downloads/) **3.9 – 3.14**.

```bash
git clone https://github.com/dsymbol/yt-dlp-gui
cd yt-dlp-gui

python -m venv .venv
# Windows (PowerShell):  .venv\Scripts\Activate.ps1
# macOS / Linux:         source .venv/bin/activate

pip install -r requirements.txt
cd app
python app.py      # desktop app
python tui.py      # terminal app
```

> On macOS / Linux use `python3` if `python` is not available.

## First run

On start-up both apps check for **yt-dlp**, **ffmpeg**, **ffprobe** and **deno** (deno solves YouTube's JavaScript challenges):

* Tools already on your `PATH` are used as they are.
* Missing tools are downloaded into the [binary folder](#files-and-folders): yt-dlp's own patched ffmpeg builds on Windows / Linux, and [martin-riedl.de](https://ffmpeg.martin-riedl.de) builds on macOS. The Windows ffmpeg download is about 200 MB, once. **Download** is disabled until this finishes.
* With *Update yt-dlp on start* enabled (the default), `yt-dlp -U` runs next. Sites change often, and old versions break.

## Desktop app

```bash
python app.py        # or the yt-dlp-gui executable
```

1. **Video URL**: paste links (one per line), **drag & drop** them onto the window, or turn on *Watch clipboard* in Settings.
2. **Preset**: the output format (`best`, `mp4`, `mp3`, `m4a`, `opus`, or [your own](#custom-presets)).
3. **Quality**: *Resolution*, *FPS* and *Codec* for video, *Bitrate* for mp3. Options that don't apply to the chosen preset are greyed out.
4. **Options**: *Subtitles*, *SponsorBlock*, *Save cover file*, *Normalize volume*, *Clean music titles*.
5. **Save to**: a folder (**Browse...**).
6. **+** adds the links to the list. **Download** starts everything queued, adding any links still in the box first.

Each row shows the title and length, the format and expected quality, size, progress, status, speed and ETA. Hover over a failed row's status to see the error.

**Right-click a row** (several rows can be selected) for *Open*, *Show in Folder*, *Pause*, *Start / Resume*, *Retry* / *Download Again*, *Edit Quality...*, *Choose Playlist Items...*, *Show Log...*, *Copy URL* and *Remove*.

Shortcuts on the list: **Enter** opens the file, **Space** pauses / resumes, **Delete** removes. A double-click opens the file.

**Menus**

* *File*: **Settings...** (`Ctrl+,`), Open Download Folder, Open Binaries Folder, Open Log Folder, Exit
* *Edit*: Clear URL List, Pause All, Resume All, Clear Finished & Failed, Clear All
* *Help*: About

The broom button clears finished and failed rows. When the window isn't focused, finished and failed downloads show a system notification.

## Terminal app

```bash
python tui.py [URL ...]        # or the yt-dlp-tui executable
```

```
┌ Video / playlist URLs (one per line) ──────────────────────────────────────────┐
│ https://www.youtube.com/watch?v=...                                            │
└────────────────────────────────────────────────────────────────────────────────┘
 Preset [mp3    ▾] Res [Best ▾] FPS [Any ▾] Codec [Any       ▾] Bitrate [320 kbps ▾]
 ▶ More options
 Save to [D:\Music                       ] […] [Add] [Download] [Clear done]
┌ Downloads ─────────────────────────────────────────────────────────────────────┐
│ Title                           Format          Size   Progress          Status│
│ Never Gonna Give You Up (3:33)  mp3 320k        3.3MiB ████████ 100.0%  Finish…│
│ My Album  [12 items]            mp3             4.1MiB ████░░░░  52.0%  Downlo…│
└────────────────────────────────────────────────────────────────────────────────┘
 Finished: 1  Downloading: 1
```

The mouse works too. *More options* expands the option checkboxes.

| Key | Action |
| --- | --- |
| `Tab` / `Shift+Tab` | Move between fields |
| `ctrl+n` | Add the links to the list |
| `ctrl+d` | Download everything queued |
| `ctrl+o` | Browse for the download folder |
| `ctrl+s` | Settings |
| `ctrl+g` | Show / hide the yt-dlp output of the selected download |
| `ctrl+l` | Clear finished and failed rows |
| `ctrl+p` | Command palette (includes **theme** switching) |
| `ctrl+q` | Quit (running downloads are paused and resume next time) |

On the downloads list:

| Key | Action |
| --- | --- |
| `↑` / `↓` | Select. The status line shows the file path or the error |
| `Enter` | Open the downloaded file (a playlist opens its folder) |
| `o` | Show in folder |
| `space` | Pause / resume |
| `r` | Retry (or download again) |
| `e` | Edit preset / quality. The resolution list shows only what the video has |
| `c` | Copy URL |
| `delete` | Remove (stops it if running) |

## Batch mode

`yt-dlp-tui` can download without any UI, for scripts and scheduled tasks:

```bash
python tui.py --batch links.txt                    # one URL per line, "#" comments allowed
python tui.py --batch - < links.txt                # from stdin
python tui.py --no-ui URL1 URL2 --preset mp3 --bitrate 320K --path ~/Music
```

| Option | |
| --- | --- |
| `--preset NAME` | Preset (default: last used) |
| `--path DIR` | Download folder (default: last used) |
| `--resolution`, `--fps`, `--codec`, `--bitrate` | Same choices as the apps, e.g. `--resolution 1080 --codec avc` |
| `--parallel N` | Downloads at once (1–10) |
| `--no-history` | Don't record these downloads in the history |

Other settings (file names, cookies, SponsorBlock, ...) come from `config.toml`. Playlists download completely. The exit code is `0` if everything finished and `1` if anything failed. `Ctrl+C` pauses, and the partial downloads resume next time.

## Music downloads

The audio presets are built for music libraries:

| Preset | Format | Notes |
| --- | --- | --- |
| `mp3` | MP3, VBR V0 (~245 kbps) or a fixed bitrate | Plays everywhere. Supports *Bitrate* and *Normalize volume* |
| `m4a` | YouTube's AAC (~128 kbps), **not re-encoded** | No quality loss, very compatible |
| `opus` | YouTube's Opus (~130 kbps), **not re-encoded** | Best quality per size, newer players |

Every audio download gets:

* **Tags**: title, artist, date, and album / track number when available. YouTube Music gives full album info; for a playlist, the playlist name becomes the album and its order the track numbers.
* **Cover art**: the video thumbnail, centre-cropped to a square like an album cover.
* **File name**: `Artist - Title.mp3` (change it in Settings).

**Clean music titles** (on by default) fixes typical music video titles when YouTube has no artist info:

| YouTube title | Artist | Title |
| --- | --- | --- |
| `Rick Astley - Never Gonna Give You Up (Official Video) (4K Remaster)` | Rick Astley | Never Gonna Give You Up |
| `Band - Song [Lyrics]` | Band | Song |
| `Song (feat. Someone)` | *(channel)* | Song (feat. Someone) |

It splits on the first ` - ` and removes bracketed parts that mention official / lyrics / audio / video / visualizer / remaster / HD / 4K / MV. Turn it off for non-music videos whose titles contain ` - `.

**Normalize volume** (mp3) evens out loudness (EBU R128, −14 LUFS), so tracks from different channels play at a similar volume. **SponsorBlock** on audio also cuts the non-music parts of music videos (intros, talking, outros) where the community has marked them.

> *Bitrate* and *Normalize volume* are disabled for `m4a` / `opus`, because those copy YouTube's audio unchanged. yt-dlp would silently ignore both settings there.

## Video quality

* **Resolution / FPS** are upper limits. The app picks the best stream **at or below** your choice, falling back to the closest available. For example, *720p + 30 fps* on a 4K60 video gives 720p30.
* **Codec** is a preference that ranks *before* resolution. *H.264* plays everywhere (old TVs, editors, phones), but YouTube only offers it up to 1080p, so choosing it caps the quality there. *VP9* and *AV1* give better quality per size. *Any* takes the best available, which above 1080p is often AV1.
* As soon as a link is added, the **Format** column shows what you'll actually get, e.g. `best 1080p → 720p30` when the video only goes up to 720p30.
* To change one download, use *Edit Quality...* / `e`. It lists only the resolutions that video really has.

The `best` preset prefers `.mp4`. `mp4` always gives H.264 + AAC. Choosing VP9 with `best` can produce `.webm` / `.mkv` files.

## Playlists

Paste a playlist, album or channel URL. Once it has been checked, a **picker** lists its videos (all ticked). Untick what you don't want, or choose *Download all*. The row shows `[5/12]` for the selection and `Downloading 3/5` while running.

With *Playlist subfolder* (Settings, on by default), files go into a folder named after the playlist. Audio downloads use the playlist as the album name and its order as track numbers.

Batch mode downloads whole playlists without asking.

## The download queue

| Status | Meaning |
| --- | --- |
| Queued | Added, not started (press Download) |
| Waiting | Started, waiting for a free slot or for playlist items to be chosen |
| Starting | yt-dlp is extracting info |
| Downloading | Receiving data (`3/12` for playlists) |
| Converting | ffmpeg is merging, converting, tagging or embedding the cover |
| Paused | Stopped by you or by quitting. The partial download is kept |
| Finished | Done. *Open* / *Show in folder* |
| Error | Failed. The reason is in the status line / tooltip, the full output in *Show Log* |

* At most *Parallel downloads* (default 3) run at once. More at once tends to trigger YouTube throttling (HTTP 403).
* **Pause / resume** continues where it stopped, also after closing the app. **Retry** on a failed download also resumes.
* Every download works in its own hidden temporary folder inside the save folder (`.yt-dlp-gui-tmp-…`). This keeps simultaneous downloads of the same video from clashing. It's removed when the download finishes or is removed, and kept while paused or failed.
* The list is saved to `history.json` and restored on start (up to 500 entries). Use *Clear finished* to tidy up.

## Settings

*File → Settings...* in the desktop app, `ctrl+s` in the terminal app. They're stored in `config.toml`:

| Setting | Default | |
| --- | --- | --- |
| Parallel downloads (`max_downloads`) | 3 | 1–10 |
| Video file name (`filename_video`) | Title | *Title*, *Title [id]*, *Artist - Title*, *Date - Title* |
| Music file name (`filename_audio`) | Artist - Title | same choices |
| Playlist subfolder (`playlist_folder`) | on | one folder per playlist |
| Subtitle languages (`subtitle_langs`) | `en.*` | yt-dlp `--sub-langs` syntax, e.g. `en.*,de,fr` |
| Browser cookies (`cookies_browser`) | none | log in with your browser's cookies for age-restricted / members-only videos. Close the browser if it fails |
| Theme (`theme`) | system | desktop app: system / light / dark. In the terminal use `ctrl+p` → *Change theme* |
| Watch clipboard (`clipboard_watch`) | off | copied links are added to the URL box |
| Notifications (`notifications`) | on | system notification (desktop) / toast and bell (terminal) |
| Update yt-dlp on start (`update_ytdlp`) | on | turn off if a package manager (winget, pip, brew) manages yt-dlp |
| Extra yt-dlp args (`global_args`) | | added to every download, e.g. `--limit-rate 5M` |

The main window's choices (preset, folder, quality, options) are remembered automatically.

## Custom presets

Presets live in the `[presets]` table of `config.toml`. The name appears in the dropdown, and the value is yt-dlp arguments, as a string or (better, for anything with spaces or quotes) a list. Edit the file while the apps are closed, since they rewrite it on exit.

```toml
[presets]
wav = "--extract-audio --audio-format wav --audio-quality 0"     # wav can't hold cover art
flac = ["-x", "--audio-format", "flac", "--embed-metadata", "--embed-thumbnail", "--convert-thumbnails", "jpg"]
mkv = "-f bv*+ba/b --merge-output-format mkv --embed-metadata --embed-thumbnail --embed-chapters"
```

* A preset containing `-x` / `--extract-audio` counts as **audio**: the video options are disabled and the music options enabled. *Bitrate* applies when it converts to mp3. *Normalize* applies when it converts to mp3 / wav / flac / alac.
* For square cover art, copy the `--ppa "ThumbnailsConvertor+FFmpeg_o:…"` entry from the `mp3` preset.
* The app adds its own options **before** the preset, so a preset (and *Extra yt-dlp args*) can override them, e.g. your own `-o` output template. The exception is *Bitrate*, which deliberately overrides the preset's `--audio-quality`.
* Don't use `--quiet` or `--progress-template`: the apps read yt-dlp's progress output.

The full command for each download is the first line of its log (*Show Log* / `ctrl+g`).

## Files and folders

| What | From source | Portable build |
| --- | --- | --- |
| `config.toml` (settings, presets) | `app/config.toml` | `_internal/config.toml` |
| `history.json` (download list) | `app/history.json` | `_internal/history.json` |
| `debug.log` | `app/debug.log` | `_internal/debug.log` |

Downloaded tools (yt-dlp, ffmpeg, ffprobe, deno), shared by every install:

* **Windows**: `%LOCALAPPDATA%\yt-dlp-gui\yt-dlp-gui`
* **macOS**: `~/Library/Application Support/yt-dlp-gui`
* **Linux**: `~/.local/share/yt-dlp-gui`

> **Updating a portable install:** copy the old `_internal/config.toml` and `history.json` into the new version's `_internal` folder to keep your settings and list. New settings are added automatically. To get new or changed presets, copy their lines from the new `config.toml` into yours.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| A download shows **Error** | Select it: the status line / tooltip shows yt-dlp's reason. *Show Log* has the full output. |
| `Sign in to confirm you're not a bot` / age-restricted / members-only | Settings → *Browser cookies* → your browser (close the browser first). |
| `HTTP Error 403: Forbidden` | YouTube throttling. Lower *Parallel downloads*, then *Retry*. Keep yt-dlp updated. |
| No cover / artist in music files | Use `mp3` / `m4a` / `opus`. With an older `config.toml`, copy the new presets. `wav` can't hold covers. Album is only set for YouTube Music tracks and playlists. |
| Wrong artist split (non-music video) | Turn off *Clean music titles* for it. |
| Video won't play on a TV / in an editor | Choose *Codec: H.264* or the `mp4` preset. |
| Lower resolution than chosen | The video doesn't have it (check the Format column's `→` or *Edit Quality*). |
| Playlist waits with "Choose items" | The picker is open (maybe behind the window), or reopen it with *Choose Playlist Items...*. |
| `Download` stays disabled | The tool check is still running (status bar), or it failed: check your internet connection and `debug.log`. |
| `yt-dlp -U` errors in the log | yt-dlp comes from a package manager. Update it there and turn off *Update yt-dlp on start*. |
| TUI looks garbled | Use a modern terminal (Windows Terminal, iTerm2, GNOME Terminal…). |
| `ModuleNotFoundError` | Activate the virtual environment and run `pip install -r requirements.txt`. |

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest            # offline tests: command building, parsing, queue, installer
```

The tests use a fake yt-dlp and need no network. GitHub Actions runs them on every push (Linux + Windows, Python 3.9 and 3.12).

**Building the portable release** (any OS, in a clean virtual environment; step-by-step Windows guide in [BUILD.md](BUILD.md)):

```bash
pip install -r requirements.txt pyinstaller==6.16.0
cd app
pyinstaller --clean -y yt-dlp-gui.spec    # -> app/dist/yt-dlp-gui/
```

The **Release** workflow (GitHub → *Actions*) builds Windows, Linux and macOS archives, runs the tests, and publishes a release. Tick *nightly* for a prerelease tagged `nightly`.

> PyInstaller refuses to run if the obsolete `pathlib` backport is installed (`pip uninstall pathlib`). A clean virtual environment avoids this.

**Layout**

```
app/
├── app.py           # desktop app (Qt main window)
├── gui_dialogs.py   # desktop dialogs: settings, playlist picker, edit quality, log
├── tui.py           # terminal app + batch mode (Textual)
├── manager.py       # shared download queue: threads, parallel limit, pause/resume, history
├── core.py          # shared: settings, yt-dlp command building, output parsing, probing
├── deps.py          # shared: download missing tools, self-update yt-dlp
├── dep_dl.py        # Qt thread wrapper around deps.ensure_dependencies
├── utils.py         # Qt-specific constants
├── ui/              # Qt Designer layout (main_window.ui) and generated code
├── assets/          # icon
├── config.toml      # settings and presets
└── yt-dlp-gui.spec  # PyInstaller build for both executables
tests/               # pytest suite
```

## License

See [LICENSE](LICENSE).

# Building the portable exe (Windows)

You **don't need an exe** to use the apps. From the `app` folder, `python app.py` (desktop) or `python tui.py` (terminal) works directly.

Build an exe only if you want a standalone folder that runs **without Python**, for example to copy to another PC or a USB stick.

## Requirements

* Windows with [Python](https://www.python.org/downloads/) 3.9 – 3.14
* About 1 GB of free space for the build environment, and about 150 MB for the result
* Internet access (to install the build tools)

## Steps (PowerShell)

```powershell
cd C:\Users\Arkajyoti\Downloads\yt-dlp-gui

# 1. Create a clean environment (one time only)
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt pyinstaller==6.16.0

# 2. Build both programs
cd app
..\.venv\Scripts\pyinstaller --clean -y yt-dlp-gui.spec
```

The build takes about 2 minutes.

> **Why the separate `.venv`?** Your main Python has an obsolete `pathlib` package installed, and PyInstaller refuses to run with it ("The 'pathlib' package is an obsolete backport…"). The virtual environment avoids that without changing your main Python.

## Result

```
app\dist\yt-dlp-gui\
├── yt-dlp-gui.exe     ← desktop app (double-click)
├── yt-dlp-tui.exe     ← terminal app
└── _internal\         ← libraries, config.toml, history.json, debug.log
```

Move the whole `yt-dlp-gui` folder anywhere (e.g. `D:\Apps\yt-dlp-gui`) and run `yt-dlp-gui.exe`. Always keep `_internal` next to the exe files.

To download from the terminal app without its UI:

```powershell
.\yt-dlp-tui.exe --no-ui "https://www.youtube.com/watch?v=..." --preset mp3 --bitrate 320K
```

## Good to know

* **Presets and settings are copied in.** The build copies the current `app\config.toml` into `_internal\config.toml`, including all presets (best, mp4, mp3, m4a, opus) and your current settings such as the download folder.
* **It's a separate copy.** The exe keeps its own settings and download history inside `_internal`. Changes there don't affect `python app.py`, and the reverse is true too.
* **Tools on first run.** On this PC the exe uses the yt-dlp, ffmpeg and deno you already have installed. On a PC without them, the first start downloads them automatically into `%LOCALAPPDATA%\yt-dlp-gui\yt-dlp-gui` (about 200 MB for ffmpeg, once).
* **Antivirus warnings.** Home-made PyInstaller exes are sometimes flagged as false positives. The official GitHub release builds use a special PyInstaller build to reduce this.

## Updating the exe later

After the code or presets change, run the build command again:

```powershell
cd C:\Users\Arkajyoti\Downloads\yt-dlp-gui\app
..\.venv\Scripts\pyinstaller --clean -y yt-dlp-gui.spec
```

To keep the settings and history of an exe folder you've already been using, copy its old `_internal\config.toml` and `_internal\history.json` into the new build's `_internal` folder. If you do, also copy in the `[presets]` section from the new `config.toml` to get the latest presets. New settings are added automatically.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `.venv\Scripts\python` not found | Run the commands from the `yt-dlp-gui` folder (step 1 starts with `cd`). |
| `pip install` fails | Check your internet connection. Delete the `.venv` folder and repeat step 1. |
| `pathlib` error from PyInstaller | You ran your main Python's `pyinstaller`. Use `..\.venv\Scripts\pyinstaller` as shown. |
| The exe closes immediately | Check `_internal\debug.log`. Make sure `_internal` is next to the exe. |
| Exe is blocked by Windows SmartScreen | Click *More info* → *Run anyway* (the exe isn't signed). |

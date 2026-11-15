# -*- mode: python ; coding: utf-8 -*-
# Builds yt-dlp-gui.exe (windowed) and yt-dlp-tui.exe (console) into one folder,
# so both share the same _internal directory, config.toml and debug.log.
#
#   pyinstaller --clean -y yt-dlp-gui.spec

import sys

datas = [("config.toml", "."), ("assets", "assets")]
icon = "assets/yt-dlp-gui.ico" if sys.platform == "win32" else None  # .ico is Windows-only

gui_a = Analysis(["app.py"], datas=datas, excludes=["textual"])
gui_pyz = PYZ(gui_a.pure)
gui_exe = EXE(
    gui_pyz,
    gui_a.scripts,
    exclude_binaries=True,
    name="yt-dlp-gui",
    console=False,
    icon=icon,
)

tui_a = Analysis(["tui.py"], datas=datas, excludes=["PySide6", "qtawesome"])
tui_pyz = PYZ(tui_a.pure)
tui_exe = EXE(
    tui_pyz,
    tui_a.scripts,
    exclude_binaries=True,
    name="yt-dlp-tui",
    console=True,
    icon=icon,
)

coll = COLLECT(
    gui_exe,
    gui_a.binaries,
    gui_a.datas,
    tui_exe,
    tui_a.binaries,
    tui_a.datas,
    name="yt-dlp-gui",
)

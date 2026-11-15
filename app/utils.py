from PySide6 import QtCore

from core import BIN_DIR, ROOT, load_toml, save_toml  # noqa: F401  (re-exported)


class ItemRoles:
    IdRole = QtCore.Qt.UserRole
    LinkRole = QtCore.Qt.UserRole + 1
    PathRole = QtCore.Qt.UserRole + 2


class TreeColumn:
    TITLE = 0
    PRESET = 1
    SIZE = 2
    PROGRESS = 3
    STATUS = 4
    SPEED = 5
    ETA = 6

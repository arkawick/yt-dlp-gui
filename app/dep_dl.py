from PySide6.QtCore import QThread, Signal

from deps import ensure_dependencies


class DepWorker(QThread):
    progress = Signal(str)

    def __init__(self, update_ytdlp: bool):
        super().__init__()
        self.update_ytdlp = update_ytdlp

    def run(self):
        ensure_dependencies(self.update_ytdlp, self.progress.emit)
        self.finished.emit()

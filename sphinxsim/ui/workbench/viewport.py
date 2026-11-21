"""Native Qt/PyVista canvas used by the desktop Workbench."""
from __future__ import annotations

from PySide6 import QtCore
from pyvistaqt import QtInteractor


class PreviewViewport(QtInteractor):
    def __init__(self, parent=None):
        super().__init__(parent=parent, off_screen=False, border=False)
        self.setMinimumSize(320, 220)
        self.setToolTip("Drag to rotate; middle-drag to pan; wheel to zoom; R resets the view.")

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key.Key_R:
            self.subplot(0, 0)
            self.reset_camera()
            self.render()
            event.accept()
            return
        super().keyPressEvent(event)

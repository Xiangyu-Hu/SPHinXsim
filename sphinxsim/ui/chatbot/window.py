"""Standalone Qt WebEngine window for the SPHinXsim chatbot."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QMainWindow

from .bridge import ChatBridge


class ChatbotWindow(QMainWindow):
    """Independent chatbot window; it does not embed preview or editor UI."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("SPHinXsim")
        self.resize(900, 760)
        self.setMinimumSize(650, 550)

        self._view = QWebEngineView(self)
        self._bridge = ChatBridge(self)
        channel = QWebChannel(self._view.page())
        channel.registerObject("bridge", self._bridge)
        self._view.page().setWebChannel(channel)
        self.setCentralWidget(self._view)

        resource = Path(__file__).with_name("web") / "index.html"
        self._view.setUrl(QUrl.fromLocalFile(str(resource.resolve())))

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._bridge.close()
        super().closeEvent(event)

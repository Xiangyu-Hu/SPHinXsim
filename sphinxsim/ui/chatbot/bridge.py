"""Qt WebChannel bridge for the first local chatbot implementation."""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal, Slot

from .controller import ChatController


class ChatWorker(QObject):
    """Background worker that owns the provider and conversation controller."""

    started = Signal()
    delta = Signal(str)
    finished = Signal()
    failed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._controller: ChatController | None = None

    @Slot(str)
    def respond(self, text: str) -> None:
        try:
            self.started.emit()
            if self._controller is None:
                self._controller = ChatController()
            response = self._controller.respond(text)
            self.delta.emit(response)
            self.finished.emit()
        except Exception as exc:  # pragma: no cover - defensive Qt boundary
            self.failed.emit(str(exc))


class ChatBridge(QObject):
    """QObject exposed to the local HTML page through QWebChannel."""

    requestMessage = Signal(str)
    assistantMessageStarted = Signal()
    assistantTextDelta = Signal(str)
    assistantMessageFinished = Signal()
    statusChanged = Signal(str)
    error = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._busy = False
        self._thread = QThread(self)
        self._worker = ChatWorker()
        self._worker.moveToThread(self._thread)
        self.requestMessage.connect(self._worker.respond)
        self._worker.started.connect(self._on_started)
        self._worker.delta.connect(self.assistantTextDelta)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._thread.start()

    @Slot(str)
    def sendMessage(self, text: str) -> None:
        """Receive one message from JavaScript."""
        cleaned = text.strip()
        if not cleaned:
            self.error.emit("Please enter a message.")
            return
        if self._busy:
            self.error.emit("The chatbot is still responding.")
            return
        self._busy = True
        self.requestMessage.emit(cleaned)

    def _on_started(self) -> None:
        self.assistantMessageStarted.emit()
        self.statusChanged.emit("Responding")

    def _on_finished(self) -> None:
        self._busy = False
        self.assistantMessageFinished.emit()
        self.statusChanged.emit("")

    def _on_failed(self, message: str) -> None:
        self._busy = False
        self.assistantMessageFinished.emit()
        self.error.emit(message)
        self.statusChanged.emit("")

    def close(self) -> None:
        """Stop the worker thread before the window is destroyed."""
        self._thread.quit()
        self._thread.wait(2000)

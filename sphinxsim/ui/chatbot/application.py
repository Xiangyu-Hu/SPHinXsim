"""Application launcher for the standalone chatbot window."""

from __future__ import annotations

import sys
from collections.abc import Sequence


def launch_chatbot(argv: Sequence[str] | None = None) -> int:
    """Create and run the standalone chatbot application."""
    from PySide6.QtWidgets import QApplication

    from .window import ChatbotWindow

    application = QApplication.instance()
    owns_application = application is None
    if application is None:
        application = QApplication(list(argv) if argv is not None else sys.argv)

    window = ChatbotWindow()
    window.show()

    if not owns_application:
        return 0
    return application.exec()

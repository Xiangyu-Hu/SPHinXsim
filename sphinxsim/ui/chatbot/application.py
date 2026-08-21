"""Launch the standalone Chainlit chatbot server."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile


_CHAINLIT_CONFIG = """[meta]
generated_by = "2.11.1"

[UI]
name = "SPHinXsim"
description = "General-purpose SPHinXsim chatbot"
default_theme = "light"
layout = "wide"
cot = "hidden"

[features.spontaneous_file_upload]
enabled = true
accept = ["application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp"]
max_files = 10
max_size_mb = 20
"""


class ChatbotDependencyError(RuntimeError):
    """Raised when the optional Chainlit dependency is unavailable."""


def _chainlit_command(
    *,
    host: str,
    port: int,
    headless: bool,
    watch: bool,
) -> list[str]:
    """Build the child-process command without importing Chainlit internals."""
    target = Path(__file__).with_name("chainlit_app.py").resolve()
    command = [
        sys.executable,
        "-m",
        "chainlit",
        "run",
        str(target),
        "--host",
        host,
        "--port",
        str(port),
    ]
    if headless:
        command.append("--headless")
    if watch:
        command.append("--watch")
    return command


def launch_chatbot(
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    headless: bool = False,
    watch: bool = False,
) -> int:
    """Run Chainlit in an isolated local app root and return its exit code."""
    if importlib.util.find_spec("chainlit") is None:
        raise ChatbotDependencyError(
            "Chainlit is not installed. Install the chatbot extra with: "
            "pip install -e .[chatbot]"
        )
    if not 1 <= port <= 65535:
        raise ValueError("chatbot port must be between 1 and 65535")

    command = _chainlit_command(
        host=host,
        port=port,
        headless=headless,
        watch=watch,
    )
    environment = os.environ.copy()
    # Chainlit's Click command treats a generic DEBUG variable as the value for
    # its boolean --debug option. Build environments commonly set DEBUG=release,
    # which is not a valid boolean and would prevent the server from starting.
    environment.pop("DEBUG", None)
    with tempfile.TemporaryDirectory(prefix="sphinxsim-chainlit-") as app_root:
        # Chainlit writes generated configuration and temporary files below APP_ROOT.
        # Keeping it outside both the repository and site-packages makes the command
        # work from source trees, build directories, and installed wheels alike.
        environment["CHAINLIT_APP_ROOT"] = app_root
        config_directory = Path(app_root, ".chainlit")
        config_directory.mkdir()
        config_directory.joinpath("config.toml").write_text(
            _CHAINLIT_CONFIG,
            encoding="utf-8",
        )
        # The actual greeting is sent by on_chat_start; suppress Chainlit's
        # generic developer welcome page.
        Path(app_root, "chainlit.md").write_text("", encoding="utf-8")
        return subprocess.call(command, env=environment)

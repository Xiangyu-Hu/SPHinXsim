"""Non-invasive adapter around the existing SPHinXsim CLI commands.

The adapter deliberately invokes the public CLI in a child process instead of
calling private functions from ``sphinxsim.cli``.  Existing ``generate``,
``update``, ``validate``, ``preview``, ``run`` and ``explore`` behavior remains
the single implementation used by both people and future Agent/graph tools.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Mapping, Sequence


@dataclass(frozen=True)
class CLICommandResult:
    """Structured result of one SPHinXsim CLI invocation."""

    command: tuple[str, ...]
    returncode: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def require_success(self) -> "CLICommandResult":
        """Return this result or raise a structured command error."""
        if not self.succeeded:
            raise CLICommandError(self)
        return self


class CLICommandError(RuntimeError):
    """Raised by :meth:`CLICommandResult.require_success`."""

    def __init__(self, result: CLICommandResult) -> None:
        self.result = result
        if result.timed_out:
            detail = "timed out"
        else:
            detail = f"failed with exit code {result.returncode}"
        stderr = result.stderr.strip()
        if stderr:
            detail = f"{detail}: {stderr}"
        super().__init__(f"SPHinXsim command {detail}")


class CLIInvocationError(RuntimeError):
    """Raised when the SPHinXsim child process cannot be started."""


class SPHinXsimCLIAdapter:
    """Expose existing SPHinXsim commands as Agent-friendly Python methods.

    This is intentionally a process boundary.  It prevents native C++ exits or
    global SPHinXsys runtime state from terminating or contaminating a future
    Agent process.  The adapter itself does not add concurrency; calls are
    synchronous and therefore serial unless a later RunManager queues them.
    """

    def __init__(
        self,
        *,
        cwd: str | Path | None = None,
        command_prefix: Sequence[str] | None = None,
        environment: Mapping[str, str] | None = None,
        default_timeout: float | None = None,
    ) -> None:
        self.cwd = Path(cwd).resolve() if cwd is not None else Path.cwd().resolve()
        self.command_prefix = tuple(command_prefix or (sys.executable, "-m", "sphinxsim"))
        self.environment = dict(environment or {})
        self.default_timeout = default_timeout

    @staticmethod
    def _path(value: str | Path) -> str:
        return str(Path(value))

    def _absolute_path(self, value: str | Path) -> str:
        """Resolve a CLI input path relative to the adapter working directory.

        ``cmd_run`` currently relocates every relative path under
        ``PROJECT_ROOT/.build-temp`` after loading it.  Passing an already
        ``.build-temp``-relative path would therefore duplicate that segment.
        An absolute path preserves the existing command behavior while avoiding
        the ambiguous second resolution.
        """
        path = Path(value)
        if not path.is_absolute():
            path = self.cwd / path
        return str(path.resolve())

    @staticmethod
    def _timeout_text(value: str | bytes | None) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value

    def _invoke(
        self,
        arguments: Sequence[str],
        *,
        timeout: float | None = None,
    ) -> CLICommandResult:
        command = (*self.command_prefix, *map(str, arguments))
        child_environment = os.environ.copy()
        child_environment.update(self.environment)
        child_environment.setdefault("PYTHONUNBUFFERED", "1")
        child_environment.setdefault("PYTHONIOENCODING", "utf-8")
        effective_timeout = self.default_timeout if timeout is None else timeout
        started = time.monotonic()

        try:
            completed = subprocess.run(
                command,
                cwd=str(self.cwd),
                env=child_environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=effective_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return CLICommandResult(
                command=command,
                returncode=None,
                stdout=self._timeout_text(exc.stdout),
                stderr=self._timeout_text(exc.stderr),
                duration_seconds=time.monotonic() - started,
                timed_out=True,
            )
        except OSError as exc:
            raise CLIInvocationError(
                f"Unable to start SPHinXsim command {command!r}: {exc}"
            ) from exc

        return CLICommandResult(
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_seconds=time.monotonic() - started,
        )

    def generate(
        self,
        description: str,
        output_path: str | Path,
        *,
        timeout: float | None = None,
    ) -> CLICommandResult:
        """Generate and automatically validate a JSON config."""
        if not description.strip():
            raise ValueError("description must not be empty")
        return self._invoke(
            ("generate", description, "--output", self._path(output_path)),
            timeout=timeout,
        )

    def update(
        self,
        config_path: str | Path,
        instruction: str,
        *,
        output_path: str | Path | None = None,
        patch_mode: bool = False,
        dry_run: bool = False,
        strict: bool = True,
        timeout: float | None = None,
    ) -> CLICommandResult:
        """Update a config through the existing natural-language command."""
        if not instruction.strip():
            raise ValueError("instruction must not be empty")
        if dry_run and not patch_mode:
            raise ValueError("dry_run requires patch_mode")

        arguments = ["update", self._path(config_path), instruction]
        if output_path is not None:
            arguments.extend(("--output", self._path(output_path)))
        if patch_mode:
            arguments.append("--patch-mode")
            if dry_run:
                arguments.append("--dry-run")
            arguments.extend(("--strict", str(strict).lower()))
        return self._invoke(arguments, timeout=timeout)

    def validate(
        self,
        config_path: str | Path,
        *,
        timeout: float | None = None,
    ) -> CLICommandResult:
        """Run the existing explicit validation command."""
        return self._invoke(("validate", self._path(config_path)), timeout=timeout)

    def preview(
        self,
        config_path: str | Path,
        *,
        with_particles: bool = False,
        off_screen: bool = False,
        screenshot_path: str | Path | None = None,
        timeout: float | None = None,
    ) -> CLICommandResult:
        """Run the existing preview command."""
        arguments = ["preview", self._path(config_path)]
        if with_particles:
            arguments.append("--with-particles")
        if off_screen:
            arguments.append("--off-screen")
        if screenshot_path is not None:
            arguments.extend(("--screenshot", self._path(screenshot_path)))
        return self._invoke(arguments, timeout=timeout)

    def run(
        self,
        config_path: str | Path,
        *,
        timeout: float | None = None,
    ) -> CLICommandResult:
        """Run the complete existing SPHinXsim simulation command."""
        return self._invoke(("run", self._absolute_path(config_path)), timeout=timeout)

    def explore(
        self,
        question: str,
        *,
        timeout: float | None = None,
    ) -> CLICommandResult:
        """Ask the existing schema/capability exploration command."""
        if not question.strip():
            raise ValueError("question must not be empty")
        return self._invoke(("explore", question), timeout=timeout)

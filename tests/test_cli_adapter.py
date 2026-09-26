"""Tests for the non-invasive SPHinXsim CLI adapter."""

from __future__ import annotations

import json
from pathlib import Path
import json
import sys
import subprocess
from unittest.mock import patch

import pytest

from sphinxsim.config.schemas import SimulationConfig
from sphinxsim.services import CLICommandError, SPHinXsimCLIAdapter


def test_managed_invocation_persists_timeout_attempt(tmp_path: Path) -> None:
    control = tmp_path / "control"
    adapter = SPHinXsimCLIAdapter(
        cwd=tmp_path,
        command_prefix=(
            sys.executable,
            "-c",
            "import time; time.sleep(10)",
        ),
        control_dir=control,
    )

    result = adapter._invoke(("run",), timeout=0.1)

    assert result.timed_out is True
    assert result.succeeded is False
    record = json.loads(Path(result.attempt_path).read_text(encoding="utf-8"))
    assert record["failure_kind"] == "solver_timeout"
    assert record["status"] == "failed"
    assert record["returncode"] is not None


def test_managed_invocation_honors_durable_cancel_marker(tmp_path: Path) -> None:
    control = tmp_path / "control"
    control.mkdir()
    (control / "cancel.request").touch()
    adapter = SPHinXsimCLIAdapter(
        cwd=tmp_path,
        command_prefix=(sys.executable, "-c", "raise SystemExit(99)"),
        control_dir=control,
    )

    result = adapter._invoke(("run",), timeout=5)

    assert result.cancelled is True
    assert result.returncode is None
    record = json.loads(Path(result.attempt_path).read_text(encoding="utf-8"))
    assert record["failure_kind"] == "cancelled"


def _completed(command: tuple[str, ...], returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, returncode, stdout="ok\n", stderr="")


def test_generate_invokes_existing_cli_without_shell_parsing(tmp_path: Path) -> None:
    adapter = SPHinXsimCLIAdapter(
        cwd=tmp_path,
        command_prefix=("python", "-m", "sphinxsim"),
    )
    output_path = tmp_path / "folder with spaces" / "case file.json"

    with patch("sphinxsim.services.cli_adapter.subprocess.run") as run_process:
        run_process.return_value = _completed(())
        result = adapter.generate("water dam break with spaces", output_path)

    assert result.succeeded is True
    command = run_process.call_args.args[0]
    assert command == (
        "python",
        "-m",
        "sphinxsim",
        "generate",
        "water dam break with spaces",
        "--output",
        str(output_path),
    )
    assert "shell" not in run_process.call_args.kwargs


def test_update_builds_patch_mode_arguments(tmp_path: Path) -> None:
    adapter = SPHinXsimCLIAdapter(cwd=tmp_path, command_prefix=("sphinxsim",))
    config_path = tmp_path / "case.json"

    with patch("sphinxsim.services.cli_adapter.subprocess.run") as run_process:
        run_process.return_value = _completed(())
        result = adapter.update(
            config_path,
            "simulate for 2 seconds",
            patch_mode=True,
            dry_run=True,
            strict=False,
        )

    assert result.succeeded is True
    assert run_process.call_args.args[0] == (
        "sphinxsim",
        "update",
        str(config_path),
        "simulate for 2 seconds",
        "--patch-mode",
        "--dry-run",
        "--strict",
        "false",
    )


def test_update_rejects_dry_run_without_patch_mode(tmp_path: Path) -> None:
    adapter = SPHinXsimCLIAdapter(cwd=tmp_path)
    with pytest.raises(ValueError, match="dry_run requires patch_mode"):
        adapter.update(tmp_path / "case.json", "change end time", dry_run=True)


def test_run_is_synchronous_and_uses_existing_command(tmp_path: Path) -> None:
    adapter = SPHinXsimCLIAdapter(cwd=tmp_path, command_prefix=("sphinxsim",))
    config_path = tmp_path / "case.json"

    with patch("sphinxsim.services.cli_adapter.subprocess.run") as run_process:
        run_process.return_value = _completed(())
        result = adapter.run(config_path, timeout=42)

    assert result.succeeded is True
    assert run_process.call_args.args[0] == ("sphinxsim", "run", str(config_path.resolve()))
    assert run_process.call_args.kwargs["timeout"] == 42
    assert run_process.call_args.kwargs["env"]["PYTHONIOENCODING"] == "utf-8"


def test_run_passes_isolated_output_directory(tmp_path: Path) -> None:
    adapter = SPHinXsimCLIAdapter(cwd=tmp_path, command_prefix=("sphinxsim",))
    config_path = tmp_path / "case.json"
    output_dir = tmp_path / "agent runs" / "run_001"

    with patch("sphinxsim.services.cli_adapter.subprocess.run") as run_process:
        run_process.return_value = _completed(())
        adapter.run(config_path, output_dir=output_dir)

    assert run_process.call_args.args[0] == (
        "sphinxsim",
        "run",
        str(config_path.resolve()),
        "--output-dir",
        str(output_dir.resolve()),
    )


def test_run_resolves_build_temp_relative_path_only_once(tmp_path: Path) -> None:
    adapter = SPHinXsimCLIAdapter(cwd=tmp_path, command_prefix=("sphinxsim",))

    with patch("sphinxsim.services.cli_adapter.subprocess.run") as run_process:
        run_process.return_value = _completed(())
        adapter.run(".build-temp/adapter_case.json")

    command = run_process.call_args.args[0]
    assert command[-1] == str((tmp_path / ".build-temp" / "adapter_case.json").resolve())
    assert ".build-temp\\.build-temp" not in command[-1]


def test_nonzero_exit_is_returned_and_can_be_raised(tmp_path: Path) -> None:
    adapter = SPHinXsimCLIAdapter(cwd=tmp_path, command_prefix=("sphinxsim",))

    with patch("sphinxsim.services.cli_adapter.subprocess.run") as run_process:
        run_process.return_value = subprocess.CompletedProcess(
            (), 2, stdout="", stderr="invalid config"
        )
        result = adapter.validate(tmp_path / "bad.json")

    assert result.succeeded is False
    assert result.returncode == 2
    with pytest.raises(CLICommandError, match="invalid config"):
        result.require_success()


def test_timeout_is_a_structured_result(tmp_path: Path) -> None:
    adapter = SPHinXsimCLIAdapter(cwd=tmp_path, command_prefix=("sphinxsim",))

    with patch(
        "sphinxsim.services.cli_adapter.subprocess.run",
        side_effect=subprocess.TimeoutExpired(("sphinxsim", "run"), 1, output="partial"),
    ):
        result = adapter.run(tmp_path / "case.json", timeout=1)

    assert result.succeeded is False
    assert result.timed_out is True
    assert result.returncode is None
    assert result.stdout == "partial"


def test_generate_real_mock_provider_round_trip(build_temp_path: Path) -> None:
    """Exercise the real existing generate command without a network service."""
    project_root = Path(__file__).resolve().parents[1]
    output_path = build_temp_path / "adapter_generated.json"
    adapter = SPHinXsimCLIAdapter(
        cwd=project_root,
        environment={"SPHINXSIM_LLM_PROVIDER": "mock"},
        default_timeout=30,
    )

    result = adapter.generate("2D water dam break", output_path)

    result.require_success()
    assert output_path.exists()
    payload = json.loads(output_path.read_text())
    config = SimulationConfig.model_validate(payload)
    assert config.simulation_type.value == "fluid_dynamics"

"""APT network recovery must retain package requirements and propagate errors."""

import importlib.util
import json
from pathlib import Path
import sys

import pytest


spec = importlib.util.spec_from_file_location(
    "install_apt_packages",
    Path(__file__).parents[1] / ".github/scripts/install-apt-packages.py",
)
apt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(apt)


@pytest.mark.parametrize("mode,status,actions,delays", [
    ("success", 0, ["update", "install"], []),
    ("update-timeout", 0, ["update", "update", "install"], [5]),
    ("install-timeout", 0, ["update", "install", "update", "install"], [5]),
    ("persistent-timeout", 100, ["update"] * 3, [5, 10]),
    ("invalid-package", 100, ["update", "install"], []),
    ("invalid-signature", 100, ["update"], []),
    ("network-then-invalid-package", 100, ["update", "update", "install"], [5]),
])
def test_apt_installation_recovers_or_remains_failed(
    tmp_path, monkeypatch, mode, status, actions, delays,
):
    tool = tmp_path / "apt.py"
    tool.write_text('''import json
from pathlib import Path
import sys

root, mode = Path(sys.argv[1]), sys.argv[2]
arguments = sys.argv[3:]
action = "install" if "install" in arguments else "update"
log = root / "calls.json"
calls = json.loads(log.read_text()) if log.exists() else []
sources = next((a.split("=", 1)[1] for a in arguments
                if a.startswith("Dir::Etc::sourcelist=")), None)
parts = next((a.split("=", 1)[1] for a in arguments
              if a.startswith("Dir::Etc::sourceparts=")), None)
calls.append({"action": action, "arguments": arguments,
              "sources": Path(sources).read_text() if sources else None})
log.write_text(json.dumps(calls))
assert "Acquire::Retries=2" in arguments
assert "APT::Update::Error-Mode=any" in arguments
if sources:
    assert Path(parts).is_dir() and not list(Path(parts).iterdir())
if action == "install":
    assert arguments[arguments.index("install") + 1:] == ["-y", "xvfb", "libxcb-cursor0"]
network_failure = (
    mode == "persistent-timeout"
    or mode in {"update-timeout", "network-then-invalid-package"} and not sources
    or mode == "install-timeout" and action == "install" and not sources
)
if network_failure:
    print("E: Failed to fetch mirror+file:/etc/apt/apt-mirrors.txt/package.deb Could not connect to azure.archive.ubuntu.com:80, connection timed out")
    raise SystemExit(100)
if mode in {"invalid-package", "network-then-invalid-package"} and action == "install":
    print("E: Unable to locate package invalid-package")
    raise SystemExit(100)
if mode == "invalid-signature":
    print("E: The repository is not signed.")
    raise SystemExit(100)
''', encoding="utf-8")
    waits = []
    monkeypatch.setattr(apt.time, "sleep", waits.append)

    assert apt.install(
        ["xvfb", "libxcb-cursor0"],
        command=[sys.executable, str(tool), str(tmp_path), mode], codename="jammy",
    ) == status

    calls = json.loads((tmp_path / "calls.json").read_text())
    assert [call["action"] for call in calls] == actions
    assert waits == delays
    assert calls[0]["sources"] is None
    for call in calls:
        if call["sources"]:
            sources = call["sources"]
            assert "https://archive.ubuntu.com/ubuntu jammy-updates" in sources
            assert "https://security.ubuntu.com/ubuntu jammy-security" in sources
            assert "azure.archive.ubuntu.com" not in sources
            assert "trusted=yes" not in sources
            path = next(a.split("=", 1)[1] for a in call["arguments"]
                        if a.startswith("Dir::Etc::sourcelist="))
            assert not Path(path).exists()

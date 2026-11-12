"""CI must recover temporary downloads and still reject failed dependency builds."""

import importlib.util
from pathlib import Path
import sys

import pytest


spec = importlib.util.spec_from_file_location(
    "retry_vcpkg_download",
    Path(__file__).parents[1] / ".github/scripts/retry-vcpkg-download.py",
)
retry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retry)


@pytest.mark.parametrize("mode,exit_code,attempts,delays", [
    ("success", 0, 1, []),
    ("recover-504", 0, 2, [15]),
    ("recover-http2", 0, 2, [15]),
    ("persistent-504", 7, 3, [15, 30]),
    ("compiler", 7, 1, []),
    ("network-then-compiler", 7, 2, [15]),
])
def test_dependency_installation_recovers_or_remains_failed(tmp_path, monkeypatch, mode, exit_code, attempts, delays):
    tool = tmp_path / "vcpkg.py"
    tool.write_text('''import sys
from pathlib import Path
root, mode = Path(sys.argv[1]), sys.argv[2]
counter = root / "attempts"
n = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(n))
if mode == "success" or mode.startswith("recover-") and n > 1:
    (root / "installed").write_text("dependencies ready")
    raise SystemExit(0)
if mode == "compiler" or mode == "network-then-compiler" and n > 1:
    print("error: C++ compilation failed")
elif mode == "recover-http2":
    print("error: curl operation failed with error code 16 (Error in the HTTP2 framing layer).")
else:
    print("error: curl operation failed with response code 504.")
raise SystemExit(7)
''')
    waits = []
    monkeypatch.setattr(retry.time, "sleep", waits.append)
    result = retry.run([sys.executable, str(tool), str(tmp_path), mode])
    assert result == exit_code
    assert (tmp_path / "installed").exists() == (exit_code == 0)
    assert int((tmp_path / "attempts").read_text()) == attempts
    assert waits == delays

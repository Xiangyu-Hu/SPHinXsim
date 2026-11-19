"""Install CI packages, falling back from runner mirrors on download failures."""

from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
import time


DOWNLOAD_ERROR = re.compile(
    r"Failed to fetch|Failed to download|Could not connect|Unable to connect|"
    r"Temporary failure resolving|Could not resolve|Connection failed|"
    r"connection timed out|TLS connection was non-properly terminated|"
    r"(?:429|500|502|503|504)\s",
    re.IGNORECASE,
)


def invoke(command):
    download_failure = False
    with subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace",
    ) as process:
        for line in process.stdout:
            print(line, end="", flush=True)
            download_failure |= bool(DOWNLOAD_ERROR.search(line))
        return process.wait(), download_failure


def install(packages, *, command=("sudo", "apt-get"), codename=None):
    options = [
        "-o", "Acquire::Retries=2",
        "-o", "Acquire::http::Timeout=20",
        "-o", "Acquire::https::Timeout=20",
        "-o", "APT::Update::Error-Mode=any",
    ]
    with tempfile.TemporaryDirectory(prefix="sphinxsim-apt-") as directory:
        for attempt in range(1, 4):
            status, download_failure = invoke([*command, *options, "update"])
            if status == 0:
                status, download_failure = invoke(
                    [*command, *options, "install", "-y", *packages]
                )
            if status == 0 or not download_failure or attempt == 3:
                return status
            if attempt == 1:
                release = codename or platform.freedesktop_os_release()["VERSION_CODENAME"]
                if not re.fullmatch(r"[a-z]+", release):
                    raise ValueError("Invalid Ubuntu release codename")
                sources = Path(directory) / "sources.list"
                sources.write_text(
                    f"deb https://archive.ubuntu.com/ubuntu {release} main restricted universe multiverse\n"
                    f"deb https://archive.ubuntu.com/ubuntu {release}-updates main restricted universe multiverse\n"
                    f"deb https://archive.ubuntu.com/ubuntu {release}-backports main restricted universe multiverse\n"
                    f"deb https://security.ubuntu.com/ubuntu {release}-security main restricted universe multiverse\n",
                    encoding="utf-8",
                )
                parts = Path(directory) / "sources.list.d"
                parts.mkdir()
                options += [
                    "-o", f"Dir::Etc::sourcelist={sources}",
                    "-o", f"Dir::Etc::sourceparts={parts}",
                ]
                print("APT download failed; switching to official Ubuntu HTTPS sources.", flush=True)
            delay = attempt * 5
            print(f"Retrying package installation in {delay} seconds.", flush=True)
            time.sleep(delay)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Usage: install-apt-packages.py <package> [packages...]")
    raise SystemExit(install(sys.argv[1:]))

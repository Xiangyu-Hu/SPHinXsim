"""Retry temporary vcpkg download failures without masking build failures."""

import re
import subprocess
import sys
import time


TRANSIENT_DOWNLOAD_ERROR = re.compile(
    r"curl operation failed with (?:"
    r"response code (?:429|500|502|503|504)\."
    r"|error code 16 \(Error in the HTTP2 framing layer\))"
)


def run(command):
    for attempt in range(1, 4):
        transient_download_failure = False
        with subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace",
        ) as process:
            for line in process.stdout:
                print(line, end="", flush=True)
                transient_download_failure |= bool(TRANSIENT_DOWNLOAD_ERROR.search(line))
            status = process.wait()
        if status == 0 or not transient_download_failure or attempt == 3:
            return status
        delay = attempt * 15
        print(f"Transient dependency download failure; retrying in {delay} seconds.", flush=True)
        time.sleep(delay)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Usage: retry-vcpkg-download.py <vcpkg> install [arguments...]")
    raise SystemExit(run(sys.argv[1:]))

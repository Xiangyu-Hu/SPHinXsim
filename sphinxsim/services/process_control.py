"""Process identity checks and cross-process ownership for local workers."""
from __future__ import annotations
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import psutil
import time


def identity(pid: int) -> dict:
    return {"pid": pid, "created": psutil.Process(pid).create_time()}


def alive(record: dict | None) -> bool:
    if not record or not record.get("pid"):
        return False
    try:
        process = psutil.Process(record["pid"])
        return process.create_time() == record["created"] and process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError):
        return False


def terminate_tree(record: dict) -> None:
    """Kill only a process whose PID and creation time match our saved child."""
    if not alive(record):
        return
    parent = psutil.Process(record["pid"])
    processes = parent.children(recursive=True) + [parent]
    for process in reversed(processes):
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass
    _, remaining = psutil.wait_procs(processes, timeout=5)
    if remaining:
        raise RuntimeError("Unable to stop the owned solver process tree")


@contextmanager
def workspace_lock(path: Path, *, blocking=False):
    """OS-owned lock released automatically if its worker dies."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if not blocking or exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise RuntimeError("An Agent worker is already running in this workspace") from exc
                    time.sleep(.05)
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except OSError as exc:
                raise RuntimeError("An Agent worker is already running in this workspace") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)

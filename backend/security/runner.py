"""Safe subprocess execution for security scanners (spec §39).

Scanners run as argument arrays (never through a shell), with a timeout, in
the session's repository directory, writing their report to a file inside the
session whose size is checked before it is read. The repository is untrusted
input: nothing from it is ever interpolated into a command line.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_LOG_TAIL = 400  # characters of stderr kept for diagnostics


class ToolNotFoundError(Exception):
    """The scanner executable is not installed / not on PATH."""


class ToolTimeoutError(Exception):
    """The scanner exceeded the configured timeout."""


class ToolOutputTooLargeError(Exception):
    """The scanner's report exceeds the configured size limit."""


@dataclass
class ToolResult:
    returncode: int
    stdout: str  # truncated
    stderr: str  # truncated
    duration_seconds: float


def find_tool(name: str, configured_path: str | None) -> str:
    """Absolute path of a scanner executable, or raise ToolNotFoundError."""
    candidate = configured_path or name
    resolved = shutil.which(candidate)
    if resolved is None and configured_path and Path(configured_path).is_file():
        resolved = str(Path(configured_path).resolve())
    if resolved is None:
        raise ToolNotFoundError(name)
    return resolved


def run_tool(args: list[str], cwd: Path, timeout_seconds: float, extra_env: dict[str, str] | None = None) -> ToolResult:
    """Run one scanner invocation. Raises ToolTimeoutError on timeout."""
    import time

    env = dict(os.environ)
    env.update(extra_env or {})
    started = time.monotonic()
    try:
        completed = subprocess.run(  # noqa: S603 - argument array, no shell, untrusted repo never in args
            args,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout_seconds,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolTimeoutError(f"{Path(args[0]).name} timed out after {timeout_seconds:.0f}s") from exc
    duration = time.monotonic() - started
    return ToolResult(
        returncode=completed.returncode,
        stdout=_decode_tail(completed.stdout),
        stderr=_decode_tail(completed.stderr),
        duration_seconds=duration,
    )


def read_report(path: Path, limit_bytes: int) -> str:
    """Read a scanner report file, refusing anything over the size limit."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise FileNotFoundError(f"Scanner produced no report at {path.name}") from exc
    if size > limit_bytes:
        raise ToolOutputTooLargeError(f"Scanner report is {size // (1024 * 1024)} MB, over the limit")
    return path.read_text(encoding="utf-8", errors="replace")


def _decode_tail(data: bytes | None) -> str:
    if not data:
        return ""
    text = data.decode("utf-8", errors="replace")
    return text[-_LOG_TAIL:]

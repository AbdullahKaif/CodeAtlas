"""Outbound notifications."""
import subprocess


def notify_owner(owner: str, message: str) -> int:
    """DELIBERATE DEFECT: builds a shell command from user-controlled strings."""
    command = f"notify-send '{owner}' '{message}'"
    return subprocess.call(command, shell=True)

"""Helper functions. Contains DELIBERATE vulnerabilities for scanner testing."""
import pickle
import subprocess

import yaml


def run_report(report_name: str) -> int:
    """VULNERABLE: builds a shell command from its argument."""
    return subprocess.call("generate-report " + report_name, shell=True)


def load_settings(document: str) -> dict:
    """VULNERABLE: yaml.load without a safe loader."""
    return yaml.load(document)


def restore_session(blob: bytes):
    """VULNERABLE: unpickles data of unknown origin."""
    return pickle.loads(blob)


def checksum(data: bytes) -> str:
    """Fine for a checksum; hashlib.md5 is flagged only as a weak hash."""
    import hashlib

    return hashlib.md5(data).hexdigest()

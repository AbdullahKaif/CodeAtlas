"""Session data removal. Everything CodeAtlas stores for a session lives under one directory."""
from __future__ import annotations

import logging
import os
import shutil
import stat
import time

from backend.config import settings
from backend.repository.clone import SESSION_ID_RE

logger = logging.getLogger(__name__)

# On Windows, a directory can stay briefly locked after a git subprocess exits
# (or while antivirus touches new files), so deletion retries.
MAX_DELETE_ATTEMPTS = 4
RETRY_DELAY_SECONDS = 0.4


def _force_remove_readonly(func, path, exc_info) -> None:
    """rmtree onerror hook: clear read-only bits (git objects on Windows) and retry.

    A path that vanished mid-delete (concurrent deletion) is already the outcome
    we want, so ENOENT is ignored.
    """
    if isinstance(exc_info[1], FileNotFoundError):
        return
    os.chmod(path, stat.S_IWRITE)
    func(path)


def delete_session(session_id: str) -> bool:
    """Delete all temporary data for a session. Returns True if data existed.

    Raises ValueError for malformed session ids (also guards against path
    traversal) and OSError if the data cannot be removed after retries.
    """
    if not isinstance(session_id, str) or not SESSION_ID_RE.match(session_id):
        raise ValueError("Invalid session id.")
    session_dir = settings.session_dir(session_id)
    if not session_dir.is_dir():
        return False

    for attempt in range(1, MAX_DELETE_ATTEMPTS + 1):
        try:
            shutil.rmtree(session_dir, onerror=_force_remove_readonly)
            return True
        except FileNotFoundError:
            return True  # deleted concurrently - the goal is achieved
        except OSError:
            if attempt == MAX_DELETE_ATTEMPTS:
                raise
            logger.info("Session %s busy, retrying deletion (%d)", session_id, attempt)
            time.sleep(RETRY_DELAY_SECONDS * attempt)
    return True

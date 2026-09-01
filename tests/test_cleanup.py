"""Tests for session deletion (privacy)."""
from __future__ import annotations

import os
import stat

import pytest

from backend.privacy.cleanup import delete_session
from backend.repository.clone import create_session


class TestDeleteSession:
    def test_deletes_all_session_data(self, temp_sessions):
        session_id, repo_dir = create_session()
        (repo_dir / "file.py").write_text("x = 1\n", encoding="utf-8")
        assert delete_session(session_id) is True
        assert not (temp_sessions / f"session_{session_id}").exists()

    def test_deletes_readonly_files(self, temp_sessions):
        """Git object files are read-only on Windows; deletion must still work."""
        session_id, repo_dir = create_session()
        locked = repo_dir / "objects" / "pack"
        locked.mkdir(parents=True)
        target = locked / "pack-abc.idx"
        target.write_bytes(b"data")
        os.chmod(target, stat.S_IREAD)
        assert delete_session(session_id) is True
        assert not (temp_sessions / f"session_{session_id}").exists()

    def test_unknown_session_returns_false(self, temp_sessions):
        assert delete_session("0123456789ab") is False

    @pytest.mark.parametrize("bad_id", ["", "abc", "../../etc", "session_x", "ABCDEF123456", None])
    def test_malformed_ids_rejected(self, temp_sessions, bad_id):
        with pytest.raises(ValueError):
            delete_session(bad_id)


class TestDeleteRetries:
    def test_locked_file_raises_after_retries(self, temp_sessions, monkeypatch):
        """An open file handle (Windows lock) should surface as OSError, fast."""
        import backend.privacy.cleanup as cleanup_module

        monkeypatch.setattr(cleanup_module, "RETRY_DELAY_SECONDS", 0.01)
        session_id, repo_dir = create_session()
        target = repo_dir / "locked.bin"
        target.write_bytes(b"data")
        with open(target, "rb"):
            with pytest.raises(OSError):
                delete_session(session_id)
        # After the handle is released, deletion succeeds.
        assert delete_session(session_id) is True


class TestConcurrentDeletes:
    def test_dir_vanishing_mid_delete_is_success(self, temp_sessions, monkeypatch):
        """A concurrent delete (FileNotFoundError from rmtree) is not an error."""
        import shutil as shutil_module

        import backend.privacy.cleanup as cleanup_module

        session_id, _ = create_session()

        def _vanished(path, onerror=None):
            raise FileNotFoundError(path)

        monkeypatch.setattr(cleanup_module.shutil, "rmtree", _vanished)
        assert delete_session(session_id) is True

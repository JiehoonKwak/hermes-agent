"""Permission hardening must retain live SQLite locks on macOS."""

import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys

import pytest

from hermes_state import _secure_state_db_files


@pytest.mark.macos_only
@pytest.mark.parametrize("journal_mode", ["wal", "delete"])
def test_permission_hardening_preserves_live_sqlite_write_lock(tmp_path, journal_mode):
    path = tmp_path / "state.db"
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA journal_mode={journal_mode}")
    conn.execute("CREATE TABLE evidence(value)")
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    probe = """
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1], timeout=0)
try:
    conn.execute('BEGIN IMMEDIATE')
except sqlite3.OperationalError as exc:
    assert 'locked' in str(exc), str(exc)
    print('blocked')
else:
    conn.rollback()
    print('admitted')
finally:
    conn.close()
"""

    def competing_writer():
        return subprocess.check_output(
            [sys.executable, "-c", probe, str(path)], text=True, timeout=10).strip()

    try:
        assert competing_writer() == "blocked"
        # Both constructor call sites can run while another handle holds locks.
        _secure_state_db_files(path, create_main=True)
        _secure_state_db_files(path)
        assert competing_writer() == "blocked"
    finally:
        conn.rollback()
        conn.close()


@pytest.mark.macos_only
def test_permission_hardening_keeps_private_creation_and_rejects_symlinks(tmp_path):
    path = tmp_path / "state.db"
    old_umask = os.umask(0)
    try:
        _secure_state_db_files(path, create_main=True)
    finally:
        os.umask(old_umask)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    files = [path, Path(str(path) + "-wal"), Path(str(path) + "-shm")]
    for file in files:
        file.touch()
        file.chmod(0o666)
    _secure_state_db_files(path, create_main=True)
    assert all(stat.S_IMODE(file.stat().st_mode) == 0o600 for file in files)

    target = tmp_path / "unrelated"
    target.write_text("unchanged", encoding="utf-8")
    target.chmod(0o644)
    for file in files:
        file.unlink()
        file.symlink_to(target)
        with pytest.raises(OSError):
            _secure_state_db_files(path, create_main=True)
        assert target.read_text(encoding="utf-8") == "unchanged"
        assert stat.S_IMODE(target.stat().st_mode) == 0o644
        file.unlink()
        file.touch(mode=0o600)

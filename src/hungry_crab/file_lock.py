"""Small crash-released OS locks for maw-owned state files."""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Iterator
from pathlib import Path

from .errors import CrabError
from .fs import is_link_stat


def require_real_state_path(path: Path) -> None:
    """Callers anchor their owner-selected root before adding managed children."""
    for candidate in (*reversed(path.parents), path):
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise CrabError("state path cannot be inspected safely") from exc
        if is_link_stat(metadata):
            raise CrabError(f"state must not traverse a symlink or junction: {candidate}")


@contextlib.contextmanager
def state_lock(path: Path) -> Iterator[None]:
    """Serialize writers; readers still verify the snapshot they intend to replace."""
    lock = path.with_suffix(".lock")
    require_real_state_path(path)
    require_real_state_path(lock)
    path.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+b") as stream:
        require_real_state_path(path)
        require_real_state_path(lock)
        if os.fstat(stream.fileno()).st_nlink > 1:
            raise CrabError("state lock must not be hardlinked")
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise CrabError("another process is updating this state; reload and retry") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

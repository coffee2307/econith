"""Single-writer fence for a finite SIMULATION forward research evidence root.

An OS O_EXCL lock is intentionally NOT silently stolen. After an OS hard exit
the orphan marker requires inspection; a subsequent recovery cannot claim
another producer has stopped just because a timeout elapsed.
This is a local host process-ownership gate, not a distributed lease or proof
that a malicious process cannot touch these files.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class ForwardOwnershipDenied(RuntimeError):
    """A producer/recovery could conflict or the evidence root is unsafe."""


@contextmanager
def exclusive_forward_owner(root: Path, *, role: str) -> Iterator[None]:
    if (
        not isinstance(root, Path)
        or not root.is_absolute()
        or not root.is_dir()
        or root != root.resolve()
        or root.is_symlink()
        or role not in ("CAPTURE", "STAGED_RECOVERY")
    ):
        raise ForwardOwnershipDenied("invalid isolated forward owner scope")
    lock = root / ".forward_producer.lock"
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ForwardOwnershipDenied(
            "active or orphaned forward producer ownership requires operator review"
        ) from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(f"SIMULATION_{role}\n")
            out.flush()
            os.fsync(out.fileno())
        yield
    finally:
        # A real process kill leaves the marker; recovery MUST NOT steal it.
        lock.unlink(missing_ok=True)

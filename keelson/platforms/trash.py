"""Deleting means moving to the Recycle Bin (Trash on Linux and macOS).

Keelson never deletes a file for good. ``delete_files`` and ``/undo`` both go
through :func:`move_to_trash`, so a deletion can always be taken back from the
Recycle Bin even when it is outside what a checkpoint covers (ignored files,
files outside the project).

``send2trash`` does the platform work: the shell's file operation API on
Windows, NSFileManager on macOS, the freedesktop.org trash on Linux. When it is
missing or the move fails (some network drives have no Recycle Bin), the file
is left where it is and the reason is returned — never a silent hard delete.
"""

from __future__ import annotations

from pathlib import Path


class TrashUnavailable(RuntimeError):
    pass


def available() -> bool:
    try:
        import send2trash  # noqa: F401
    except ImportError:
        return False
    return True


def move_to_trash(path: Path) -> None:
    """Move one file or folder to the Recycle Bin, or raise with the reason."""
    try:
        from send2trash import send2trash
    except ImportError as exc:
        raise TrashUnavailable("send2trash is not installed (pip install send2trash)") from exc
    # send2trash wants an absolute path; on Windows a long path must not be relative either.
    send2trash(str(path.absolute()))


def bin_name() -> str:
    from . import IS_WINDOWS

    return "the Recycle Bin" if IS_WINDOWS else "the Trash"

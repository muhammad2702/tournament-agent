"""Read saved Windows-relative paths on both Windows and Linux.

Run commands from the repository root. Historical data files retain their
original bytes and hashes; only path interpretation changes on read.
"""

from pathlib import Path


def saved_path(value: str) -> Path:
    return Path(value.replace("\\", "/"))

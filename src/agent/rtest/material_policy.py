"""Material request policy helpers for RustTestAgent.

This module keeps range normalization and whole-file escalation out of the
main repair loop.  The rules are intentionally conservative: small source
files can be promoted to whole-file context when the model is repeatedly
asking for related ranges, while large files still use focused snippets.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple


SMALL_FILE_WHOLE_FILE_CHARS = 80 * 1024
LARGE_RANGE_LINE_COUNT = 300


@dataclass(frozen=True)
class FileSlice:
    content: str
    start_line: int
    end_line: int
    requested_start_line: Optional[int] = None
    requested_end_line: Optional[int] = None
    total_lines: int = 0

    @property
    def range_changed(self) -> bool:
        return (
            isinstance(self.requested_start_line, int)
            and isinstance(self.requested_end_line, int)
            and (self.start_line, self.end_line)
            != (self.requested_start_line, self.requested_end_line)
        )


def safe_file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 1 << 60


def should_upgrade_line_range_to_whole_file(
    path: Path,
    *,
    existing_material_count: int,
    start_line: Optional[int],
    end_line: Optional[int],
    max_chars: int = SMALL_FILE_WHOLE_FILE_CHARS,
) -> bool:
    """Return whether a line-range request should become a whole-file read."""

    if not path.is_file() or safe_file_size(path) > max_chars:
        return False
    requested_lines = 0
    if isinstance(start_line, int) and isinstance(end_line, int):
        requested_lines = abs(end_line - start_line) + 1
    return existing_material_count > 0 or requested_lines >= LARGE_RANGE_LINE_COUNT


def normalize_requested_range(
    line_count: int,
    start_line: int,
    end_line: int,
) -> Optional[Tuple[int, int]]:
    """Clamp a requested line range to available file lines.

    If the requested start is beyond EOF, return the final line instead of
    rejecting the request.  That gives the model a concrete tail anchor and
    makes the feedback explicit.
    """

    if line_count <= 0:
        return None
    start = int(start_line)
    end = int(end_line)
    if end < start:
        start, end = end, start
    start = max(1, start)
    end = max(start, end)
    if start > line_count:
        return line_count, line_count
    return start, min(line_count, end)


def read_text_file_slice(
    path: Path,
    *,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
) -> Optional[FileSlice]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    if start_line is None or end_line is None:
        return FileSlice(
            content=text,
            start_line=1,
            end_line=max(1, len(text.splitlines())),
            total_lines=len(text.splitlines()),
        )

    lines = text.splitlines()
    normalized = normalize_requested_range(len(lines), int(start_line), int(end_line))
    if normalized is None:
        return None
    actual_start, actual_end = normalized
    return FileSlice(
        content="\n".join(lines[actual_start - 1:actual_end]) + "\n",
        start_line=actual_start,
        end_line=actual_end,
        requested_start_line=int(start_line),
        requested_end_line=int(end_line),
        total_lines=len(lines),
    )

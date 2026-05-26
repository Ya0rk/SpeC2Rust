"""Build and validate C projects used by RustTestAgent and instrumentation.

The supported dataset contract is intentionally narrow:
- the C project root contains a Makefile/makefile;
- `make` builds the executable into the project root;
- the C project root contains a `test/` directory.
"""

from __future__ import annotations

import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


_OUTPUT_TAIL_CHARS = 12000


@dataclass
class CBuildResult:
    ok: bool
    project_path: str
    binary_path: str = ""
    stdout: str = ""
    stderr: str = ""
    error: str = ""
    test_dir: str = ""
    makefile_path: str = ""


class CProjectBuilder:
    """Validate, clean, build, and locate the root executable for a C project."""

    def __init__(self, timeout_seconds: int = 600):
        self.timeout_seconds = timeout_seconds

    def validate(
        self, c_project_path: str, expected_bin_name: Optional[str] = None
    ) -> CBuildResult:
        project = Path(c_project_path).resolve()
        base = CBuildResult(ok=False, project_path=str(project))
        if not project.is_dir():
            base.error = f"C project directory does not exist: {project}"
            return base

        makefile = self._find_makefile(project)
        if not makefile:
            base.error = f"C project root must contain Makefile or makefile: {project}"
            return base
        base.makefile_path = str(makefile)

        test_dir = project / "test"
        if not test_dir.is_dir():
            base.error = f"C project root must contain a test/ directory: {project}"
            return base
        base.test_dir = str(test_dir)

        binary = self.locate_binary(str(project), expected_bin_name=expected_bin_name)
        base.binary_path = binary
        base.ok = True
        return base

    def clean_and_build(
        self, c_project_path: str, expected_bin_name: Optional[str] = None
    ) -> CBuildResult:
        project = Path(c_project_path).resolve()
        validation = self.validate(str(project), expected_bin_name=expected_bin_name)
        if not validation.ok:
            return validation

        clean = self._run_make(project, "clean")
        build = self._run_make(project)
        stdout = _tail((clean.stdout or "") + (build.stdout or ""))
        stderr = _tail((clean.stderr or "") + (build.stderr or ""))

        if build.returncode != 0:
            return CBuildResult(
                ok=False,
                project_path=str(project),
                stdout=stdout,
                stderr=stderr,
                error=f"`make` failed in C project: {project}",
                test_dir=validation.test_dir,
                makefile_path=validation.makefile_path,
            )

        binary = self.locate_binary(str(project), expected_bin_name=expected_bin_name)
        if not binary:
            return CBuildResult(
                ok=False,
                project_path=str(project),
                stdout=stdout,
                stderr=stderr,
                error=(
                    "`make` completed, but no root executable could be located. "
                    "Pass the expected binary name if the project builds multiple root executables."
                ),
                test_dir=validation.test_dir,
                makefile_path=validation.makefile_path,
            )

        return CBuildResult(
            ok=True,
            project_path=str(project),
            binary_path=binary,
            stdout=stdout,
            stderr=stderr,
            test_dir=validation.test_dir,
            makefile_path=validation.makefile_path,
        )

    def locate_binary(
        self, c_project_path: str, expected_bin_name: Optional[str] = None
    ) -> str:
        project = Path(c_project_path).resolve()
        if not project.is_dir():
            return ""

        expected_names: List[str] = []
        for raw in (expected_bin_name, project.name):
            name = (raw or "").strip()
            if name and name not in expected_names:
                expected_names.append(name)
        for name in list(expected_names):
            exe_name = f"{name}.exe"
            if exe_name not in expected_names:
                expected_names.append(exe_name)

        for name in expected_names:
            candidate = project / name
            if candidate.is_file() and _is_executable_candidate(candidate):
                return str(candidate)

        candidates = [
            path
            for path in sorted(project.iterdir())
            if path.is_file() and _is_executable_candidate(path)
            and path.name not in expected_names
        ]
        if len(candidates) == 1:
            return str(candidates[0])
        return ""

    @staticmethod
    def _find_makefile(project: Path) -> Optional[Path]:
        for name in ("Makefile", "makefile"):
            path = project / name
            if path.is_file():
                return path
        return None

    def _run_make(self, project: Path, target: str = "") -> subprocess.CompletedProcess:
        command = ["make"]
        if target:
            command.append(target)
        try:
            return subprocess.run(
                command,
                cwd=str(project),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=self.timeout_seconds,
            )
        except FileNotFoundError as exc:
            return subprocess.CompletedProcess(command, 127, "", f"make not found: {exc}")
        except subprocess.TimeoutExpired as exc:
            stdout = _decode_timeout_stream(exc.stdout)
            stderr = _decode_timeout_stream(exc.stderr)
            return subprocess.CompletedProcess(
                command,
                124,
                stdout,
                stderr + f"\nmake {' '.join(command[1:])} timed out",
            )
        except OSError as exc:
            return subprocess.CompletedProcess(command, 126, "", str(exc))


def _is_executable_candidate(path: Path) -> bool:
    name = path.name
    lower = name.lower()
    if lower in {"makefile", "gnumakefile"}:
        return False
    if lower.startswith("."):
        return False
    blocked_suffixes = {
        ".c",
        ".h",
        ".o",
        ".a",
        ".so",
        ".dylib",
        ".dll",
        ".txt",
        ".md",
        ".json",
        ".log",
        ".sh",
        ".mk",
    }
    if path.suffix.lower() in blocked_suffixes:
        return False
    if os.name == "nt":
        return lower.endswith(".exe") or _looks_like_native_executable(path)
    
    try:
        mode = path.stat().st_mode
    except OSError:
        return False
    return bool(mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))


def _looks_like_native_executable(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            magic = f.read(4)
    except OSError:
        return False
    return magic.startswith(b"\x7fELF") or magic.startswith(b"MZ") or magic in {
        b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe",
        b"\xfe\xed\xfa\xcf",
        b"\xfe\xed\xfa\xce",
    }


def _decode_timeout_stream(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return str(value)


def _tail(text: str) -> str:
    return (text or "")[-_OUTPUT_TAIL_CHARS:]

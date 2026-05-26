"""Debugger backends for runtime evidence collection."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class BreakpointSpec:
    file: str
    line: int


@dataclass
class StaticProbeSpec:
    probe_id: str
    target: str
    file: str
    line: int
    expressions: List[str] = field(default_factory=list)
    label: str = ""


@dataclass
class InstrumentationRequest:
    target: str = "rust"
    breakpoints: List[BreakpointSpec] = field(default_factory=list)
    collect_stack: bool = True
    collect_locals: bool = True
    watch_expressions: List[str] = field(default_factory=list)
    program_args: List[str] = field(default_factory=list)


@dataclass
class LldbRunResult:
    ok: bool
    returncode: int
    command: List[str]
    script_path: str
    stdout: str
    stderr: str
    frames: List[Dict[str, Any]] = field(default_factory=list)
    locals: Dict[str, Any] = field(default_factory=dict)
    watch_values: Dict[str, str] = field(default_factory=dict)


class LldbBackend:
    def __init__(self, debugger_executable: Optional[str] = None, timeout_seconds: int = 60):
        self.debugger_executable = debugger_executable
        self.timeout_seconds = timeout_seconds

    def run(
        self,
        program: str,
        request: InstrumentationRequest,
        *,
        log_dir: Path,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> LldbRunResult:
        debugger = self._resolve_debugger()
        log_dir.mkdir(parents=True, exist_ok=True)
        script_path = log_dir / "lldb.cmd"
        script_path.write_text(self.build_script(program, request), encoding="utf-8")

        command = [debugger, "--batch", "-s", str(script_path)]
        proc = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=self.timeout_seconds,
            check=False,
        )
        stdout = proc.stdout.decode("utf-8", errors="ignore")
        stderr = proc.stderr.decode("utf-8", errors="ignore")
        parsed = self.parse_output(stdout)
        return LldbRunResult(
            ok=proc.returncode == 0,
            returncode=proc.returncode,
            command=command,
            script_path=str(script_path),
            stdout=stdout,
            stderr=stderr,
            frames=parsed["frames"],
            locals=parsed["locals"],
            watch_values=parsed["watch_values"],
        )

    def build_script(self, program: str, request: InstrumentationRequest) -> str:
        lines: List[str] = [
            "settings set auto-confirm true",
            "settings set stop-disassembly-count 0",
            f"target create {json.dumps(program)}",
        ]
        for bp in request.breakpoints:
            lines.append(f"breakpoint set --file {bp.file} --line {bp.line}")
        if request.program_args:
            lines.append(
                "settings set target.run-args "
                + " ".join(shlex.quote(arg) for arg in request.program_args)
            )
        lines.append("run")
        if request.collect_locals:
            lines.append("echo __CGR_LOCALS_BEGIN__")
            lines.append("frame variable")
            lines.append("echo __CGR_LOCALS_END__")
        if request.collect_stack:
            lines.append("echo __CGR_BACKTRACE_BEGIN__")
            lines.append("bt")
            lines.append("echo __CGR_BACKTRACE_END__")
        for index, expr in enumerate(request.watch_expressions):
            lines.append(f"echo __CGR_WATCH_{index}_BEGIN__")
            lines.append(f"expression -- {expr}")
            lines.append(f"echo __CGR_WATCH_{index}_END__")
        lines.append("process kill")
        lines.append("quit")
        return "\n".join(lines) + "\n"

    def parse_output(self, output: str) -> Dict[str, Any]:
        frames: List[Dict[str, Any]] = []
        locals_map: Dict[str, Any] = {}
        watch_values: Dict[str, str] = {}

        sections = self._split_sections(output)
        for name, body in sections.items():
            if name == "backtrace":
                frames.extend(self._parse_backtrace(body))
            elif name == "locals":
                locals_map.update(self._parse_locals(body))
            elif name.startswith("watch_"):
                watch_values[name.removeprefix("watch_")] = self._extract_watch_value(body)

        return {
            "frames": frames,
            "locals": locals_map,
            "watch_values": watch_values,
        }

    def _resolve_debugger(self) -> str:
        if self.debugger_executable:
            return self.debugger_executable
        for candidate in ("rust-lldb", "lldb"):
            resolved = shutil.which(candidate)
            if resolved:
                return resolved
        raise FileNotFoundError("Neither rust-lldb nor lldb was found on PATH")

    @staticmethod
    def _split_sections(output: str) -> Dict[str, str]:
        section_map: Dict[str, List[str]] = {}
        current_name: Optional[str] = None
        for raw_line in (output or "").splitlines():
            line = raw_line.rstrip()
            if line == "__CGR_LOCALS_BEGIN__":
                current_name = "locals"
                section_map.setdefault(current_name, [])
                continue
            if line == "__CGR_LOCALS_END__":
                current_name = None
                continue
            if line == "__CGR_BACKTRACE_BEGIN__":
                current_name = "backtrace"
                section_map.setdefault(current_name, [])
                continue
            if line == "__CGR_BACKTRACE_END__":
                current_name = None
                continue
            watch_begin = re.match(r"^__CGR_WATCH_(\d+)_BEGIN__$", line)
            if watch_begin:
                current_name = f"watch_{watch_begin.group(1)}"
                section_map.setdefault(current_name, [])
                continue
            watch_end = re.match(r"^__CGR_WATCH_(\d+)_END__$", line)
            if watch_end:
                current_name = None
                continue
            if current_name is not None:
                section_map[current_name].append(line)
        return {name: "\n".join(lines).strip() for name, lines in section_map.items()}

    @staticmethod
    def _parse_backtrace(body: str) -> List[Dict[str, Any]]:
        frames: List[Dict[str, Any]] = []
        for line in (body or "").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            match = re.match(r"^frame #(?P<index>\d+): (?P<rest>.*)$", stripped)
            if match:
                frame = {"index": int(match.group("index")), "text": stripped}
                rest = match.group("rest")
                func_match = re.search(r"`(?P<function>[^`]+?)(?: at | \+ |\s*$)", rest)
                if func_match:
                    frame["function"] = func_match.group("function").strip()
                file_match = re.search(r" at (?P<file>.+):(?P<line>\d+)$", rest)
                if file_match:
                    frame["file"] = file_match.group("file")
                    frame["line"] = int(file_match.group("line"))
                frames.append(frame)
            else:
                frames.append({"text": stripped})
        return frames

    @staticmethod
    def _parse_locals(body: str) -> Dict[str, Any]:
        locals_map: Dict[str, Any] = {}
        for line in (body or "").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            match = re.match(r"^(?:\([^)]+\)\s*)?(?P<name>[A-Za-z_][A-Za-z0-9_:]*)\s*=\s*(?P<value>.*)$", stripped)
            if match:
                locals_map[match.group("name")] = match.group("value")
            else:
                locals_map.setdefault("_raw", []).append(stripped)
        return locals_map

    @staticmethod
    def _extract_watch_value(body: str) -> str:
        lines = [line.strip() for line in (body or "").splitlines() if line.strip()]
        if not lines:
            return ""
        for line in lines:
            match = re.match(r"^(?:\([^)]+\)\s*)?(?:\$\d+\s*=\s*)?(?P<value>.*)$", line)
            if match and match.group("value"):
                return match.group("value")
        return lines[-1]


class DapBackend:
    def __init__(self, adapter_type: str = "lldb-dap"):
        self.adapter_type = adapter_type

    def build_launch_payload(
        self,
        program: str,
        request: InstrumentationRequest,
        *,
        cwd: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "name": "cgr-runtime-debug",
            "type": self.adapter_type,
            "request": "launch",
            "program": program,
            "stopOnEntry": False,
            "breakpoints": [{"file": bp.file, "line": bp.line} for bp in request.breakpoints],
            "collectStack": request.collect_stack,
            "collectLocals": request.collect_locals,
            "watchExpressions": list(request.watch_expressions),
            "args": list(request.program_args),
        }
        if cwd:
            payload["cwd"] = cwd
        return payload

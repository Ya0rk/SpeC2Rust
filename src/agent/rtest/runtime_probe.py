"""Runtime probe execution and evidence loading for rtest."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional

from .c_project_builder import CProjectBuilder
from .debug_backends import DapBackend, LldbBackend, StaticProbeSpec
from .log_agent import LogAgent
from .models import TestCaseResult


class RuntimeProbeService:
    """Manage runtime evidence files and debugger-backed probes."""

    def __init__(
        self,
        locate_release_binary: Callable[[str, str], Optional[str]],
        *,
        test_timeout_seconds: int,
        build_timeout_seconds: int = 600,
    ):
        self._locate_release_binary = locate_release_binary
        self._test_timeout_seconds = test_timeout_seconds
        self._build_timeout_seconds = build_timeout_seconds
        self._c_project_path = ""
        self._c_binary_path = ""

    def configure_c_target(self, c_project_path: str, c_binary_path: str) -> None:
        self._c_project_path = c_project_path
        self._c_binary_path = c_binary_path

    @staticmethod
    def read_runtime_evidence(failing_case: TestCaseResult) -> Dict[str, object]:
        run_dir = RuntimeProbeService._run_dir_for_case(failing_case)
        runtime_path = run_dir / ".cgr_logs" / "runtime.json"
        payload: Dict[str, object] = {}
        if runtime_path.is_file():
            try:
                raw = runtime_path.read_text(encoding="utf-8", errors="ignore")
                parsed = json.loads(raw)
            except (OSError, json.JSONDecodeError):
                parsed = {}
            if isinstance(parsed, dict):
                payload = parsed
        probe_dir = run_dir / ".cgr_logs"
        if probe_dir.is_dir():
            try:
                probe_paths = [
                    path
                    for path in probe_dir.glob("debug_probe_*.json")
                    if ".dap." not in path.name
                ]
                probe_paths.sort(key=RuntimeProbeService._debug_probe_path_sort_key)
            except OSError:
                probe_paths = []
            summaries = []
            for path in probe_paths[-4:]:
                try:
                    parsed = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
                except (OSError, json.JSONDecodeError):
                    parsed = {}
                if isinstance(parsed, dict):
                    summaries.append(parsed)
            if summaries:
                payload = dict(payload)
                payload["debug_probes"] = summaries
                payload["debug_probe"] = summaries[-1]
            static_paths = sorted(
                probe_dir.glob("static_probe_*.json"),
                key=RuntimeProbeService._static_probe_path_sort_key,
            )
            if static_paths:
                try:
                    static_summary = json.loads(
                        static_paths[-1].read_text(encoding="utf-8", errors="ignore")
                    )
                except (OSError, json.JSONDecodeError):
                    static_summary = {}
                if isinstance(static_summary, dict):
                    payload = dict(payload)
                    payload["static_probe"] = static_summary
        return payload

    def execute_debug_probe(
        self,
        *,
        rust_project_path: str,
        bin_name: str,
        failing_case: TestCaseResult,
        probe_spec: Dict[str, object],
        attempt: int,
    ) -> bool:
        run_dir = self._run_dir_for_case(failing_case)
        probe_dir = run_dir / ".cgr_logs"
        probe_dir.mkdir(parents=True, exist_ok=True)

        requested_target = str(probe_spec.get("target") or "rust").strip().lower()
        targets = ["rust", "c"] if requested_target == "both" else [requested_target]
        if any(target not in {"rust", "c"} for target in targets):
            print(f"    [rtest] debug_probe target 无效：{requested_target}")
            return False
        results: Dict[str, object] = {}
        success = False
        for target in targets:
            effective_spec = self._effective_target_spec(probe_spec, target)
            target_summary = self._execute_dynamic_target(
                rust_project_path=rust_project_path,
                bin_name=bin_name,
                run_dir=run_dir,
                probe_dir=probe_dir,
                probe_spec=effective_spec,
                attempt=attempt,
                target=target,
            )
            results[target] = target_summary
            success = success or bool(target_summary.get("ok"))

        summary: Dict[str, object] = {
            "target": requested_target,
            "request": probe_spec,
            "probe_round": attempt,
            "targets": results,
            "ok": success,
        }
        if requested_target == "rust" and isinstance(results.get("rust"), dict):
            summary.update(results["rust"])  # backward-compatible single-target fields
        path = LogAgent.write_named_bundle(probe_dir, f"debug_probe_{attempt}.json", summary)
        print(f"    [rtest] 已执行 debug_probe，结果写入：{path.relative_to(run_dir)}")
        return success

    def execute_static_probes(
        self,
        *,
        rust_project_path: str,
        bin_name: str,
        failing_case: TestCaseResult,
        probes: Iterable[StaticProbeSpec],
        program_args: list[str],
        attempt: int,
    ) -> bool:
        run_dir = self._run_dir_for_case(failing_case)
        probe_dir = run_dir / ".cgr_logs"
        round_dir = probe_dir / f"static_probe_round_{attempt}"
        probe_list = list(probes)
        targets: Dict[str, object] = {}
        ok = False
        for target in ("rust", "c"):
            selected = [
                probe for probe in probe_list if probe.target in {target, "both"}
            ]
            if not selected:
                continue
            result = self._execute_static_target(
                rust_project_path=rust_project_path,
                bin_name=bin_name,
                target=target,
                probes=selected,
                program_args=program_args,
                work_dir=round_dir / target,
                run_dir=run_dir,
            )
            targets[target] = result
            ok = ok or bool(result.get("ok"))
        summary = {
            "probe_round": attempt,
            "mode": "static",
            "active_probes": [_static_probe_to_dict(probe) for probe in probe_list],
            "program_args": list(program_args),
            "targets": targets,
            "ok": ok,
        }
        path = LogAgent.write_named_bundle(probe_dir, f"static_probe_{attempt}.json", summary)
        print(f"    [rtest] 已执行 static_probe，结果写入：{path.relative_to(run_dir)}")
        return ok

    def _execute_dynamic_target(
        self,
        *,
        rust_project_path: str,
        bin_name: str,
        run_dir: Path,
        probe_dir: Path,
        probe_spec: Dict[str, object],
        attempt: int,
        target: str,
    ) -> Dict[str, object]:
        backend_name = str(probe_spec.get("backend") or "lldb").strip().lower()
        try:
            request = LogAgent.parse_instrumentation_request(probe_spec)
        except Exception as exc:  # noqa: BLE001
            return {"target": target, "error": str(exc), "ok": False}
        binary = (
            self._locate_release_binary(rust_project_path, f"{bin_name}-rust")
            if target == "rust"
            else self._c_binary_path
        )
        if not binary:
            return {"target": target, "error": f"找不到 {target} 可执行文件", "ok": False}
        if backend_name in {"dap", "lldb-dap"}:
            dap_payload = DapBackend().build_launch_payload(binary, request, cwd=str(run_dir))
            LogAgent.write_named_bundle(
                probe_dir,
                f"debug_probe_{attempt}.{target}.dap.json",
                {"backend": backend_name, "target": target, "launch": dap_payload, "request": probe_spec},
            )
        backend = LldbBackend(timeout_seconds=max(10, min(60, self._test_timeout_seconds + 15)))
        try:
            result = backend.run(
                binary,
                request,
                log_dir=probe_dir / f"debug_probe_round_{attempt}" / target,
                cwd=str(run_dir),
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "target": target,
                "backend": backend_name,
                "executed_backend": "lldb",
                "error": str(exc),
                "ok": False,
            }
        return {
            "target": target,
            "backend": backend_name,
            "executed_backend": "lldb",
            "program": binary,
            "ok": result.ok,
            "returncode": result.returncode,
            "command": result.command,
            "script_path": result.script_path,
            "stdout_tail": result.stdout[-2000:],
            "stderr_tail": result.stderr[-2000:],
            "frames": result.frames,
            "locals": result.locals,
            "watch_values": result.watch_values,
        }

    def _execute_static_target(
        self,
        *,
        rust_project_path: str,
        bin_name: str,
        target: str,
        probes: list[StaticProbeSpec],
        program_args: list[str],
        work_dir: Path,
        run_dir: Path,
    ) -> Dict[str, object]:
        source_root = Path(rust_project_path if target == "rust" else self._c_project_path)
        if not source_root.is_dir():
            return {"target": target, "ok": False, "error": f"{target} project path unavailable"}
        project_copy = work_dir / "project"
        try:
            if project_copy.exists():
                shutil.rmtree(project_copy)
            shutil.copytree(source_root, project_copy, ignore=_ignore_static_copy_paths)
            _apply_static_probes(project_copy, probes)
            if target == "rust":
                build = subprocess.run(
                    ["cargo", "build", "--release"],
                    cwd=str(project_copy),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=self._build_timeout_seconds,
                    check=False,
                )
                binary = self._locate_release_binary(str(project_copy), f"{bin_name}-rust")
                build_stdout, build_stderr = build.stdout, build.stderr
                build_ok = build.returncode == 0 and bool(binary)
            else:
                c_result = CProjectBuilder(timeout_seconds=self._build_timeout_seconds).clean_and_build(
                    str(project_copy), expected_bin_name=bin_name
                )
                binary = c_result.binary_path
                build_stdout, build_stderr = c_result.stdout, c_result.stderr
                build_ok = c_result.ok and bool(binary)
            if not build_ok:
                return {
                    "target": target,
                    "ok": False,
                    "phase": "build",
                    "stdout_tail": build_stdout[-2000:],
                    "stderr_tail": build_stderr[-4000:],
                }
            proc = subprocess.run(
                [binary, *program_args],
                cwd=str(run_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=self._test_timeout_seconds,
                check=False,
            )
            return {
                "target": target,
                "ok": True,
                "program": binary,
                "returncode": proc.returncode,
                "stdout_tail": proc.stdout[-2000:],
                "stderr_tail": proc.stderr[-4000:],
                "probe_lines": [
                    line for line in proc.stderr.splitlines() if "[CGR_STATIC:" in line
                ][-40:],
            }
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"target": target, "ok": False, "error": str(exc)}

    @staticmethod
    def _effective_target_spec(probe_spec: Dict[str, object], target: str) -> Dict[str, object]:
        effective = dict(probe_spec)
        nested = probe_spec.get("targets")
        if isinstance(nested, dict) and isinstance(nested.get(target), dict):
            effective.update(nested[target])
        effective["target"] = target
        return effective

    @staticmethod
    def _run_dir_for_case(failing_case: TestCaseResult) -> Path:
        script = Path(failing_case.script_path)
        return script.parent / f".run_{script.stem}"

    @staticmethod
    def _debug_probe_path_sort_key(path: Path) -> tuple[int, float, str]:
        match = re.search(r"debug_probe_(\d+)\.json$", path.name)
        attempt = int(match.group(1)) if match else -1
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        return (attempt, mtime, path.name)

    @staticmethod
    def _static_probe_path_sort_key(path: Path) -> tuple[int, float, str]:
        match = re.search(r"static_probe_(\d+)\.json$", path.name)
        attempt = int(match.group(1)) if match else -1
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        return (attempt, mtime, path.name)


def _static_probe_to_dict(probe: StaticProbeSpec) -> Dict[str, object]:
    return {
        "id": probe.probe_id,
        "target": probe.target,
        "file": probe.file,
        "line": probe.line,
        "expressions": list(probe.expressions),
        "label": probe.label,
    }


def _ignore_static_copy_paths(_src: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name in {".git", "target", ".cgr_logs", "__pycache__"}
        or name.startswith(".run_")
    }


def _apply_static_probes(project_root: Path, probes: list[StaticProbeSpec]) -> None:
    grouped: Dict[str, list[StaticProbeSpec]] = {}
    for probe in probes:
        grouped.setdefault(probe.file, []).append(probe)
    for raw_path, file_probes in grouped.items():
        relative = Path(raw_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"unsafe static probe path: {raw_path}")
        path = project_root / relative
        if not path.is_file():
            raise FileNotFoundError(f"static probe source does not exist: {raw_path}")
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)
        c_header_inserted = False
        if path.suffix.lower() == ".c" and not any("#include <stdio.h>" in line for line in lines):
            lines.insert(0, "#include <stdio.h>\n")
            c_header_inserted = True
        for probe in sorted(file_probes, key=lambda item: item.line, reverse=True):
            index = probe.line - 1 + (1 if c_header_inserted else 0)
            if index < 0 or index > len(lines):
                raise ValueError(f"static probe line out of range: {raw_path}:{probe.line}")
            lines.insert(index, _render_static_statement(probe))
        path.write_text("".join(lines), encoding="utf-8", newline="\n")


def _render_static_statement(probe: StaticProbeSpec) -> str:
    marker = re.sub(r"[^A-Za-z0-9_.-]", "_", probe.probe_id)
    label = probe.label.replace('"', "'")
    prefix = f"[CGR_STATIC:{marker}] {label}".rstrip()
    if probe.target == "c" or probe.file.endswith(".c"):
        if probe.expressions:
            fmt = " ".join(f"expr{idx}=%lld" for idx in range(len(probe.expressions)))
            args = ", ".join(f"(long long)({expr})" for expr in probe.expressions)
            return f'fprintf(stderr, "{prefix} {fmt}\\n", {args});\n'
        return f'fprintf(stderr, "{prefix}\\n");\n'
    if probe.expressions:
        fmt = " ".join(f"expr{idx}={{:?}}" for idx in range(len(probe.expressions)))
        args = ", ".join(probe.expressions)
        return f'eprintln!("{prefix} {fmt}", {args});\n'
    return f'eprintln!("{prefix}");\n'

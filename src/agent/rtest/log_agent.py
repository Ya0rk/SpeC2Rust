"""Runtime evidence collection and compression helpers for rtest."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from .models import TestCaseResult


@dataclass
class RuntimeEvidenceBundle:
    case_name: str
    exit_code: int = 0
    error: str = ""
    stdout: str = ""
    stderr: str = ""
    frames: List[Dict[str, Any]] = field(default_factory=list)
    locals: Dict[str, Any] = field(default_factory=dict)
    trace_lines: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StaticProbeUpdate:
    add: List[Any] = field(default_factory=list)
    remove: List[str] = field(default_factory=list)
    clear: bool = False
    program_args: List[str] = field(default_factory=list)


class LogAgent:
    """Normalize runtime evidence into a compact JSON-friendly bundle."""

    @staticmethod
    def bundle_from_result(
        result: TestCaseResult,
        *,
        case_name: str | None = None,
    ) -> RuntimeEvidenceBundle:
        trace_lines = [line.rstrip() for line in (result.trace or "").splitlines() if line.strip()]
        error = result.stderr.strip() or result.stdout.strip()
        return RuntimeEvidenceBundle(
            case_name=case_name or result.name,
            exit_code=result.exit_code,
            error=error,
            stdout=result.stdout,
            stderr=result.stderr,
            trace_lines=trace_lines,
        )

    @staticmethod
    def compress(bundle: RuntimeEvidenceBundle, max_chars: int = 4000) -> Dict[str, Any]:
        summary: Dict[str, Any] = {
            "case_name": bundle.case_name,
            "exit_code": bundle.exit_code,
            "error": _clip_text(bundle.error or bundle.stderr or bundle.stdout, max_chars // 4),
            "stdout_tail": _clip_text(bundle.stdout, max_chars // 4),
            "stderr_tail": _clip_text(bundle.stderr, max_chars // 2),
            "frames": _clip_frames(bundle.frames, 8),
            "locals": _clip_mapping(bundle.locals, max_chars // 4),
            "trace": _clip_lines(bundle.trace_lines, 40, max_chars // 2),
        }
        if bundle.metadata:
            summary["metadata"] = _clip_mapping(bundle.metadata, max_chars // 4)
        return summary

    @staticmethod
    def write_case_bundle(log_dir: Path, bundle: Dict[str, Any]) -> Path:
        return LogAgent.write_named_bundle(log_dir, "runtime.json", bundle)

    @staticmethod
    def write_named_bundle(log_dir: Path, filename: str, bundle: Dict[str, Any]) -> Path:
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / filename
        path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    @staticmethod
    def combine_probe_summary(base: Dict[str, Any], probe: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(base or {})
        merged.pop("debug_probes", None)
        if probe:
            merged["debug_probe"] = probe
        return merged

    @staticmethod
    def parse_instrumentation_request(spec: Dict[str, Any]):
        from .debug_backends import BreakpointSpec, InstrumentationRequest

        if not isinstance(spec, dict):
            raise TypeError("instrumentation request must be a mapping")
        breakpoints = []
        for item in spec.get("breakpoints") or []:
            if not isinstance(item, dict):
                continue
            file = str(item.get("file") or "").strip()
            line = item.get("line")
            try:
                line_no = int(line)
            except (TypeError, ValueError):
                continue
            if file and line_no > 0:
                breakpoints.append(BreakpointSpec(file=file, line=line_no))
        return InstrumentationRequest(
            target=_normalize_target(spec.get("target") or "rust"),
            breakpoints=breakpoints,
            collect_stack=bool(spec.get("collect_stack", spec.get("collectStack", True))),
            collect_locals=bool(spec.get("collect_locals", spec.get("collectLocals", True))),
            watch_expressions=[str(item) for item in (spec.get("watch_expressions") or spec.get("watchExpressions") or []) if str(item).strip()],
            program_args=[str(item) for item in (spec.get("program_args") or spec.get("args") or []) if str(item).strip()],
        )

    @staticmethod
    def parse_static_probe_update(spec: Dict[str, Any]) -> StaticProbeUpdate:
        from .debug_backends import StaticProbeSpec

        if not isinstance(spec, dict):
            raise TypeError("static probe update must be a mapping")
        probes: List[StaticProbeSpec] = []
        for item in spec.get("add") or spec.get("probes") or []:
            if not isinstance(item, dict):
                continue
            probe_id = str(item.get("id") or item.get("probe_id") or "").strip()
            file = str(item.get("file") or "").strip()
            try:
                line = int(item.get("line"))
            except (TypeError, ValueError):
                line = 0
            if not probe_id or not file or line <= 0:
                continue
            probes.append(
                StaticProbeSpec(
                    probe_id=probe_id,
                    target=_normalize_target(item.get("target") or "rust"),
                    file=file,
                    line=line,
                    expressions=[
                        str(expr).strip()
                        for expr in item.get("expressions") or []
                        if str(expr).strip()
                    ],
                    label=str(item.get("label") or "").strip(),
                )
            )
        return StaticProbeUpdate(
            add=probes,
            remove=[
                str(item).strip()
                for item in spec.get("remove") or spec.get("remove_ids") or []
                if str(item).strip()
            ],
            clear=bool(spec.get("clear", False)),
            program_args=[
                str(item)
                for item in spec.get("program_args") or spec.get("args") or []
            ],
        )


def _normalize_target(raw: object) -> str:
    value = str(raw or "rust").strip().lower()
    if value not in {"rust", "c", "both"}:
        raise ValueError(f"unsupported instrumentation target: {value}")
    return value


def _clip_text(text: str, limit: int) -> str:
    value = (text or "").strip()
    if limit <= 0 or len(value) <= limit:
        return value
    return value[-limit:]


def _clip_frames(frames: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    clipped: List[Dict[str, Any]] = []
    for frame in (frames or [])[: max(limit, 0)]:
        clipped.append(_clip_mapping(frame, 120))
    return clipped


def _clip_lines(lines: List[str], line_limit: int, char_limit: int) -> List[str]:
    clipped = [line.rstrip() for line in (lines or []) if line is not None]
    if line_limit >= 0:
        clipped = clipped[-line_limit:]
    if char_limit > 0:
        total = "\n".join(clipped)
        if len(total) > char_limit:
            total = total[-char_limit:]
        clipped = total.splitlines()
    return clipped


def _clip_mapping(mapping: Dict[str, Any], char_limit: int) -> Dict[str, Any]:
    if not mapping:
        return {}
    clipped: Dict[str, Any] = {}
    for key, value in mapping.items():
        clipped[str(key)] = _clip_value(value, char_limit)
    return clipped


def _clip_value(value: Any, char_limit: int) -> Any:
    if isinstance(value, str):
        return _clip_text(value, char_limit)
    if isinstance(value, dict):
        return _clip_mapping(value, char_limit)
    if isinstance(value, list):
        return [_clip_value(item, char_limit) for item in value[:16]]
    return value

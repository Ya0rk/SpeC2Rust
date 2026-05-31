"""rtest 的数据类。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import List

from .constants import FAILURE_EXCERPT_CHARS, FAILURE_SIGNATURE_HEX


@dataclass
class TestCaseResult:
    name: str
    script_path: str
    passed: bool
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float = 0.0
    trace: str = ""
    run_dir: str = ""

    def short_failure_excerpt(self, max_chars: int = FAILURE_EXCERPT_CHARS) -> str:
        text = (self.stderr.strip() or self.stdout.strip() or "(no output)")
        if len(text) <= max_chars:
            return text
        return text[-max_chars:]

    def failure_signature(self) -> str:
        material = _normalize_failure_material(
            f"{self.exit_code}|{self.stderr[-1600:]}|{self.stdout[-3000:]}|{self.trace[-2000:]}"
        )
        # 使用 sha256 取前 N 位十六进制作为稳定签名（sha1 已不推荐）。
        return hashlib.sha256(material.encode("utf-8", errors="ignore")).hexdigest()[
            :FAILURE_SIGNATURE_HEX
        ]


@dataclass
class TestRunSummary:
    total: int
    passed: int
    failed: int
    results: List[TestCaseResult] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return self.failed == 0 and self.total > 0


def _normalize_failure_material(text: str) -> str:
    """去掉时间戳、长 PATH 和临时路径噪声，让 stall 检测关注真正的失败语义。"""
    normalized = text or ""
    normalized = re.sub(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+\s+[+-]\d{4}", "<timestamp>", normalized)
    normalized = re.sub(r"(?m)^(---|\+\+\+) [^\n]+$", r"\1 <diff-file>", normalized)
    normalized = re.sub(r"PATH: [^\n]+", "PATH: <path>", normalized)
    normalized = re.sub(r"\bin \([^)\n]{200,}\)", "in (<PATH>)", normalized)
    normalized = re.sub(r"(/[^\s:'\"]*/)(?:which-rust|which)(?=\s|\[|:)", r"<BIN>", normalized)
    normalized = re.sub(r"/mnt/[a-z]/Code/C2R-Auto/cGrcode/(?:datasets|output)/[^\s:'\"]+", "<PROJECT_PATH>", normalized)
    normalized = re.sub(r"results/test\d+_[A-Za-z0-9_.-]+", "results/<test-artifact>", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()

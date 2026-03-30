import os
import re
from typing import Dict, List


class ErrorOrganizerAgent:
    """错误梳理 Agent：对大量编译/测试错误做规范化、切分和分批。"""

    def __init__(self, batch_size: int = 10):
        """
        初始化错误梳理器。

        Args:
            batch_size: 每批最多保留多少条诊断，默认 10
        """
        self.batch_size = max(1, batch_size)

    def _strip_ansi(self, text: str) -> str:
        """移除命令行输出中的 ANSI 转义序列。"""
        return re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', text or '')

    def _normalize_error_message(self, error_message: str) -> str:
        """统一错误输出格式，减少后续切分时的噪声。"""
        cleaned = self._strip_ansi(error_message).replace('\r\n', '\n').replace('\r', '\n')
        lines = [line.rstrip() for line in cleaned.splitlines()]
        normalized_lines: List[str] = []
        previous_blank = False
        for line in lines:
            is_blank = not line.strip()
            if is_blank and previous_blank:
                continue
            normalized_lines.append(line)
            previous_blank = is_blank
        return "\n".join(normalized_lines).strip()

    def _split_diagnostics(self, error_message: str) -> List[str]:
        """
        把长错误输出切成单条诊断块。
        这里优先识别 error/warning/failed to compile 等典型开头。
        """
        normalized = self._normalize_error_message(error_message)
        if not normalized:
            return []

        lines = normalized.splitlines()
        blocks: List[List[str]] = []
        current_block: List[str] = []
        diagnostic_header = re.compile(r'^(error(\[[A-Z0-9]+\])?:|warning:|note:|help:|error: could not compile)')

        for line in lines:
            if diagnostic_header.match(line) and current_block:
                blocks.append(current_block)
                current_block = [line]
            else:
                current_block.append(line)

        if current_block:
            blocks.append(current_block)

        return ["\n".join(block).strip() for block in blocks if "\n".join(block).strip()]

    def _extract_candidate_files(self, diagnostic: str, project_path: str) -> List[str]:
        """从单条诊断中提取候选文件路径。"""
        candidates: List[str] = []
        for match in re.finditer(r'--> ([^:\n]+):(\d+):(\d+)', diagnostic):
            file_path = match.group(1).strip()
            if not os.path.isabs(file_path):
                file_path = os.path.join(project_path, file_path)
            if os.path.exists(file_path) and file_path not in candidates:
                candidates.append(file_path)

        if "Cargo.toml" in diagnostic:
            cargo_toml = os.path.join(project_path, "Cargo.toml")
            if os.path.exists(cargo_toml) and cargo_toml not in candidates:
                candidates.append(cargo_toml)

        return candidates

    def _extract_error_code(self, diagnostic: str) -> str:
        """
        提取单条诊断的错误码。
        如果没有明确错误码，则退化为 error/warning/other 三类。
        """
        match = re.search(r'error\[([A-Z0-9]+)\]', diagnostic)
        if match:
            return match.group(1)
        if diagnostic.lstrip().startswith("warning:"):
            return "warning"
        if "error:" in diagnostic:
            return "error"
        return "other"

    def _group_diagnostics(self, diagnostics: List[str], project_path: str) -> List[Dict]:
        """
        先按“错误码 + 主文件”聚类，尽量把同类问题放到同一批中。
        """
        grouped: Dict[str, Dict] = {}

        for diagnostic in diagnostics:
            candidate_files = self._extract_candidate_files(diagnostic, project_path)
            primary_file = candidate_files[0] if candidate_files else "__unknown__"
            error_code = self._extract_error_code(diagnostic)
            key = f"{error_code}::{primary_file}"

            if key not in grouped:
                grouped[key] = {
                    "error_code": error_code,
                    "primary_file": primary_file,
                    "diagnostics": [],
                    "candidate_files": [],
                }

            grouped[key]["diagnostics"].append(diagnostic)
            for file_path in candidate_files:
                if file_path not in grouped[key]["candidate_files"]:
                    grouped[key]["candidate_files"].append(file_path)

        grouped_items = list(grouped.values())
        grouped_items.sort(
            key=lambda item: (
                0 if item["primary_file"] != "__unknown__" else 1,
                item["error_code"],
                item["primary_file"],
            )
        )
        return grouped_items

    def organize_errors(self, error_message: str, project_path: str) -> List[Dict]:
        """
        把错误输出整理成分批后的结构化结果。

        Returns:
            [
                {
                    "batch_index": 1,
                    "diagnostics": [...],
                    "candidate_files": [...],
                    "summary": "..."
                }
            ]
        """
        diagnostics = self._split_diagnostics(error_message)
        if not diagnostics:
            diagnostics = [self._normalize_error_message(error_message)]

        grouped_diagnostics = self._group_diagnostics(diagnostics, project_path)
        batches: List[Dict] = []
        current_batch_diagnostics: List[str] = []
        current_batch_files: List[str] = []
        current_batch_codes: List[str] = []

        def flush_batch():
            if not current_batch_diagnostics:
                return
            summary = (
                f"当前批次共 {len(current_batch_diagnostics)} 条诊断，"
                f"涉及 {len(current_batch_files)} 个候选文件，"
                f"主要错误类别：{', '.join(current_batch_codes[:5])}"
            )
            batches.append({
                "batch_index": len(batches) + 1,
                "diagnostics": list(current_batch_diagnostics),
                "candidate_files": list(current_batch_files),
                "summary": summary,
            })

        for group in grouped_diagnostics:
            group_diagnostics = group["diagnostics"]
            group_files = group["candidate_files"]
            group_code = group["error_code"]

            if current_batch_diagnostics and len(current_batch_diagnostics) + len(group_diagnostics) > self.batch_size:
                flush_batch()
                current_batch_diagnostics = []
                current_batch_files = []
                current_batch_codes = []

            for diagnostic in group_diagnostics:
                current_batch_diagnostics.append(diagnostic)
            for file_path in group_files:
                if file_path not in current_batch_files:
                    current_batch_files.append(file_path)
            if group_code not in current_batch_codes:
                current_batch_codes.append(group_code)

            while len(current_batch_diagnostics) >= self.batch_size:
                overflow = current_batch_diagnostics[self.batch_size:]
                current_batch_diagnostics = current_batch_diagnostics[:self.batch_size]
                flush_batch()
                current_batch_diagnostics = list(overflow)
                current_batch_files = list(group_files)
                current_batch_codes = [group_code]

        flush_batch()

        return batches

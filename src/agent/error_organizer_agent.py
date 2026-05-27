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
        return list(self._extract_candidate_locations(diagnostic, project_path).keys())

    def _extract_candidate_locations(self, diagnostic: str, project_path: str) -> Dict[str, List[int]]:
        """从单条诊断中提取候选文件路径及其行号。"""
        locations: Dict[str, List[int]] = {}
        for match in re.finditer(r'--> ([^:\n]+):(\d+):(\d+)', diagnostic):
            file_path = match.group(1).strip()
            line_number = int(match.group(2))
            if not os.path.isabs(file_path):
                file_path = os.path.join(project_path, file_path)
            if os.path.exists(file_path):
                if file_path not in locations:
                    locations[file_path] = []
                if line_number not in locations[file_path]:
                    locations[file_path].append(line_number)

        if "Cargo.toml" in diagnostic:
            cargo_toml = os.path.join(project_path, "Cargo.toml")
            if os.path.exists(cargo_toml) and cargo_toml not in locations:
                locations[cargo_toml] = []

        return locations

    def _build_line_windows(self, line_numbers: List[int], total_lines: int, radius: int = 15) -> List[Dict]:
        """把锚点行号合并为若干个 +/-radius 的行窗口。"""
        anchors = sorted({line for line in line_numbers if line > 0})
        if not anchors:
            anchors = [1]

        windows: List[Dict] = []
        for anchor in anchors:
            start_line = max(1, anchor - radius)
            end_line = min(total_lines, anchor + radius)
            if not windows or start_line > windows[-1]["end_line"] + 1:
                windows.append({
                    "start_line": start_line,
                    "end_line": end_line,
                    "anchor_lines": [anchor],
                })
            else:
                windows[-1]["end_line"] = max(windows[-1]["end_line"], end_line)
                if anchor not in windows[-1]["anchor_lines"]:
                    windows[-1]["anchor_lines"].append(anchor)
                windows[-1]["anchor_lines"].sort()
        return windows

    def _read_file(self, path: str) -> str:
        """读取普通文本文件；失败时返回空串。"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except (OSError, UnicodeDecodeError):
            return ""

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
            candidate_locations = self._extract_candidate_locations(diagnostic, project_path)
            candidate_files = list(candidate_locations.keys())
            primary_file = candidate_files[0] if candidate_files else "__unknown__"
            error_code = self._extract_error_code(diagnostic)
            key = f"{error_code}::{primary_file}"

            if key not in grouped:
                grouped[key] = {
                    "error_code": error_code,
                    "primary_file": primary_file,
                    "diagnostics": [],
                    "candidate_files": [],
                    "candidate_locations": {},
                }

            grouped[key]["diagnostics"].append(diagnostic)
            for file_path in candidate_files:
                if file_path not in grouped[key]["candidate_files"]:
                    grouped[key]["candidate_files"].append(file_path)
            for file_path, line_numbers in candidate_locations.items():
                if file_path not in grouped[key]["candidate_locations"]:
                    grouped[key]["candidate_locations"][file_path] = []
                for line_number in line_numbers:
                    if line_number not in grouped[key]["candidate_locations"][file_path]:
                        grouped[key]["candidate_locations"][file_path].append(line_number)

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
        current_batch_locations: Dict[str, List[int]] = {}

        def build_context_blocks(file_locations: Dict[str, List[int]]) -> List[Dict]:
            blocks: List[Dict] = []
            for file_path in sorted(file_locations):
                line_numbers = file_locations[file_path]
                raw_content = self._read_file(file_path)
                if not raw_content.strip():
                    continue

                normalized = raw_content.replace('\r\n', '\n').replace('\r', '\n')
                lines = normalized.split('\n')
                rel_path = os.path.relpath(file_path, project_path).replace("\\", "/")
                windows = self._build_line_windows(line_numbers, len(lines))
                for window in windows:
                    start_line = max(1, min(window["start_line"], len(lines)))
                    end_line = max(start_line, min(window["end_line"], len(lines)))
                    snippet_lines = [
                        f"{line_no:04d} | {lines[line_no - 1].rstrip()}"
                        for line_no in range(start_line, end_line + 1)
                    ]
                    blocks.append({
                        "file_path": rel_path,
                        "start_line": start_line,
                        "end_line": end_line,
                        "anchor_lines": list(window["anchor_lines"]),
                        "content": "\n".join(snippet_lines),
                    })
            return blocks

        def format_context_text(context_blocks: List[Dict]) -> str:
            parts: List[str] = []
            for block in context_blocks:
                anchors = ", ".join(str(line) for line in block["anchor_lines"]) if block["anchor_lines"] else "n/a"
                parts.append(
                    f"File: {block['file_path']}\n"
                    f"Line range: {block['start_line']}-{block['end_line']} (anchors: {anchors})\n"
                    f"{block['content']}"
                )
            return "\n\n".join(parts)

        def flush_batch():
            if not current_batch_diagnostics:
                return
            candidate_contexts = build_context_blocks(current_batch_locations)
            summary = (
                f"当前批次共 {len(current_batch_diagnostics)} 条诊断，"
                f"涉及 {len(current_batch_files)} 个候选文件，"
                f"附加 {len(candidate_contexts)} 段源码上下文，"
                f"主要错误类别：{', '.join(current_batch_codes[:5])}"
            )
            batches.append({
                "batch_index": len(batches) + 1,
                "diagnostics": list(current_batch_diagnostics),
                "candidate_files": list(current_batch_files),
                "candidate_contexts": candidate_contexts,
                "context_text": format_context_text(candidate_contexts),
                "summary": summary,
            })

        for group in grouped_diagnostics:
            group_diagnostics = group["diagnostics"]
            group_files = group["candidate_files"]
            group_code = group["error_code"]
            group_locations = group.get("candidate_locations", {})

            if current_batch_diagnostics and len(current_batch_diagnostics) + len(group_diagnostics) > self.batch_size:
                flush_batch()
                current_batch_diagnostics = []
                current_batch_files = []
                current_batch_codes = []
                current_batch_locations = {}

            for diagnostic in group_diagnostics:
                current_batch_diagnostics.append(diagnostic)
            for file_path in group_files:
                if file_path not in current_batch_files:
                    current_batch_files.append(file_path)
            if group_code not in current_batch_codes:
                current_batch_codes.append(group_code)
            for file_path, line_numbers in group_locations.items():
                if file_path not in current_batch_locations:
                    current_batch_locations[file_path] = []
                for line_number in line_numbers:
                    if line_number not in current_batch_locations[file_path]:
                        current_batch_locations[file_path].append(line_number)

            while len(current_batch_diagnostics) >= self.batch_size:
                overflow = current_batch_diagnostics[self.batch_size:]
                current_batch_diagnostics = current_batch_diagnostics[:self.batch_size]
                flush_batch()
                current_batch_diagnostics = list(overflow)
                current_batch_files = list(group_files)
                current_batch_codes = [group_code]
                current_batch_locations = {
                    file_path: list(line_numbers)
                    for file_path, line_numbers in group_locations.items()
                }

        flush_batch()

        return batches

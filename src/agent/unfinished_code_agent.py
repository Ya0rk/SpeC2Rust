import os
import re
from pathlib import Path
from typing import Dict, List, Optional

from config.config import Config

from config.prompt import unfinished_code_prompt_manager
from .rust_agent import RustAgent


class UnfinishedCodeAgent:
    """扫描 Rust 项目中的未完成实现，并通知 RustAgent 继续补全。"""

    PLACEHOLDER_PATTERNS = [
        ("todo", re.compile(r"\btodo!\s*\(", re.IGNORECASE)),
        ("unimplemented", re.compile(r"\bunimplemented!\s*\(", re.IGNORECASE)),
        (
            "panic_not_implemented",
            re.compile(r'\bpanic!\s*\(\s*"[^"\n]*(?:todo|not implemented|unimplemented|stub)[^"\n]*"', re.IGNORECASE),
        ),
        (
            "unreachable_not_implemented",
            re.compile(r'\bunreachable!\s*\(\s*"[^"\n]*(?:todo|not implemented|unimplemented|stub)[^"\n]*"', re.IGNORECASE),
        ),
    ]

    def __init__(self, config: Optional[Config] = None, rust_agent: Optional[RustAgent] = None):
        self.config = config or Config()
        self.rust_agent = rust_agent or RustAgent(config=self.config)

    def _ensure_rust_agent_bound(self, project_path: str):
        if not self.rust_agent.project_path or Path(self.rust_agent.project_path).resolve() != Path(project_path).resolve():
            self.rust_agent.attach_existing_project(project_path=project_path)

    def _iter_rust_files(self, project_path: str):
        skipped_dirs = {"target", ".git", ".idea", ".vscode", "__pycache__"}
        for root, dirs, files in os.walk(project_path):
            dirs[:] = [d for d in dirs if d not in skipped_dirs]
            for file_name in files:
                if not file_name.endswith(".rs"):
                    continue
                yield os.path.join(root, file_name)

    def _scan_content(self, content: str) -> List[Dict[str, str]]:
        findings: List[Dict[str, str]] = []
        for line_no, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("//"):
                continue
            for marker_name, pattern in self.PLACEHOLDER_PATTERNS:
                if pattern.search(line):
                    findings.append(
                        {
                            "line": line_no,
                            "marker": marker_name,
                            "snippet": stripped[:220],
                        }
                    )
        return findings

    def scan_project(self, project_path: str) -> Dict[str, List[Dict[str, str]]]:
        findings_by_file: Dict[str, List[Dict[str, str]]] = {}
        for full_path in self._iter_rust_files(project_path):
            try:
                with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except Exception as e:
                print(f"读取 Rust 文件失败：{full_path}，错误：{e}")
                continue

            findings = self._scan_content(content)
            if findings:
                rel_path = os.path.relpath(full_path, project_path).replace("\\", "/")
                findings_by_file[rel_path] = findings
        return findings_by_file

    def _format_findings_summary(self, findings: List[Dict[str, str]]) -> str:
        lines = []
        for item in findings:
            lines.append(f"- 第 {item['line']} 行，{item['marker']}：{item['snippet']}")
        return "\n".join(lines)

    def _build_documentation_context(self, max_chars: int = 12000) -> str:
        if not self.rust_agent.doc_contents:
            return ""

        parts = []
        current_len = 0
        for path, content in self.rust_agent.doc_contents.items():
            chunk = f"\n=== 文档：{path} ===\n{content}\n"
            if current_len + len(chunk) > max_chars:
                remaining = max_chars - current_len
                if remaining > 0:
                    parts.append(chunk[:remaining])
                break
            parts.append(chunk)
            current_len += len(chunk)
        return "".join(parts).strip()

    def _repair_single_file(self, project_path: str, rel_path: str, findings: List[Dict[str, str]]) -> bool:
        full_path = os.path.join(project_path, rel_path)
        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                current_code = f.read()
        except Exception as e:
            print(f"读取待补全文件失败：{full_path}，错误：{e}")
            return False

        project_context = self.rust_agent.build_project_generation_context(include_docs=False)
        documentation_context = self._build_documentation_context()
        findings_summary = self._format_findings_summary(findings)

        system_prompt = unfinished_code_prompt_manager.continue_unfinished_file_system_prompt()
        user_prompt = unfinished_code_prompt_manager.continue_unfinished_file(
            file_path=rel_path,
            findings_summary=findings_summary,
            current_code=current_code,
            project_context=project_context or "无额外项目结构上下文，请以当前文件和检测到的占位为主完成实现。",
            documentation_context=documentation_context,
        )

        regenerated = self.rust_agent.regenerate_existing_file(
            file_path=rel_path,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            code_lang="rust",
            max_rounds=5,
            label=f"未完成实现补全 {rel_path}",
            status_note="unfinished_code_repair",
        )
        if not regenerated:
            return False

        remaining = self._scan_content(regenerated)
        if remaining:
            print(f"文件仍存在未完成占位：{rel_path}，剩余 {len(remaining)} 处")
            return False
        return True

    def check_and_continue(self, project_path: str, max_passes: int = 2) -> Dict[str, object]:
        """
        检查项目中未完成实现，并让 RustAgent 定点补全这些文件。
        """
        self._ensure_rust_agent_bound(project_path)

        report: Dict[str, object] = {
            "checked_project": project_path,
            "passes": [],
            "repaired_files": [],
            "remaining_files": [],
            "success": True,
        }
        repaired_files = set()

        for pass_index in range(1, max_passes + 1):
            findings_by_file = self.scan_project(project_path)
            if not findings_by_file:
                break

            pass_summary = {
                "pass_index": pass_index,
                "files": {},
            }
            report["passes"].append(pass_summary)
            print(f"UnfinishedCodeAgent 第 {pass_index} 轮检测到 {len(findings_by_file)} 个文件存在未完成实现")

            for rel_path, findings in findings_by_file.items():
                print(f"尝试补全未完成文件：{rel_path}（{len(findings)} 处占位）")
                repaired = self._repair_single_file(project_path, rel_path, findings)
                pass_summary["files"][rel_path] = {
                    "placeholder_count": len(findings),
                    "repaired": repaired,
                }
                if repaired:
                    repaired_files.add(rel_path)

        final_findings = self.scan_project(project_path)
        report["repaired_files"] = sorted(repaired_files)
        report["remaining_files"] = sorted(final_findings.keys())
        report["success"] = not final_findings
        return report

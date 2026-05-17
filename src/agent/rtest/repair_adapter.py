"""对 ``RustRepairAgent`` 内部方法的稳定公共封装。

原实现直接调用了 ``RustRepairAgent`` 的 4 个下划线开头方法，一旦这些方法
签名变化，``rtest`` 会无声崩掉。本模块把这些调用集中到一个类里，后续如果
``RustRepairAgent`` 的接口演进，只需改这里一处。

唯一对 ``RustRepairAgent`` 的依赖点：
- ``_run_command(command, cwd, timeout_seconds) -> (ok, output)``
- ``_extract_json_payload(text) -> dict | None``
- ``_read_file_slice(project_dir, rel_path, start_line=None, end_line=None) -> str``
- ``_apply_structured_edits_with_audit(project_dir, edits) -> (applied, audit_records)``
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


class RepairAdapter:
    """把 RustRepairAgent 的私有能力封装为稳定公共接口。"""

    def __init__(self, repair_helper: Any):
        self._helper = repair_helper

    def run_command(self, command: str, cwd: str, *, timeout_seconds: int = 180) -> Tuple[bool, str]:
        return self._helper._run_command(command, cwd, timeout_seconds=timeout_seconds)

    def extract_json_payload(self, text: str) -> Optional[Dict]:
        return self._helper._extract_json_payload(text)

    def read_file_slice(
        self,
        project_dir: str,
        rel_path: str,
        *,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> str:
        return self._helper._read_file_slice(
            project_dir, rel_path, start_line=start_line, end_line=end_line
        )

    def apply_structured_edits(
        self, project_dir: str, edits: List[Dict]
    ) -> Tuple[bool, List[Dict]]:
        return self._helper._apply_structured_edits_with_audit(project_dir, edits)

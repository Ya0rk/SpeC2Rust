"""C 源码记录加载 + 索引。

修复原实现的两个问题：

- **#27**：原 ``_fulfill_c_source_request`` 对每次请求都线性扫描 ``source_records``，
  大项目 + 多轮修复下性能退化。这里改为加载时构建 ``name -> record`` / ``file -> records``
  索引，查询 O(1)。
- **#2**：原实现对 ``kind=file`` 只做子串兜底匹配，返回的还是单个函数记录，
  与 prompt 描述的 "kind=file 返回文件" 不符。这里新增 ``CSourceIndex.fulfill_request``，
  ``kind=file`` 会把同一文件下的所有函数拼接成一条 "聚合记录" 返回，真正交付一个文件。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class CSourceIndex:
    records: List[Dict] = field(default_factory=list)
    _by_name: Dict[str, Dict] = field(default_factory=dict)
    _by_file: Dict[str, List[Dict]] = field(default_factory=dict)
    source_root: str = ""

    def _register(self, rec: Dict) -> None:
        name = str(rec.get("name") or "").strip()
        file_path = str(rec.get("file") or "").replace("\\", "/").strip()
        if name and name not in self._by_name:
            self._by_name[name.lower()] = rec
        if file_path:
            self._by_file.setdefault(file_path.lower(), []).append(rec)

    def add(self, rec: Dict) -> None:
        self.records.append(rec)
        self._register(rec)

    def __bool__(self) -> bool:
        return bool(self.records)

    # --------- 查询 ---------

    def find_function(self, name: str) -> Optional[Dict]:
        if not name:
            return None
        return self._by_name.get(name.lower())

    def find_file(self, path: str) -> List[Dict]:
        """按文件路径/文件名子串精确或模糊匹配，返回该文件下的所有函数记录。"""
        if not path:
            return []
        lowered = path.replace("\\", "/").lower()
        exact = self._by_file.get(lowered)
        if exact:
            return exact
        # 兜底：尾部匹配（支持只写 basename）
        matched: List[Dict] = []
        for file_key, recs in self._by_file.items():
            if file_key.endswith("/" + lowered) or file_key == lowered or lowered in file_key:
                matched.extend(recs)
        return matched

    def fulfill_request(self, request: Dict) -> Optional[Dict]:
        """响应 LLM 的 cgr_read 请求。

        - ``kind=function``：按名字查，找不到再做子串兜底。
        - ``kind=file``：把该文件下所有函数 source 拼成一条聚合记录返回，
          字段为 ``{name: '<path>'(file), file: '<path>', source: '合并内容', span: ...}``。
        """
        if not self.records or not isinstance(request, dict):
            return None

        kind = str(request.get("kind") or "function").lower()
        query = str(request.get("query") or request.get("path") or request.get("file") or "").strip()
        if not query:
            return None

        if kind == "file":
            ranged = self._fulfill_file_range(query, request)
            if ranged:
                return ranged
            recs = self.find_file(query)
            if not recs:
                # 兜底：query 可能是函数名，但用户标成了 file
                fn = self.find_function(query)
                return fn
            return _aggregate_file_record(recs)

        # 默认 function
        fn = self.find_function(query)
        if fn:
            return fn
        # 兜底：在 name / file 上做子串匹配
        lowered = query.lower()
        for rec in self.records:
            name = str(rec.get("name", "")).lower()
            file_path = str(rec.get("file", "")).replace("\\", "/").lower()
            if lowered == name or lowered in file_path or lowered in name:
                return rec
        return None

    def _fulfill_file_range(self, query: str, request: Dict) -> Optional[Dict]:
        mode = str(request.get("mode") or "").strip().lower()
        if mode not in {"line_range", "range"}:
            return None
        try:
            start_line = int(request.get("start_line"))
            end_line = int(request.get("end_line"))
        except Exception:
            return None
        if start_line <= 0 or end_line < start_line:
            return None
        source = self._read_source_file_range(query, start_line, end_line)
        if source is None:
            return None
        rel = self._canonical_file_path(query) or query
        return {
            "name": f"<file:{rel}:{start_line}-{end_line}>",
            "file": rel,
            "span": f"{start_line}-{end_line}",
            "source": source,
            "num_lines": len(source.splitlines()),
            "func_defid": f"{rel}:<file:{start_line}-{end_line}>",
            "is_file_aggregate": True,
            "is_line_range": True,
            "start_line": start_line,
            "end_line": end_line,
        }

    def _canonical_file_path(self, query: str) -> str:
        matches = self.find_file(query)
        if matches:
            return str(matches[0].get("file") or "").replace("\\", "/")
        return query.replace("\\", "/").strip()

    def _read_source_file_range(self, query: str, start_line: int, end_line: int) -> Optional[str]:
        if not self.source_root:
            return None
        rel = self._canonical_file_path(query)
        normalized = rel.replace("\\", "/").strip().lstrip("/")
        if not normalized or normalized.startswith("../") or "/../" in f"/{normalized}/":
            return None
        root = Path(self.source_root).resolve()
        full = (root / normalized).resolve()
        try:
            if root not in full.parents and full != root:
                return None
        except Exception:
            return None
        if not full.is_file():
            return None
        text = full.read_text(encoding="utf-8", errors="ignore")
        lines = text.splitlines()
        start = max(1, start_line)
        end = min(len(lines), end_line)
        if end < start:
            return ""
        return "\n".join(lines[start - 1:end]) + "\n"


def _aggregate_file_record(records: List[Dict]) -> Dict:
    """把同文件多个函数拼成一条"文件聚合记录"。"""
    if not records:
        return {}
    file_path = str(records[0].get("file") or "")
    parts: List[str] = []
    total_lines = 0
    for rec in records:
        name = rec.get("name") or "<anon>"
        span = rec.get("span") or ""
        src = str(rec.get("source") or "")
        total_lines += int(rec.get("num_lines") or src.count("\n") + 1)
        header = f"// ---- {name} [{span}] ----"
        parts.append(header)
        parts.append(src.rstrip())
        parts.append("")
    return {
        "name": f"<file:{file_path}>",
        "file": file_path,
        "span": "",
        "source": "\n".join(parts).rstrip() + "\n",
        "num_lines": total_lines,
        "func_defid": f"{file_path}:<file>",
        "is_file_aggregate": True,
    }


# --------- 加载 ---------


def load_source_records(
    c_project_path: str,
    *,
    repo_root: Optional[Path] = None,
    explicit_path: Optional[str] = None,
) -> CSourceIndex:
    """加载某 C 项目对应的源码 JSON，返回带索引的 CSourceIndex。

    查找顺序：
    1. explicit_path（CLI 显式传入的 ``--source-records`` 路径）
    2. ``<repo_root>/src/parse/res/<project_name>.json``（向后兼容旧行为）
    """
    if explicit_path:
        candidate = Path(explicit_path).expanduser().resolve()
        return _load_from_path(candidate, source_root=c_project_path)

    if not c_project_path:
        return CSourceIndex()

    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[3]
    candidate = repo_root / "src" / "parse" / "res" / f"{Path(c_project_path).name}.json"
    if not candidate.exists():
        print(f"[rtest] 未找到源码 JSON：{candidate}（修复时无法提供 C 源码上下文）")
        return CSourceIndex()
    return _load_from_path(candidate, source_root=c_project_path)


def _load_from_path(path: Path, source_root: str = "") -> CSourceIndex:
    if not path.exists():
        print(f"[rtest] 源码 JSON 不存在：{path}")
        return CSourceIndex()
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            payload = json.load(f)
    except Exception as exc:  # noqa: BLE001
        print(f"[rtest] 加载源码 JSON 失败：{exc}")
        return CSourceIndex()

    raw: List[Dict] = []
    if isinstance(payload, list):
        raw = [item for item in payload if isinstance(item, dict)]
    elif isinstance(payload, dict):
        for key in ("functions", "records", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                raw.extend(item for item in value if isinstance(item, dict))
        if not raw:
            raw = [payload]

    index = CSourceIndex(source_root=str(Path(source_root).resolve()) if source_root else "")
    for item in raw:
        func_defid = item.get("func_defid", "") or ""
        name = item.get("name") or (
            func_defid.rsplit(":", 1)[-1] if ":" in func_defid else ""
        )
        file_path = ""
        if ":" in func_defid:
            file_path = func_defid.rsplit(":", 1)[0]
        rec = {
            "name": name or "unknown",
            "file": file_path,
            "span": item.get("span", ""),
            "source": item.get("source", "") or "",
            "num_lines": item.get("num_lines")
            or len(str(item.get("source", "")).splitlines()),
            "func_defid": func_defid,
        }
        index.add(rec)
    return index


# --------- 展示 ---------


def build_source_index_display(index: CSourceIndex, max_items: int) -> str:
    if not index:
        return "(C project source index not loaded)"
    lines: List[str] = []
    for rec in index.records[:max_items]:
        lines.append(
            f"- `{rec.get('name')}` [{rec.get('file')}] "
            f"({rec.get('num_lines', '?')} lines)"
        )
    if len(index.records) > max_items:
        lines.append(
            f"... {len(index.records) - max_items} additional records are not expanded; "
            "request them through cgr_read by function name or file name"
        )
    return "\n".join(lines)

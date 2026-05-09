from __future__ import annotations

import os
import re
import json
from collections import Counter, defaultdict, deque
from typing import Any, Dict, List, Optional, Set, Tuple


class ModuleSplitter:
    """
    用于把大型 C 项目逐层拆分为：
    1. 候选模块
    2. 收敛后的子模块
    3. 更细粒度的函数簇

    输入数据假设：
    - project_info["c_files"]: List[str]
    - project_analysis["functions"]: List[Dict]
    - project_analysis["structs"]: List[Dict]
    - dependency_graph["call_graph"]: Dict[str, List[str]]
    - dependency_graph["struct_usage"]: Dict[str, List[str]]

    设计意图：
    - 不直接把“目录”当成最终模块，而是把目录当成第一层粗粒度线索
    - 先用目录得到候选模块，再结合调用关系、结构体使用情况和规模阈值做二次收敛
    - 对过大的模块，继续拆成更细的子模块；对仍然过大的子模块，再切到函数簇级别
    - 最终产出两层结果：
      1. module_units：更适合写 spec/plan/tasks 的模块单元
      2. cluster_units：更适合做局部分析或控制 prompt 大小的函数簇
    """

    # 以下阈值不是“语义真理”，而是为了控制模块大小和 prompt 尺寸的工程经验值。
    MAX_MODULE_FILES = 10
    MAX_MODULE_FUNCTIONS = 60
    MAX_CLUSTER_FUNCTIONS = 15
    MAX_CLUSTER_LINES = 700
    MAX_PLANNING_MODULE_FUNCTIONS = 24
    MIN_STRUCT_CLUSTER_SIZE = 2
    MIN_PREFIX_CLUSTER_SIZE = 2
    DEFAULT_LLM_CONTEXT_FUNCTIONS = 36
    DEFAULT_LLM_CONTEXT_CHARS = 9000
    DEFAULT_LLM_MAX_ROUNDS = 30
    MIN_LLM_ASSIGNED_FUNCTIONS = 1

    MODULE_CATEGORIES = {
        "main": ["root", "main", "entry", "start", "app"],
        "config": ["config", "conf", "option", "setting"],
        "parser": ["parser", "lexer", "scan", "token", "ast"],
        "io": ["io", "file", "net", "socket", "serial", "db"],
        "protocol": ["proto", "codec", "encode", "decode", "marshal"],
        "storage": ["storage", "store", "cache", "buffer", "pool"],
        "utils": ["utils", "util", "helper", "common", "base"],
        "cli": ["cli", "cmd", "command", "shell"],
        "log": ["log", "debug", "trace"],
        "error": ["error", "err", "fault"],
        "memory": ["mem", "alloc", "malloc"],
        "string": ["str", "string"],
    }

    RESPONSIBILITY_KEYWORDS = {
        "entry": ["main", "entry", "run", "start", "dispatch"],
        "configuration": ["config", "option", "setting", "parse_args", "getopt"],
        "input_output": ["read", "write", "open", "close", "flush", "file", "stream", "io"],
        "memory_management": ["alloc", "malloc", "free", "realloc", "buffer", "pool"],
        "data_structure": ["insert", "delete", "remove", "find", "search", "lookup", "tree", "list", "hash"],
        "serialization": ["encode", "decode", "parse", "format", "print", "scan"],
        "validation": ["check", "validate", "verify", "assert", "test"],
        "error_handling": ["error", "err", "die", "fail", "panic"],
        "string_processing": ["str", "string", "utf", "char", "mb", "quote"],
    }

    def __init__(
        self,
        llm: Any = None,
        use_llm: bool = True,
        llm_context_functions: int = DEFAULT_LLM_CONTEXT_FUNCTIONS,
        llm_context_chars: int = DEFAULT_LLM_CONTEXT_CHARS,
        llm_max_rounds: int = DEFAULT_LLM_MAX_ROUNDS,
    ):
        self.llm = llm
        self.use_llm = bool(use_llm)
        self.llm_context_functions = max(4, int(llm_context_functions or self.DEFAULT_LLM_CONTEXT_FUNCTIONS))
        self.llm_context_chars = max(2000, int(llm_context_chars or self.DEFAULT_LLM_CONTEXT_CHARS))
        self.llm_max_rounds = max(1, int(llm_max_rounds or self.DEFAULT_LLM_MAX_ROUNDS))

    def set_llm(self, llm: Any) -> None:
        self.llm = llm

    def _normalize_path(self, path: str) -> str:
        # 统一成 / 分隔、去掉前导 ./，避免 Windows / Linux 路径格式不一致导致匹配失败。
        return (path or "").replace("\\", "/").lstrip("./")

    def _parse_span(self, span: str) -> Tuple[str, int, int] | Tuple[str, None, None]:
        # parser 的 span 形如 file:start_line:start_col:end_line:end_col。
        # 这里把它拆回 split.py 需要的 file / start_line / end_line。
        if not span:
            return "", None, None

        match = re.match(r"^(.*):(\d+):(\d+):(\d+):(\d+)$", span)
        if not match:
            return "", None, None

        file_path = self._normalize_path(match.group(1))
        start_line = int(match.group(2))
        end_line = int(match.group(4))
        return file_path, start_line, end_line

    def _normalize_function_record(self, func: Dict) -> Dict:
        """
        兼容 parser 当前输出格式。

        split.py 的下游逻辑默认函数至少具备：
        - name
        - file
        - start_line / end_line
        - line_count

        但 c_ast.py 当前输出的是 func_defid / func_name / span / num_lines。
        这里统一转成 split.py 内部稳定使用的字段名，后面逻辑就不需要到处兼容两套 schema。
        """
        normalized = dict(func)

        if not normalized.get("name"):
            normalized["name"] = (
                normalized.get("func_name")
                or normalized.get("func_defid", "").rsplit(":", 1)[-1]
            )

        if not normalized.get("file"):
            span_file, span_start, span_end = self._parse_span(normalized.get("span", ""))
            normalized["file"] = (
                normalized.get("filename")
                or span_file
                or normalized.get("func_defid", "").rsplit(":", 1)[0]
            )
            if not normalized.get("start_line") and span_start is not None:
                normalized["start_line"] = span_start
            if not normalized.get("end_line") and span_end is not None:
                normalized["end_line"] = span_end

        if not normalized.get("start_line"):
            normalized["start_line"] = normalized.get("startLine", 0)
        if not normalized.get("end_line"):
            normalized["end_line"] = normalized.get("endLine", 0)
        if not normalized.get("line_count"):
            normalized["line_count"] = normalized.get("num_lines", 0)

        normalized["file"] = self._normalize_path(normalized.get("file", ""))
        normalized["id"] = self._function_uid(normalized)
        return normalized

    def _normalize_struct_record(self, struct: Dict) -> Dict:
        # 结构体记录同样做一次 schema 兼容，重点补齐 file 字段。
        normalized = dict(struct)
        if not normalized.get("file"):
            span_file, _, _ = self._parse_span(normalized.get("span", ""))
            normalized["file"] = normalized.get("filename") or span_file
        normalized["file"] = self._normalize_path(normalized.get("file", ""))
        return normalized

    def _tokenize_path(self, path: str) -> List[str]:
        # 把目录名拆成 token，后面用于做模块类别启发式判断。
        normalized = self._normalize_path(path).lower()
        if not normalized:
            return ["root"]
        tokens = [token for token in re.split(r"[\/_.\-]+", normalized) if token]
        return tokens or ["root"]

    def _extract_function_prefix(self, func_name: str) -> str:
        # 使用前两个下划线片段做 prefix，例如：
        # parse_config_load -> parse_config
        # uart_send -> uart_send
        # 这个粒度比只看第一个单词更稳定，更适合做函数簇收尾聚类。
        if not func_name:
            return "unknown"
        parts = [part for part in func_name.split("_") if part]
        if len(parts) >= 2:
            return "_".join(parts[:2])
        return parts[0] if parts else "unknown"

    def _safe_line_count(self, func: Dict) -> int:
        # 优先信任 start/end 行号；没有时退回 line_count。
        start = int(func.get("start_line", 0) or 0)
        end = int(func.get("end_line", 0) or 0)
        if start > 0 and end >= start:
            return end - start + 1
        return int(func.get("line_count", 1) or 1)

    def _function_sort_key(self, func: Dict) -> Tuple[str, int, str]:
        file_path = self._normalize_path(func.get("file", ""))
        start_line = int(func.get("start_line", 0) or 0)
        name = func.get("name", "")
        return (file_path, start_line, name)

    def _function_uid(self, func: Dict) -> str:
        # 函数名在 C 项目里不一定全局唯一，例如多个测试/示例文件都可能定义 main。
        # 模块覆盖和 LLM 分配必须使用稳定 ID，而不是裸函数名。
        existing = func.get("id") or func.get("func_defid")
        if existing:
            return self._normalize_path(str(existing))
        file_path = self._normalize_path(func.get("file", "unknown"))
        name = func.get("name") or func.get("func_name") or "unknown"
        return f"{file_path}:{name}"

    def _collapse_whitespace(self, text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").strip())

    def _truncate_text(self, text: str, max_chars: int) -> str:
        text = text or ""
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3].rstrip() + "..."

    def _extract_signature_excerpt(self, func: Dict, max_chars: int = 180) -> str:
        """
        给 LLM 的代码证据只保留声明/签名，不包含函数体。

        这可以让模块划分使用语义线索，同时避免把整个 C 项目一次性塞进上下文。
        """
        source = func.get("source", "") or ""
        if source:
            signature = source.split("{", 1)[0].strip()
            if not signature:
                signature = source.strip().splitlines()[0] if source.strip() else ""
            signature = self._collapse_whitespace(signature)
            return self._truncate_text(signature, max_chars)
        return func.get("name", "unknown")

    def _normalize_call_graph(self, call_graph: Dict[str, List[str]]) -> Dict[str, List[str]]:
        normalized = {}
        for caller, callees in (call_graph or {}).items():
            if not caller:
                continue
            normalized[caller] = sorted({callee for callee in callees if callee})
        return normalized

    def _build_reverse_call_graph(self, call_graph: Dict[str, List[str]]) -> Dict[str, List[str]]:
        reverse = defaultdict(list)
        for caller, callees in (call_graph or {}).items():
            for callee in callees:
                if caller and callee:
                    reverse[callee].append(caller)
        return {name: sorted(set(callers)) for name, callers in reverse.items()}

    def _build_name_to_function_ids(self, functions: List[Dict]) -> Dict[str, List[str]]:
        result = defaultdict(list)
        for func in functions:
            name = func.get("name")
            if name:
                result[name].append(self._function_uid(func))
        return {name: sorted(set(ids)) for name, ids in result.items()}

    def _resolve_call_targets(
        self,
        symbol: str,
        name_to_ids: Dict[str, List[str]],
        function_ids: Set[str],
    ) -> List[str]:
        if not symbol:
            return []
        normalized = self._normalize_path(symbol)
        if normalized in function_ids:
            return [normalized]
        return name_to_ids.get(symbol, [])

    def _normalize_call_graph_to_ids(
        self,
        call_graph: Dict[str, List[str]],
        name_to_ids: Dict[str, List[str]],
        function_ids: Set[str],
    ) -> Dict[str, List[str]]:
        normalized_by_id = defaultdict(set)
        for raw_caller, raw_callees in self._normalize_call_graph(call_graph).items():
            caller_ids = self._resolve_call_targets(raw_caller, name_to_ids, function_ids)
            if not caller_ids:
                continue
            for raw_callee in raw_callees:
                callee_ids = self._resolve_call_targets(raw_callee, name_to_ids, function_ids)
                for caller_id in caller_ids:
                    for callee_id in callee_ids:
                        if caller_id != callee_id:
                            normalized_by_id[caller_id].add(callee_id)
        return {caller_id: sorted(callees) for caller_id, callees in normalized_by_id.items()}

    def _normalize_struct_usage_to_ids(
        self,
        struct_usage: Dict[str, List[str]],
        name_to_ids: Dict[str, List[str]],
        function_ids: Set[str],
    ) -> Dict[str, List[str]]:
        normalized = defaultdict(set)
        for raw_func, structs in (struct_usage or {}).items():
            func_ids = self._resolve_call_targets(raw_func, name_to_ids, function_ids)
            for func_id in func_ids:
                for struct_name in structs:
                    if struct_name:
                        normalized[func_id].add(struct_name)
        return {func_id: sorted(structs) for func_id, structs in normalized.items()}

    def _safe_module_name(self, value: str, fallback: str = "module") -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", (value or "").strip().lower())
        cleaned = re.sub(r"_+", "_", cleaned).strip("_")
        return cleaned or fallback

    def _dedupe_module_names(self, modules: List[Dict]) -> List[Dict]:
        seen = Counter()
        deduped = []
        for module in modules:
            updated = dict(module)
            base_name = self._safe_module_name(updated.get("name", ""), "module")
            seen[base_name] += 1
            if seen[base_name] > 1:
                updated["name"] = f"{base_name}_{seen[base_name]:02d}"
            else:
                updated["name"] = base_name
            deduped.append(updated)
        return deduped

    def _function_tokens(self, func: Dict) -> Set[str]:
        text = " ".join(
            [
                func.get("name", ""),
                func.get("file", ""),
                self._extract_signature_excerpt(func, 160),
            ]
        ).lower()
        return {token for token in re.split(r"[^a-zA-Z0-9_]+", text) if token}

    def _build_function_index(
        self,
        functions: List[Dict],
        dependency_graph: Dict,
    ) -> Dict[str, Dict]:
        name_to_ids = self._build_name_to_function_ids(functions)
        function_ids = {self._function_uid(func) for func in functions}
        call_graph = self._normalize_call_graph_to_ids(
            dependency_graph.get("call_graph", {}),
            name_to_ids,
            function_ids,
        )
        reverse_call_graph = self._build_reverse_call_graph(call_graph)
        struct_usage = self._normalize_struct_usage_to_ids(
            dependency_graph.get("struct_usage", {}),
            name_to_ids,
            function_ids,
        )

        index = {}
        for func in functions:
            name = func.get("name")
            if not name:
                continue
            func_id = self._function_uid(func)
            file_path = self._normalize_path(func.get("file", ""))
            index[func_id] = {
                "id": func_id,
                "name": name,
                "file": file_path,
                "start_line": int(func.get("start_line", 0) or 0),
                "end_line": int(func.get("end_line", 0) or 0),
                "line_count": self._safe_line_count(func),
                "signature": self._extract_signature_excerpt(func),
                "callees": [callee for callee in call_graph.get(func_id, []) if callee],
                "callers": [caller for caller in reverse_call_graph.get(func_id, []) if caller],
                "structs": sorted(set(struct_usage.get(func_id, []))),
                "tokens": self._function_tokens(func),
                "record": func,
            }
        return index

    def _initial_llm_queries(
        self,
        project_info: Dict,
        function_index: Dict[str, Dict],
        call_graph: Dict[str, List[str]],
    ) -> List[str]:
        queries = []

        for file_path in project_info.get("entry_files", []):
            normalized = self._normalize_path(file_path)
            for item in function_index.values():
                if item["file"] == normalized:
                    queries.append(item["id"])

        for func_id, item in function_index.items():
            lowered = item["name"].lower()
            if lowered == "main" or lowered.endswith("_main") or "entry" in lowered:
                queries.append(func_id)

        degree = Counter()
        reverse = self._build_reverse_call_graph(call_graph)
        for func_id in function_index:
            degree[func_id] = len(call_graph.get(func_id, [])) + len(reverse.get(func_id, []))
        for func_id, _ in degree.most_common(8):
            queries.append(func_id)

        by_dir = defaultdict(list)
        for func_id, item in function_index.items():
            by_dir[os.path.dirname(item["file"]) or "root"].append(func_id)
        for _, ids in sorted(by_dir.items()):
            if ids:
                queries.append(sorted(ids, key=lambda func_id: function_index[func_id]["name"])[0])

        return self._dedupe_keep_order(queries)

    def _dedupe_keep_order(self, values: List[str]) -> List[str]:
        seen = set()
        result = []
        for value in values:
            if value and value not in seen:
                seen.add(value)
                result.append(value)
        return result

    def _search_functions_for_query(
        self,
        query: str,
        function_index: Dict[str, Dict],
        unassigned: Set[str],
    ) -> List[str]:
        query = (query or "").strip()
        if not query:
            return []

        normalized_query = query.lower()
        query_tokens = {token for token in re.split(r"[^a-zA-Z0-9_]+", normalized_query) if token}

        scored = []
        for func_id, item in function_index.items():
            score = 0
            lowered_id = func_id.lower()
            lowered_name = item["name"].lower()
            lowered_file = item["file"].lower()

            if func_id in unassigned:
                score += 3
            if normalized_query == lowered_id:
                score += 12
            if normalized_query == lowered_name:
                score += 10
            if normalized_query in lowered_name:
                score += 6
            if normalized_query and normalized_query in lowered_id:
                score += 5
            if normalized_query and normalized_query in lowered_file:
                score += 4
            if query_tokens:
                score += len(query_tokens & item["tokens"])
            if any(normalized_query == struct.lower() for struct in item.get("structs", [])):
                score += 5

            if score > 0:
                scored.append((score, item["file"], item["start_line"], func_id))

        scored.sort(key=lambda item: (-item[0], item[1], item[2], item[3]))
        return [func_id for _, _, _, func_id in scored[: max(1, self.llm_context_functions // 3)]]

    def _related_functions(
        self,
        func_id: str,
        function_index: Dict[str, Dict],
        call_graph: Dict[str, List[str]],
        reverse_call_graph: Dict[str, List[str]],
        struct_to_functions: Dict[str, List[str]],
    ) -> List[str]:
        if func_id not in function_index:
            return []

        item = function_index[func_id]
        related = []
        related.extend(call_graph.get(func_id, []))
        related.extend(reverse_call_graph.get(func_id, []))

        same_file = [
            other_name
            for other_name, other in function_index.items()
            if other_name != func_id and other["file"] == item["file"]
        ]
        same_file.sort(key=lambda other_name: (
            abs(function_index[other_name]["start_line"] - item["start_line"]),
            other_name,
        ))
        related.extend(same_file[:6])

        for struct_name in item.get("structs", []):
            related.extend(struct_to_functions.get(struct_name, [])[:8])

        return [candidate for candidate in self._dedupe_keep_order(related) if candidate in function_index and candidate != func_id]

    def _build_struct_to_functions(self, struct_usage: Dict[str, List[str]]) -> Dict[str, List[str]]:
        result = defaultdict(list)
        for func_name, struct_names in (struct_usage or {}).items():
            for struct_name in struct_names:
                if struct_name:
                    result[struct_name].append(func_name)
        return {name: sorted(set(funcs)) for name, funcs in result.items()}

    def _expand_llm_context(
        self,
        seed_query: str,
        function_index: Dict[str, Dict],
        call_graph: Dict[str, List[str]],
        reverse_call_graph: Dict[str, List[str]],
        struct_to_functions: Dict[str, List[str]],
        unassigned: Set[str],
    ) -> List[str]:
        seeds = self._search_functions_for_query(seed_query, function_index, unassigned)
        if not seeds:
            seeds = [seed_query] if seed_query in function_index else []
        if not seeds and unassigned:
            seeds = [sorted(unassigned, key=lambda name: (
                function_index[name]["file"],
                function_index[name]["start_line"],
                name,
            ))[0]]

        context = []
        seen = set()
        queue = deque(seeds)

        while queue and len(context) < self.llm_context_functions:
            name = queue.popleft()
            if name not in function_index or name in seen:
                continue
            seen.add(name)
            context.append(name)

            for related in self._related_functions(
                name,
                function_index,
                call_graph,
                reverse_call_graph,
                struct_to_functions,
            ):
                if related not in seen:
                    queue.append(related)

        context.sort(key=lambda name: (
            0 if name in unassigned else 1,
            function_index[name]["file"],
            function_index[name]["start_line"],
            name,
        ))
        return context[: self.llm_context_functions]

    def _format_function_fact_for_llm(self, item: Dict, assignable: bool) -> str:
        relations = []
        if item.get("callers"):
            relations.append("callers=" + ",".join(item["callers"][:8]))
        if item.get("callees"):
            relations.append("callees=" + ",".join(item["callees"][:8]))
        if item.get("structs"):
            relations.append("structs=" + ",".join(item["structs"][:6]))
        relation_text = "; ".join(relations) if relations else "relations=none"
        status = "assignable" if assignable else "context_only"
        location = f"{item['file']}:{item['start_line']}-{item['end_line']}"
        return f"- id={item['id']}; name={item['name']} [{status}] {location}; {relation_text}; signature={item['signature']}"

    def _build_llm_partition_prompt(
        self,
        project_info: Dict,
        seed_query: str,
        visible_names: List[str],
        assigned_names: Set[str],
        function_index: Dict[str, Dict],
    ) -> List[Dict[str, str]]:
        assignable_names = [name for name in visible_names if name not in assigned_names]
        facts = []
        current_size = 0
        for name in visible_names:
            fact = self._format_function_fact_for_llm(function_index[name], name in assignable_names)
            if facts and current_size + len(fact) + 1 > self.llm_context_chars:
                break
            facts.append(fact)
            current_size += len(fact) + 1

        directories = sorted({
            os.path.dirname(function_index[name]["file"]) or "root"
            for name in visible_names
        })
        system_prompt = (
            "你是 C 项目模块划分助手。你只能基于当前可见的静态分析事实划分模块，"
            "不能假设未给出的源码。输出必须是严格 JSON，不要 markdown。"
        )
        user_prompt = {
            "task": "partition_visible_c_functions",
            "project": project_info.get("project_name", "project"),
            "seed_query": seed_query,
            "visible_directories": directories,
            "assignable_function_ids": assignable_names,
            "assignable_function_names": [
                {
                    "id": func_id,
                    "name": function_index[func_id]["name"],
                    "file": function_index[func_id]["file"],
                }
                for func_id in assignable_names
            ],
            "rules": [
                "优先把 assignable_function_ids 中的 id 放入 modules[].function_ids",
                "如果输出 modules[].function_names，只能使用 assignable_function_names 中对应的 name；重名函数必须用 function_ids",
                "如果需要更多上下文，用 expand_queries 写查询词，例如文件名、函数名前缀、结构体名或被调函数名",
                "模块名使用 snake_case 英文，按职责命名，不按目录机械命名",
                "files 只能写当前函数事实中出现过的真实相对路径",
                "不要输出源码，不要输出解释，只输出 JSON",
            ],
            "expected_schema": {
                "modules": [
                    {
                        "name": "module_name",
                        "description": "one sentence responsibility",
                        "function_ids": ["relative/file.c:function_a"],
                        "function_names": ["function_a"],
                        "files": ["relative/path.c"],
                        "expand_queries": ["related prefix or symbol"],
                        "confidence": "high|medium|low",
                    }
                ],
                "next_queries": ["symbol or file to inspect next"],
            },
            "function_facts": facts,
        }
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_prompt, ensure_ascii=False, indent=2)},
        ]

    def _call_llm_for_partition(self, messages: List[Dict[str, str]], round_index: int) -> Optional[Dict]:
        if not self.llm:
            return None
        try:
            if hasattr(self.llm, "set_request_label"):
                self.llm.set_request_label(f"ModuleSplitter 动态模块划分 [round {round_index}]")
            response = self.llm.generate(messages)
            if isinstance(response, list):
                response_text = response[0] if response else ""
            else:
                response_text = str(response or "")
            return self._parse_llm_json(response_text)
        except Exception as exc:
            print(f"  WARN LLM 模块划分失败，回退到静态划分：{type(exc).__name__}: {exc}")
            return None

    def _parse_llm_json(self, response_text: str) -> Optional[Dict]:
        text = (response_text or "").strip()
        if not text:
            return None

        fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
        if fenced:
            text = fenced.group(1).strip()

        if not text.startswith("{"):
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                text = text[start:end + 1]

        try:
            parsed = json.loads(text)
        except Exception:
            return None

        return parsed if isinstance(parsed, dict) else None

    def _validate_llm_modules(
        self,
        parsed: Dict,
        visible_names: Set[str],
        assigned_names: Set[str],
        function_index: Dict[str, Dict],
    ) -> Tuple[List[Dict], List[str]]:
        modules = []
        next_queries = []

        raw_modules = parsed.get("modules", []) if isinstance(parsed, dict) else []
        if not isinstance(raw_modules, list):
            raw_modules = []

        for raw_module in raw_modules:
            if not isinstance(raw_module, dict):
                continue
            valid_ids = self._resolve_llm_function_refs(
                raw_module,
                visible_names,
                assigned_names,
                function_index,
            )
            if not valid_ids:
                continue

            module_name = self._safe_module_name(raw_module.get("name", ""), "llm_module")
            modules.append(
                {
                    "name": module_name,
                    "description": self._truncate_text(str(raw_module.get("description", "") or ""), 500),
                    "function_ids": valid_ids,
                    "function_names": [function_index[func_id]["name"] for func_id in valid_ids],
                    "files": [
                        self._normalize_path(path)
                        for path in raw_module.get("files", [])
                        if isinstance(path, str) and path.strip()
                    ],
                    "expand_queries": [
                        str(query).strip()
                        for query in raw_module.get("expand_queries", [])
                        if str(query).strip()
                    ][:8],
                    "confidence": raw_module.get("confidence", "medium") if raw_module.get("confidence") in {"high", "medium", "low"} else "medium",
                }
            )

        raw_next_queries = parsed.get("next_queries", []) if isinstance(parsed, dict) else []
        if isinstance(raw_next_queries, list):
            next_queries = [str(query).strip() for query in raw_next_queries if str(query).strip()][:12]

        return modules, next_queries

    def _resolve_llm_function_refs(
        self,
        raw_module: Dict,
        visible_ids: Set[str],
        assigned_ids: Set[str],
        function_index: Dict[str, Dict],
    ) -> List[str]:
        valid_ids = []

        raw_ids = raw_module.get("function_ids", [])
        if isinstance(raw_ids, list):
            for raw_id in raw_ids:
                if not isinstance(raw_id, str):
                    continue
                func_id = self._normalize_path(raw_id)
                if func_id in visible_ids and func_id not in assigned_ids:
                    valid_ids.append(func_id)

        raw_names = raw_module.get("function_names", [])
        if isinstance(raw_names, list):
            for raw_name in raw_names:
                if not isinstance(raw_name, str):
                    continue
                candidates = [
                    func_id
                    for func_id in visible_ids
                    if func_id not in assigned_ids
                    and function_index[func_id]["name"] == raw_name
                ]
                if len(candidates) == 1:
                    valid_ids.append(candidates[0])
                else:
                    raw_files = {
                        self._normalize_path(path)
                        for path in raw_module.get("files", [])
                        if isinstance(path, str)
                    }
                    file_matched = [
                        func_id
                        for func_id in candidates
                        if function_index[func_id]["file"] in raw_files
                    ]
                    if len(file_matched) == 1:
                        valid_ids.append(file_matched[0])

        return self._dedupe_keep_order(valid_ids)

    def _function_records_by_id(self, functions: List[Dict]) -> Dict[str, Dict]:
        return {self._function_uid(func): func for func in functions if func.get("name")}

    def _candidate_headers_for_files(self, files: Set[str], project_info: Dict, dependency_graph: Dict) -> List[str]:
        headers = set(self._collect_file_headers(files))
        all_headers = [self._normalize_path(path) for path in project_info.get("h_files", [])]
        include_graph = dependency_graph.get("include_graph", {})

        for file_path in files:
            file_dir = os.path.dirname(file_path) or "root"
            stem = os.path.splitext(os.path.basename(file_path))[0]
            for header in all_headers:
                header_dir = os.path.dirname(header) or "root"
                header_stem = os.path.splitext(os.path.basename(header))[0]
                if header_dir == file_dir and header_stem == stem:
                    headers.add(header)
            for include_name in include_graph.get(file_path, []):
                include_base = os.path.basename(self._normalize_path(include_name))
                matches = [header for header in all_headers if os.path.basename(header) == include_base]
                if len(matches) == 1:
                    headers.add(matches[0])
        return sorted(headers)

    def _project_file_set(self, project_info: Dict) -> Set[str]:
        return {
            self._normalize_path(path)
            for key in ("c_files", "h_files", "other_files")
            for path in project_info.get(key, [])
            if path
        }

    def _is_absolute_like_path(self, path: str) -> bool:
        normalized = path.replace("\\", "/")
        return bool(re.match(r"^[A-Za-z]:/", normalized) or normalized.startswith("/"))

    def _resolve_project_file(self, raw_path: str, project_files: Set[str]) -> str:
        normalized = self._normalize_path(raw_path)
        if not normalized:
            return ""
        if normalized in project_files:
            return normalized
        if self._is_absolute_like_path(raw_path):
            matches = [path for path in project_files if normalized.endswith("/" + path)]
            if len(matches) == 1:
                return matches[0]
        return ""

    def _collect_module_structs_from_functions(
        self,
        files: Set[str],
        func_names: List[str],
        structs: List[Dict],
        struct_usage: Dict[str, List[str]],
    ) -> List[str]:
        names = set()
        for struct in structs:
            if self._normalize_path(struct.get("file", "")) in files:
                name = struct.get("name") or struct.get("struct_name")
                if name:
                    names.add(name)
        for func_name in func_names:
            names.update(struct_usage.get(func_name, []))
        return sorted(names)

    def _materialize_llm_module(
        self,
        spec: Dict,
        functions_by_id: Dict[str, Dict],
        structs: List[Dict],
        project_info: Dict,
        dependency_graph: Dict,
        index: int,
        fallback: bool = False,
    ) -> Dict:
        funcs = [
            functions_by_id[func_id]
            for func_id in spec.get("function_ids", [])
            if func_id in functions_by_id
        ]
        funcs.sort(key=self._function_sort_key)
        func_names = [func.get("name") for func in funcs if func.get("name")]
        files = {self._normalize_path(func.get("file", "")) for func in funcs if func.get("file")}
        project_files = self._project_file_set(project_info)
        for file_path in spec.get("files", []):
            resolved_file = self._resolve_project_file(file_path, project_files)
            if resolved_file:
                files.add(resolved_file)
        files = {file_path for file_path in files if file_path}
        category, category_confidence = self._resolve_module_category(spec.get("name", ""))
        if category == "module":
            category, _ = self._resolve_module_category(" ".join(sorted(files)))

        if files:
            try:
                common_dir = os.path.commonpath([path.replace("/", os.sep) for path in files]).replace("\\", "/")
            except ValueError:
                common_dir = "root"
            if not common_dir or common_dir.strip() == ".":
                common_dir = os.path.dirname(sorted(files)[0]) or "root"
        else:
            common_dir = "root"

        return {
            "name": spec.get("name") or f"module_{index:02d}",
            "category": category,
            "directory": common_dir,
            "files": sorted(files),
            "functions": funcs,
            "structs": self._collect_module_structs_from_functions(
                files,
                func_names,
                structs,
                dependency_graph.get("struct_usage", {}),
            ),
            "headers": self._candidate_headers_for_files(files, project_info, dependency_graph),
            "llm_guided": not fallback,
            "llm_description": spec.get("description", ""),
            "llm_expand_queries": spec.get("expand_queries", []),
            "confidence": spec.get("confidence") or category_confidence,
            "function_count": len(funcs),
            "total_lines": sum(self._safe_line_count(func) for func in funcs),
            "coverage_method": "static_fallback" if fallback else "llm_dynamic_expansion",
        }

    def _fallback_specs_for_unassigned(
        self,
        unassigned: Set[str],
        static_modules: List[Dict],
        dependency_graph: Dict,
    ) -> List[Dict]:
        fallback_specs = []
        spec_index = 1
        for module in static_modules:
            funcs = [
                func
                for func in module.get("functions", [])
                if self._function_uid(func) in unassigned
            ]
            if not funcs:
                continue

            for chunk in self._fallback_chunks_from_functions(funcs, dependency_graph):
                ids = [self._function_uid(func) for func in chunk["functions"]]
                fallback_specs.append(
                    {
                        "name": f"{module.get('name', 'module')}_{chunk['cluster_key']}_fallback_{spec_index:02d}",
                        "description": "Static fallback for functions not assigned by LLM dynamic expansion.",
                        "function_ids": sorted(ids),
                        "function_names": sorted({func.get("name") for func in chunk["functions"] if func.get("name")}),
                        "files": sorted({self._normalize_path(func.get("file", "")) for func in chunk["functions"] if func.get("file")}),
                        "expand_queries": [],
                        "confidence": "medium",
                    }
                )
                spec_index += 1
        return fallback_specs

    def _fallback_chunks_from_functions(self, funcs: List[Dict], dependency_graph: Dict) -> List[Dict]:
        chunks = []
        funcs = sorted(funcs, key=self._function_sort_key)
        grouped_by_file = self._group_functions_by_file(funcs)

        for file_path, file_funcs in sorted(grouped_by_file.items()):
            graph_chunks = self._fallback_call_component_chunks(file_funcs, dependency_graph)
            chunks.extend(graph_chunks)
            claimed_ids = {
                self._function_uid(func)
                for chunk in graph_chunks
                for func in chunk["functions"]
            }
            remaining_after_graph = [
                func for func in file_funcs if self._function_uid(func) not in claimed_ids
            ]

            prefix_groups = defaultdict(list)
            for func in remaining_after_graph:
                prefix_groups[self._extract_function_prefix(func.get("name", ""))].append(func)

            for prefix, prefix_funcs in sorted(prefix_groups.items(), key=lambda item: (-len(item[1]), item[0])):
                if len(prefix_funcs) < self.MIN_PREFIX_CLUSTER_SIZE:
                    continue
                for chunk in self._chunk_large_group(prefix_funcs, "fallback_prefix", prefix):
                    chunks.append(chunk)
                claimed_ids.update(self._function_uid(func) for func in prefix_funcs)

            remaining = [func for func in remaining_after_graph if self._function_uid(func) not in claimed_ids]
            for chunk in self._chunk_large_group(
                remaining,
                "fallback_file",
                os.path.splitext(os.path.basename(file_path))[0] or "file",
            ):
                chunks.append(chunk)

        return chunks

    def _fallback_call_component_chunks(self, funcs: List[Dict], dependency_graph: Dict) -> List[Dict]:
        if len(funcs) < 2:
            return []

        name_to_ids = self._build_name_to_function_ids(funcs)
        function_ids = {self._function_uid(func) for func in funcs}
        call_graph = self._normalize_call_graph_to_ids(
            dependency_graph.get("call_graph", {}),
            name_to_ids,
            function_ids,
        )
        adjacency = defaultdict(set)
        for caller_id, callees in call_graph.items():
            if caller_id not in function_ids:
                continue
            for callee_id in callees:
                if callee_id not in function_ids:
                    continue
                adjacency[caller_id].add(callee_id)
                adjacency[callee_id].add(caller_id)

        chunks = []
        visited = set()
        funcs_by_id = {self._function_uid(func): func for func in funcs}
        for func_id in sorted(function_ids):
            if func_id in visited or not adjacency.get(func_id):
                continue

            stack = [func_id]
            component = []
            visited.add(func_id)
            while stack:
                current = stack.pop()
                component.append(current)
                for neighbor in sorted(adjacency.get(current, [])):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        stack.append(neighbor)

            if len(component) < 2:
                continue
            component_funcs = [funcs_by_id[item] for item in component if item in funcs_by_id]
            key = self._fallback_component_key(component_funcs)
            for chunk in self._chunk_large_group(component_funcs, "fallback_call_component", key):
                chunks.append(chunk)

        return chunks

    def _fallback_component_key(self, funcs: List[Dict]) -> str:
        prefixes = [
            self._extract_function_prefix(func.get("name", ""))
            for func in funcs
            if func.get("name")
        ]
        prefix_counts = Counter(prefixes)
        for prefix, count in prefix_counts.most_common():
            if prefix != "unknown" and count >= 2:
                return prefix
        file_path = self._normalize_path(funcs[0].get("file", "")) if funcs else ""
        return os.path.splitext(os.path.basename(file_path))[0] or "component"

    def _module_name_tokens(self, module: Dict) -> Set[str]:
        stopwords = {
            "core", "api", "module", "helper", "helpers", "internal", "tree",
            "test", "tests", "and", "the", "logic",
        }
        text_parts = [module.get("name", "")]
        text_parts.extend(func.get("name", "") for func in module.get("functions", []))
        tokens = {
            token
            for text in text_parts
            for token in re.split(r"[^A-Za-z0-9]+|_", (text or "").lower())
            if token and token not in stopwords and len(token) > 1
        }
        return tokens

    def _module_function_prefixes(self, module: Dict) -> Set[str]:
        return {
            prefix
            for prefix in (
                self._extract_function_prefix(func.get("name", ""))
                for func in module.get("functions", [])
            )
            if prefix and prefix != "unknown" and not self._is_low_signal_function_prefix(prefix)
        }

    def _module_function_names(self, module: Dict) -> List[str]:
        return [func.get("name", "") for func in module.get("functions", []) if func.get("name")]

    def _is_low_signal_function_prefix(self, prefix: str) -> bool:
        normalized = (prefix or "").lower()
        return normalized in {"test", "tests", "unit_test", "unit_tests"}

    def _split_semantic_tokens(self, text: str) -> Set[str]:
        if not text:
            return set()
        # Split both snake_case and camelCase identifiers so names like sdsMakeRoomFor
        # contribute real semantic terms instead of one opaque token.
        spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
        spaced = re.sub(r"([A-Za-z])([0-9])", r"\1 \2", spaced)
        spaced = re.sub(r"([0-9])([A-Za-z])", r"\1 \2", spaced)
        raw_tokens = re.split(r"[^A-Za-z0-9]+|_", spaced.lower())
        generic_tokens = {
            "api", "apis", "c", "code", "core", "dynamic", "general", "helper",
            "helpers", "internal", "logic", "management", "manager", "memory",
            "module", "modules", "operation", "operations", "result", "results",
            "string", "strings", "the", "unit", "test", "tests", "wrapper",
            "wrappers",
        }
        tokens = set()
        for token in raw_tokens:
            if len(token) <= 1 or token in generic_tokens:
                continue
            token = self._normalize_semantic_token(token)
            if not token or token in generic_tokens:
                continue
            if token.endswith("ies") and len(token) > 4:
                token = token[:-3] + "y"
            elif token.endswith("s") and len(token) > 4:
                token = token[:-1]
            tokens.add(token)
        return tokens

    def _normalize_semantic_token(self, token: str) -> str:
        aliases = {
            "arg": "argument",
            "args": "argument",
            "alloc": "alloc",
            "allocation": "alloc",
            "buf": "buffer",
            "cfg": "config",
            "cmp": "compare",
            "hdr": "header",
            "incr": "length",
            "len": "length",
            "ptr": "pointer",
            "repr": "representation",
            "req": "required",
            "res": "result",
            "room": "capacity",
            "space": "capacity",
            "str": "string",
        }
        return aliases.get((token or "").lower(), (token or "").lower())

    def _identifier_action_tokens(self, text: str) -> Set[str]:
        lowered = (text or "").lower()
        action_terms = [
            "alloc", "append", "arg", "buffer", "case", "char", "clear", "copy",
            "decode", "delete", "destroy", "digit", "encode", "find", "format",
            "cat", "free", "grow", "header", "hex", "incr", "insert", "join", "len", "lower",
            "map", "parse", "print", "range", "remove", "resize", "room", "search",
            "shrink", "split", "trim", "upper", "validate", "write",
        ]
        return {
            self._normalize_semantic_token(term)
            for term in action_terms
            if term in lowered
        }

    def _share_action_family(self, left_tokens: Set[str], right_tokens: Set[str]) -> bool:
        action_families = [
            {"alloc", "free", "destroy", "clear"},
            {"append", "cat", "copy", "format", "write"},
            {"argument", "digit", "hex", "parse", "split"},
            {"buffer", "capacity", "grow", "length", "resize", "shrink"},
            {"case", "char", "lower", "map", "range", "trim", "upper"},
            {"compare", "find", "search"},
            {"header", "metadata", "pointer", "required", "size", "type"},
            {"insert", "delete", "remove"},
            {"join", "split"},
        ]
        return any(left_tokens & family and right_tokens & family for family in action_families)

    def _function_semantic_tokens(
        self,
        func: Dict,
        common_tokens: Optional[Set[str]] = None,
    ) -> Set[str]:
        name = func.get("name", "")
        text = " ".join([name, self._extract_signature_excerpt(func, 240)])
        tokens = self._split_semantic_tokens(text)
        tokens.update(self._identifier_action_tokens(name))
        if common_tokens:
            tokens -= common_tokens
        return tokens

    def _common_project_semantic_tokens(self, functions: List[Dict]) -> Set[str]:
        if not functions:
            return set()
        counter = Counter()
        for func in functions:
            counter.update(self._function_semantic_tokens(func))
        threshold = max(4, int(len(functions) * 0.35))
        return {token for token, count in counter.items() if count >= threshold}

    def _module_semantic_tokens(self, module: Dict) -> Set[str]:
        text_parts = [
            module.get("name", ""),
            module.get("llm_description", ""),
            module.get("description", ""),
        ]
        return {
            token
            for text in text_parts
            for token in self._split_semantic_tokens(text or "")
        }

    def _longest_common_prefix_len(self, left: str, right: str) -> int:
        count = 0
        for left_char, right_char in zip(left, right):
            if left_char != right_char:
                break
            count += 1
        return count

    def _has_strong_function_name_prefix_similarity(self, target: Dict, source: Dict) -> bool:
        for left in self._module_function_names(target):
            for right in self._module_function_names(source):
                if left == right:
                    continue
                common_len = self._longest_common_prefix_len(left.lower(), right.lower())
                shorter = max(1, min(len(left), len(right)))
                if common_len >= 5 and common_len / shorter >= 0.5:
                    return True
        return False

    def _is_entry_like_module(self, module: Dict) -> bool:
        name_tokens = self._module_name_tokens(module)
        if {"entry", "runner", "harness", "main"} & name_tokens:
            return True
        return any((func.get("name") or "").lower() == "main" for func in module.get("functions", []))

    def _is_test_like_module(self, module: Dict) -> bool:
        text = " ".join([module.get("name", "")] + module.get("files", [])).lower()
        return bool(re.search(r"(^|[^a-z0-9])(test|tests|unit|fixture|spec)([^a-z0-9]|$)", text))

    def _merge_module_pair(self, target: Dict, source: Dict) -> Dict:
        merged = dict(target)
        functions_by_id = {
            self._function_uid(func): func
            for func in target.get("functions", []) + source.get("functions", [])
        }
        merged["functions"] = sorted(functions_by_id.values(), key=self._function_sort_key)
        merged["files"] = sorted(set(target.get("files", [])) | set(source.get("files", [])))
        merged["headers"] = sorted(set(target.get("headers", [])) | set(source.get("headers", [])))
        merged["structs"] = sorted(set(target.get("structs", [])) | set(source.get("structs", [])))
        merged["function_count"] = len(merged["functions"])
        merged["total_lines"] = sum(self._safe_line_count(func) for func in merged["functions"])
        descriptions = [
            text
            for text in [target.get("llm_description", ""), source.get("llm_description", "")]
            if text
        ]
        if descriptions:
            merged["llm_description"] = " ".join(self._dedupe_keep_order(descriptions))
        merged["merged_from"] = self._dedupe_keep_order(
            target.get("merged_from", []) + source.get("merged_from", []) + [source.get("name", "")]
        )
        return merged

    def _target_module_budget(self, total_functions: int) -> int:
        if total_functions <= 12:
            return 3
        if total_functions <= 24:
            return 4
        if total_functions <= 40:
            return 6
        if total_functions <= 60:
            return 8
        if total_functions <= 90:
            return 11
        if total_functions <= 130:
            return 15
        return min(22, max(16, total_functions // 8))

    def _module_file_dirs(self, module: Dict) -> Set[str]:
        return {os.path.dirname(path) or "root" for path in module.get("files", [])}

    def _count_module_edges(
        self,
        target_index: int,
        source_index: int,
        edge_counts: Dict[Tuple[int, int], int],
    ) -> int:
        return edge_counts.get((target_index, source_index), 0) + edge_counts.get((source_index, target_index), 0)

    def _is_same_file_merge_candidate(self, target: Dict, source: Dict) -> bool:
        target_files = set(target.get("files", []))
        source_files = set(source.get("files", []))
        return bool(target_files and target_files == source_files)

    def _is_public_like_module(self, module: Dict, inbound_count: int) -> bool:
        functions = module.get("functions", [])
        if inbound_count >= 2:
            return True
        if len(functions) >= 4 and any(not self._is_static_function(func) for func in functions):
            return True
        return False

    def _is_static_function(self, func: Dict) -> bool:
        source_head = self._extract_signature_excerpt(func, 240).lower()
        return source_head.startswith("static ") or " static " in source_head

    def _has_public_function(self, module: Dict) -> bool:
        return any(not self._is_static_function(func) for func in module.get("functions", []))

    def _has_uncalled_public_source_functions(self, source: Dict, called_source_ids: Set[str]) -> bool:
        return any(
            self._function_uid(func) not in called_source_ids and not self._is_static_function(func)
            for func in source.get("functions", [])
        )

    def _has_direct_function_edge(self, left_id: str, right_id: str, call_graph: Dict[str, List[str]]) -> bool:
        return right_id in call_graph.get(left_id, []) or left_id in call_graph.get(right_id, [])

    def _has_safe_function_name_family(self, left: Dict, right: Dict, shared_tokens: Set[str]) -> bool:
        left_name = (left.get("name") or "").lower()
        right_name = (right.get("name") or "").lower()
        if not left_name or not right_name or left_name == right_name:
            return False

        common_len = self._longest_common_prefix_len(left_name, right_name)
        shorter = max(1, min(len(left_name), len(right_name)))
        if common_len < 5 or common_len / shorter < 0.5:
            return False

        if shared_tokens:
            return True

        left_actions = self._identifier_action_tokens(left_name)
        right_actions = self._identifier_action_tokens(right_name)
        action_families = [
            {"lower", "upper", "case"},
            {"encode", "decode"},
            {"insert", "delete", "remove"},
            {"alloc", "free"},
            {"read", "write"},
        ]
        return any(left_actions & family and right_actions & family for family in action_families)

    def _has_compatible_function_prefix(self, left: Dict, right: Dict) -> bool:
        left_prefix = self._extract_function_prefix(left.get("name", ""))
        right_prefix = self._extract_function_prefix(right.get("name", ""))
        return (
            left_prefix
            and left_prefix == right_prefix
            and left_prefix != "unknown"
            and not self._is_low_signal_function_prefix(left_prefix)
        )

    def _functions_are_llm_module_compatible(
        self,
        left: Dict,
        right: Dict,
        module_tokens: Set[str],
        token_cache: Dict[str, Set[str]],
        call_graph: Dict[str, List[str]],
    ) -> bool:
        left_id = self._function_uid(left)
        right_id = self._function_uid(right)
        left_tokens = token_cache.get(left_id, set())
        right_tokens = token_cache.get(right_id, set())
        left_actions = self._identifier_action_tokens(left.get("name", ""))
        right_actions = self._identifier_action_tokens(right.get("name", ""))
        shared_tokens = left_tokens & right_tokens

        if self._has_compatible_function_prefix(left, right):
            return True

        if self._has_safe_function_name_family(left, right, shared_tokens):
            return True

        if shared_tokens and (left_tokens & module_tokens) and (right_tokens & module_tokens):
            return True

        if (
            (left_tokens & module_tokens)
            and (right_tokens & module_tokens)
            and self._share_action_family(left_tokens, right_tokens)
        ):
            return True

        if self._has_direct_function_edge(left_id, right_id, call_graph):
            # Direct calls only prove same-module ownership for private helpers or
            # functions with matching semantics. Public APIs often call lower-level
            # utilities without belonging to the utility module.
            if self._is_static_function(left) or self._is_static_function(right):
                return True
            if shared_tokens:
                return True
            if self._share_action_family(left_actions, right_actions):
                return True

        return False

    def _connected_function_components(
        self,
        functions: List[Dict],
        module_tokens: Set[str],
        token_cache: Dict[str, Set[str]],
        call_graph: Dict[str, List[str]],
    ) -> List[List[Dict]]:
        if len(functions) <= 1:
            return [functions]

        ids = [self._function_uid(func) for func in functions]
        by_id = {self._function_uid(func): func for func in functions}
        adjacency = {func_id: set() for func_id in ids}
        for left_index, left in enumerate(functions):
            left_id = self._function_uid(left)
            for right in functions[left_index + 1:]:
                right_id = self._function_uid(right)
                if self._functions_are_llm_module_compatible(
                    left,
                    right,
                    module_tokens,
                    token_cache,
                    call_graph,
                ):
                    adjacency[left_id].add(right_id)
                    adjacency[right_id].add(left_id)

        components = []
        seen = set()
        for func_id in ids:
            if func_id in seen:
                continue
            queue = deque([func_id])
            seen.add(func_id)
            component_ids = []
            while queue:
                current = queue.popleft()
                component_ids.append(current)
                for neighbor in sorted(adjacency[current]):
                    if neighbor not in seen:
                        seen.add(neighbor)
                        queue.append(neighbor)
            components.append(
                sorted(
                    [by_id[item] for item in component_ids],
                    key=self._function_sort_key,
                )
            )
        return sorted(components, key=lambda funcs: (-len(funcs), self._function_sort_key(funcs[0])))

    def _clone_module_with_functions(
        self,
        module: Dict,
        functions: List[Dict],
        suffix: str,
        purified_from: Optional[str] = None,
    ) -> Dict:
        updated = dict(module)
        updated["functions"] = sorted(functions, key=self._function_sort_key)
        updated["files"] = sorted({
            self._normalize_path(func.get("file", ""))
            for func in updated["functions"]
            if func.get("file")
        })
        updated["function_count"] = len(updated["functions"])
        updated["total_lines"] = sum(self._safe_line_count(func) for func in updated["functions"])
        updated["structs"] = sorted(set(module.get("structs", [])))
        if suffix:
            updated["name"] = f"{module.get('name', 'module')}_{suffix}"
        if purified_from:
            updated["purified_from"] = purified_from
        return updated

    def _purify_llm_modules(
        self,
        modules: List[Dict],
        dependency_graph: Dict,
    ) -> List[Dict]:
        if not modules:
            return modules

        all_functions = [
            func
            for module in modules
            for func in module.get("functions", [])
        ]
        name_to_ids = self._build_name_to_function_ids(all_functions)
        function_ids = {self._function_uid(func) for func in all_functions}
        call_graph = self._normalize_call_graph_to_ids(
            dependency_graph.get("call_graph", {}),
            name_to_ids,
            function_ids,
        )
        common_tokens = self._common_project_semantic_tokens(all_functions)
        token_cache = {
            self._function_uid(func): self._function_semantic_tokens(func, common_tokens)
            for func in all_functions
        }

        purified = []
        for module in modules:
            functions = module.get("functions", [])
            if len(functions) <= 1 or self._is_entry_like_module(module):
                purified.append(module)
                continue

            module_tokens = self._module_semantic_tokens(module) - common_tokens
            components = self._connected_function_components(
                functions,
                module_tokens,
                token_cache,
                call_graph,
            )
            if len(components) <= 1:
                purified.append(module)
                continue

            largest_component_size = len(components[0])
            public_static_mix = (
                len(functions) == 2
                and any(self._is_static_function(func) for func in functions)
                and any(not self._is_static_function(func) for func in functions)
            )
            should_split = (
                len(functions) >= 4
                or (len(functions) == 3 and largest_component_size >= 2)
                or public_static_mix
            )
            if not should_split:
                purified.append(module)
                continue

            base_name = module.get("name", "module")
            for index, component in enumerate(components, start=1):
                suffix = "" if index == 1 else f"purified_{index:02d}"
                purified.append(
                    self._clone_module_with_functions(
                        module,
                        component,
                        suffix,
                        purified_from=base_name,
                    )
                )
        return purified

    def _can_merge_llm_small_modules(
        self,
        target: Dict,
        source: Dict,
        target_index: int,
        source_index: int,
        edge_count: int,
        called_source_ids: Set[str],
        inbound_modules: List[Dict],
        module_func_ids: List[Set[str]],
    ) -> bool:
        if not self._is_same_file_merge_candidate(target, source):
            return False

        union_size = len(module_func_ids[target_index] | module_func_ids[source_index])
        if union_size > self.MAX_CLUSTER_FUNCTIONS:
            return False

        # Entry/test harness modules orchestrate other code and should not absorb implementation modules.
        if self._is_entry_like_module(target) or self._is_entry_like_module(source):
            return False

        # Keep test code and library code apart even inside unusual monolithic files unless both are tests.
        if self._is_test_like_module(target) != self._is_test_like_module(source):
            return False

        source_inbound_count = sum(inbound_modules[source_index].values())
        if self._is_public_like_module(source, source_inbound_count) and len(source.get("functions", [])) > 1:
            return False

        source_functions = source.get("functions", [])
        target_functions = target.get("functions", [])
        source_all_static = bool(source_functions) and all(self._is_static_function(func) for func in source_functions)
        source_has_public = self._has_public_function(source)
        target_has_public = self._has_public_function(target)
        source_inbound = set(inbound_modules[source_index].keys())
        source_is_private_helper = (
            len(source_functions) <= 3
            and source_inbound
            and source_inbound.issubset({target_index})
            and len(target_functions) <= 6
            and source_all_static
        )
        if source_is_private_helper:
            return True

        target_prefixes = self._module_function_prefixes(target)
        source_prefixes = self._module_function_prefixes(source)
        shared_prefix = bool(target_prefixes & source_prefixes)
        name_prefix_similar = self._has_strong_function_name_prefix_similarity(target, source)

        shared_semantic_tokens = self._module_semantic_tokens(target) & self._module_semantic_tokens(source)
        strong_semantic_overlap = len(shared_semantic_tokens) >= 2
        public_name_family = name_prefix_similar and (bool(shared_semantic_tokens) or union_size <= 2)
        strong_same_family = shared_prefix or strong_semantic_overlap or public_name_family

        if not strong_same_family:
            return False

        single_helper_called_by_target = (
            len(source_functions) == 1
            and source_inbound
            and source_inbound.issubset({target_index})
            and union_size <= 8
            and source_all_static
        )
        if single_helper_called_by_target:
            return True

        if edge_count <= 0:
            if union_size > 8:
                return False
            if source_has_public or target_has_public:
                public_safe_family = (
                    shared_prefix
                    or public_name_family
                    or strong_semantic_overlap
                )
                if not public_safe_family:
                    return False
                if source_has_public and len(source_functions) > 1 and not (
                    shared_prefix or (name_prefix_similar and bool(shared_semantic_tokens))
                ):
                    return False
                if (
                    (len(source_functions) > 1 or len(target_functions) > 1)
                    and not (shared_prefix or (name_prefix_similar and bool(shared_semantic_tokens)))
                    and not strong_semantic_overlap
                ):
                    return False
            return True

        if source_all_static:
            return union_size <= 8

        if source_has_public and self._has_uncalled_public_source_functions(source, called_source_ids):
            # Avoid dragging unrelated public APIs along with a helper that happened to be
            # placed in the same small LLM module.
            if not (shared_prefix or (name_prefix_similar and bool(shared_semantic_tokens))):
                return False

        return union_size <= 8

    def _planning_merge_score(
        self,
        target: Dict,
        source: Dict,
        target_index: int,
        source_index: int,
        edge_counts: Dict[Tuple[int, int], int],
        inbound_modules: List[Dict],
        module_func_ids: List[Set[str]],
        total_functions: int,
    ) -> Optional[int]:
        if target_index == source_index:
            return None

        union_size = len(module_func_ids[target_index] | module_func_ids[source_index])
        if union_size > min(self.MAX_MODULE_FUNCTIONS, self.MAX_PLANNING_MODULE_FUNCTIONS):
            return None

        target_entry = self._is_entry_like_module(target)
        source_entry = self._is_entry_like_module(source)
        if target_entry or source_entry:
            return None

        target_test = self._is_test_like_module(target)
        source_test = self._is_test_like_module(source)
        if target_test != source_test:
            return None

        target_files = set(target.get("files", []))
        source_files = set(source.get("files", []))
        same_file = bool(target_files and target_files == source_files)
        same_dir = bool(self._module_file_dirs(target) & self._module_file_dirs(source))

        shared_prefixes = self._module_function_prefixes(target) & self._module_function_prefixes(source)
        shared_semantic_tokens = self._module_semantic_tokens(target) & self._module_semantic_tokens(source)
        edge_count = self._count_module_edges(target_index, source_index, edge_counts)
        name_prefix_similar = self._has_strong_function_name_prefix_similarity(target, source)
        source_purified = "purified" in (source.get("name", "") or "")
        target_purified = "purified" in (target.get("name", "") or "")

        if not same_file:
            if not same_dir:
                return None
            if edge_count <= 0:
                return None
            if not shared_semantic_tokens and not shared_prefixes:
                return None

        target_inbound = sum(inbound_modules[target_index].values())
        source_inbound = sum(inbound_modules[source_index].values())
        target_public = self._is_public_like_module(target, target_inbound)
        source_public = self._is_public_like_module(source, source_inbound)
        if not target_test and not same_file and target_public and source_public:
            return None
        if same_file and not target_test and not source_test:
            has_strong_non_test_link = (
                edge_count > 0
                or bool(shared_prefixes)
                or len(shared_semantic_tokens) >= 2
                or name_prefix_similar
            )
            if not has_strong_non_test_link:
                return None

        score = 0
        if same_file:
            score += 9
        elif same_dir:
            score += 2

        if target_test and source_test:
            score += 7
        elif same_file and (target_test or source_test):
            score += 3

        if edge_count > 0:
            score += min(5, edge_count)

        if shared_prefixes:
            score += 4 + min(2, len(shared_prefixes) - 1)

        if len(shared_semantic_tokens) >= 2:
            score += 4
        elif shared_semantic_tokens:
            score += 2

        if name_prefix_similar:
            score += 2

        if target_purified or source_purified:
            score += 3

        if min(len(target.get("functions", [])), len(source.get("functions", []))) <= 2:
            score += 2

        if union_size <= 8:
            score += 2
        elif union_size <= 12:
            score += 1

        if same_file and not target_test and not source_test and edge_count <= 0 and not shared_prefixes and len(shared_semantic_tokens) < 2:
            score -= 4

        target_names = set(self._module_function_names(target))
        source_names = set(self._module_function_names(source))
        if target_names & source_names:
            score -= 10

        target_budget = self._target_module_budget(total_functions)
        if len(module_func_ids) > target_budget and same_file:
            score += 2

        return score if score >= 8 else None

    def _consolidate_modules_for_planning(
        self,
        modules: List[Dict],
        dependency_graph: Dict,
        total_functions: int,
    ) -> List[Dict]:
        if len(modules) <= 1:
            return modules

        target_budget = self._target_module_budget(total_functions)
        if len(modules) <= target_budget:
            return modules

        merged_modules = [dict(module) for module in modules]
        all_functions = [
            func
            for module in merged_modules
            for func in module.get("functions", [])
        ]
        name_to_ids = self._build_name_to_function_ids(all_functions)
        function_ids = {self._function_uid(func) for func in all_functions}
        call_graph = self._normalize_call_graph_to_ids(
            dependency_graph.get("call_graph", {}),
            name_to_ids,
            function_ids,
        )

        changed = True
        while changed and len(merged_modules) > target_budget:
            changed = False
            func_to_module = {}
            module_func_ids = []
            for index, module in enumerate(merged_modules):
                ids = {self._function_uid(func) for func in module.get("functions", [])}
                module_func_ids.append(ids)
                for func_id in ids:
                    func_to_module[func_id] = index

            inbound_modules = [defaultdict(int) for _ in merged_modules]
            edge_counts = defaultdict(int)
            for caller_id, callees in call_graph.items():
                caller_module = func_to_module.get(caller_id)
                if caller_module is None:
                    continue
                for callee_id in callees:
                    callee_module = func_to_module.get(callee_id)
                    if callee_module is None or callee_module == caller_module:
                        continue
                    edge_counts[(caller_module, callee_module)] += 1
                    inbound_modules[callee_module][caller_module] += 1

            best_pair = None
            best_score = None
            for target_index in range(len(merged_modules)):
                for source_index in range(target_index + 1, len(merged_modules)):
                    score = self._planning_merge_score(
                        merged_modules[target_index],
                        merged_modules[source_index],
                        target_index,
                        source_index,
                        edge_counts,
                        inbound_modules,
                        module_func_ids,
                        total_functions,
                    )
                    if score is None:
                        continue
                    if best_score is None or score > best_score:
                        best_score = score
                        best_pair = (target_index, source_index)

            if best_pair is None:
                break

            target_index, source_index = best_pair
            merged_modules[target_index] = self._merge_module_pair(
                merged_modules[target_index],
                merged_modules[source_index],
            )
            del merged_modules[source_index]
            changed = True

        return merged_modules

    def _merge_related_small_modules(
        self,
        modules: List[Dict],
        dependency_graph: Dict,
    ) -> List[Dict]:
        if len(modules) <= 1:
            return modules

        all_functions = [
            func
            for module in modules
            for func in module.get("functions", [])
        ]
        name_to_ids = self._build_name_to_function_ids(all_functions)
        function_ids = {self._function_uid(func) for func in all_functions}
        call_graph = self._normalize_call_graph_to_ids(
            dependency_graph.get("call_graph", {}),
            name_to_ids,
            function_ids,
        )

        merged_modules = [dict(module) for module in modules]
        changed = True
        while changed:
            changed = False
            func_to_module = {}
            module_func_ids = []
            for index, module in enumerate(merged_modules):
                ids = {self._function_uid(func) for func in module.get("functions", [])}
                module_func_ids.append(ids)
                for func_id in ids:
                    func_to_module[func_id] = index

            inbound_modules = [defaultdict(int) for _ in merged_modules]
            outbound_modules = [defaultdict(int) for _ in merged_modules]
            edge_counts = defaultdict(int)
            edge_callee_ids = defaultdict(set)
            for caller_id, callees in call_graph.items():
                caller_module = func_to_module.get(caller_id)
                if caller_module is None:
                    continue
                for callee_id in callees:
                    callee_module = func_to_module.get(callee_id)
                    if callee_module is None or callee_module == caller_module:
                        continue
                    edge_counts[(caller_module, callee_module)] += 1
                    edge_callee_ids[(caller_module, callee_module)].add(callee_id)
                    inbound_modules[callee_module][caller_module] += 1
                    outbound_modules[caller_module][callee_module] += 1

            merge_pair = None
            for (target_index, source_index), edge_count in sorted(edge_counts.items(), key=lambda item: -item[1]):
                target = merged_modules[target_index]
                source = merged_modules[source_index]
                if self._can_merge_llm_small_modules(
                    target,
                    source,
                    target_index,
                    source_index,
                    edge_count,
                    edge_callee_ids.get((target_index, source_index), set()),
                    inbound_modules,
                    module_func_ids,
                ):
                    merge_pair = (target_index, source_index)
                    break

            if merge_pair is None:
                for target_index in range(len(merged_modules)):
                    for source_index in range(len(merged_modules)):
                        if target_index == source_index:
                            continue
                        target = merged_modules[target_index]
                        source = merged_modules[source_index]
                        if self._can_merge_llm_small_modules(
                            target,
                            source,
                            target_index,
                            source_index,
                            0,
                            set(),
                            inbound_modules,
                            module_func_ids,
                        ):
                            merge_pair = (target_index, source_index)
                            break
                    if merge_pair is not None:
                        break

            if merge_pair is None:
                continue

            target_index, source_index = merge_pair
            merged_modules[target_index] = self._merge_module_pair(
                merged_modules[target_index],
                merged_modules[source_index],
            )
            del merged_modules[source_index]
            changed = True

        return merged_modules

    def _llm_guided_module_split(
        self,
        project_info: Dict,
        normalized_project_analysis: Dict,
        dependency_graph: Dict,
        static_modules: List[Dict],
    ) -> Optional[List[Dict]]:
        if not self.use_llm or not self.llm:
            return None

        functions = normalized_project_analysis.get("functions", [])
        if not functions:
            return None

        print("  使用 LLM 动态查询进行模块划分...")

        function_index = self._build_function_index(functions, dependency_graph)
        call_graph = {
            func_id: item.get("callees", [])
            for func_id, item in function_index.items()
            if item.get("callees")
        }
        reverse_call_graph = self._build_reverse_call_graph(call_graph)
        struct_to_functions = defaultdict(list)
        for func_id, item in function_index.items():
            for struct_name in item.get("structs", []):
                struct_to_functions[struct_name].append(func_id)
        struct_to_functions = {name: sorted(set(ids)) for name, ids in struct_to_functions.items()}
        functions_by_id = self._function_records_by_id(functions)
        all_names = set(function_index.keys())
        assigned_names: Set[str] = set()
        llm_specs: List[Dict] = []
        context_rounds = []
        query_queue = deque(self._initial_llm_queries(project_info, function_index, call_graph))
        seen_queries = set()

        for round_index in range(1, self.llm_max_rounds + 1):
            unassigned = all_names - assigned_names
            if not unassigned:
                break

            seed_query = ""
            while query_queue and not seed_query:
                candidate = query_queue.popleft()
                if candidate not in seen_queries:
                    seed_query = candidate
                    seen_queries.add(candidate)
            if not seed_query:
                seed_query = sorted(
                    unassigned,
                    key=lambda name: (
                        -(len(call_graph.get(name, [])) + len(reverse_call_graph.get(name, []))),
                        function_index[name]["file"],
                        function_index[name]["start_line"],
                        name,
                    ),
                )[0]
                seen_queries.add(seed_query)

            visible_names = self._expand_llm_context(
                seed_query,
                function_index,
                call_graph,
                reverse_call_graph,
                struct_to_functions,
                unassigned,
            )
            if not visible_names:
                continue

            messages = self._build_llm_partition_prompt(
                project_info,
                seed_query,
                visible_names,
                assigned_names,
                function_index,
            )
            parsed = self._call_llm_for_partition(messages, round_index)
            if parsed is None:
                return None

            modules, next_queries = self._validate_llm_modules(
                parsed,
                set(visible_names),
                assigned_names,
                function_index,
            )
            round_assigned = []
            for module in modules:
                new_ids = [func_id for func_id in module["function_ids"] if func_id not in assigned_names]
                if not new_ids:
                    continue
                module["function_ids"] = new_ids
                module["function_names"] = [function_index[func_id]["name"] for func_id in new_ids]
                assigned_names.update(new_ids)
                round_assigned.extend(new_ids)
                llm_specs.append(module)

            for module in modules:
                for query in module.get("expand_queries", []):
                    if query not in seen_queries:
                        query_queue.append(query)
            for query in next_queries:
                if query not in seen_queries:
                    query_queue.append(query)

            for name in round_assigned:
                for related in self._related_functions(
                    name,
                    function_index,
                    call_graph,
                    reverse_call_graph,
                    struct_to_functions,
                ):
                    if related not in assigned_names and related not in seen_queries:
                        query_queue.append(related)

            context_rounds.append(
                {
                    "round": round_index,
                    "seed_query": seed_query,
                    "visible_functions": visible_names,
                    "assigned_functions": sorted(round_assigned),
                    "next_queries": self._dedupe_keep_order(
                        next_queries + [
                            query
                            for module in modules
                            for query in module.get("expand_queries", [])
                        ]
                    ),
                }
            )

        if len(assigned_names) < self.MIN_LLM_ASSIGNED_FUNCTIONS:
            return None

        fallback_specs = self._fallback_specs_for_unassigned(
            all_names - assigned_names,
            static_modules,
            dependency_graph,
        )
        modules = []
        for index, spec in enumerate(llm_specs, start=1):
            modules.append(
                self._materialize_llm_module(
                    spec,
                    functions_by_id,
                    normalized_project_analysis.get("structs", []),
                    project_info,
                    dependency_graph,
                    index,
                    fallback=False,
                )
            )
        modules = self._purify_llm_modules(modules, dependency_graph)
        modules = self._merge_related_small_modules(modules, dependency_graph)
        modules = self._purify_llm_modules(modules, dependency_graph)
        for index, spec in enumerate(fallback_specs, start=len(modules) + 1):
            modules.append(
                self._materialize_llm_module(
                    spec,
                    functions_by_id,
                    normalized_project_analysis.get("structs", []),
                    project_info,
                    dependency_graph,
                    index,
                    fallback=True,
                )
            )

        modules = self._dedupe_module_names(modules)
        for module in modules:
            module["llm_context_rounds"] = context_rounds
            module["llm_coverage"] = {
                "assigned_functions": len(assigned_names),
                "total_functions": len(all_names),
                "fallback_functions": len(all_names - assigned_names),
            }

        print(
            f"  OK LLM 动态划分完成：{len(modules)} 个模块，"
            f"LLM 覆盖 {len(assigned_names)}/{len(all_names)} 个函数"
        )
        return modules

    def _resolve_responsibility_labels(self, module: Dict) -> List[str]:
        text_parts = []
        text_parts.extend(module.get("files", []))
        text_parts.extend(func.get("name", "") for func in module.get("functions", []))
        text_parts.extend(module.get("structs", []))
        tokens = {
            token
            for text in text_parts
            for token in re.split(r"[^A-Za-z0-9]+|_", (text or "").lower())
            if token
        }

        labels = []
        for label, keywords in self.RESPONSIBILITY_KEYWORDS.items():
            if any(keyword in tokens for keyword in keywords):
                labels.append(label)
        if not labels:
            category = module.get("category", "module")
            labels.append(category if category != "module" else "general_c_logic")
        return labels[:5]

    def _top_function_prefixes(self, functions: List[Dict]) -> List[str]:
        counter = Counter()
        for func in functions:
            counter[self._extract_function_prefix(func.get("name", ""))] += 1
        return [name for name, _ in counter.most_common(6) if name and name != "unknown"]

    def _public_entrypoints(
        self,
        module_function_names: Set[str],
        functions: List[Dict],
        reverse_call_graph: Dict[str, List[str]],
    ) -> List[str]:
        entries = []
        for func in functions:
            name = func.get("name")
            if not name:
                continue
            callers = reverse_call_graph.get(name, [])
            has_external_caller = any(caller not in module_function_names for caller in callers)
            source_head = self._extract_signature_excerpt(func, 220).lower()
            looks_static = source_head.startswith("static ") or " static " in source_head
            if has_external_caller or name == "main" or not looks_static:
                entries.append(name)
        return sorted(set(entries))[:12]

    def _module_dependency_summary(
        self,
        module_function_names: Set[str],
        call_graph: Dict[str, List[str]],
        reverse_call_graph: Dict[str, List[str]],
    ) -> Tuple[List[str], List[str], int]:
        outbound = set()
        inbound = set()
        internal_calls = 0

        for name in module_function_names:
            for callee in call_graph.get(name, []):
                if callee in module_function_names:
                    internal_calls += 1
                else:
                    outbound.add(callee)
            for caller in reverse_call_graph.get(name, []):
                if caller not in module_function_names:
                    inbound.add(caller)

        return sorted(outbound)[:20], sorted(inbound)[:20], internal_calls

    def _make_static_summary_text(self, module: Dict, responsibilities: List[str], entrypoints: List[str], outbound: List[str]) -> str:
        files = module.get("files", [])
        function_count = len(module.get("functions", []))
        responsibility_text = ", ".join(responsibilities) if responsibilities else "general C logic"
        file_text = ", ".join(files[:3]) + (" 等" if len(files) > 3 else "")
        entry_text = ", ".join(entrypoints[:5]) if entrypoints else "无明显外部入口"
        outbound_text = ", ".join(outbound[:5]) if outbound else "无明显模块外调用"
        return (
            f"该模块覆盖 {function_count} 个函数，主要职责信号为 {responsibility_text}；"
            f"核心文件包括 {file_text or 'unknown'}；"
            f"静态入口候选为 {entry_text}；"
            f"模块外依赖为 {outbound_text}。"
        )

    def _summarize_module_static(
        self,
        module: Dict,
        dependency_graph: Dict,
        project_function_names: Set[str],
    ) -> Dict:
        functions = sorted(module.get("functions", []), key=self._function_sort_key)
        module_function_names = {func.get("name") for func in functions if func.get("name")}
        call_graph = self._normalize_call_graph(dependency_graph.get("call_graph", {}))
        reverse_call_graph = self._build_reverse_call_graph(call_graph)
        outbound, inbound, internal_calls = self._module_dependency_summary(
            module_function_names,
            call_graph,
            reverse_call_graph,
        )
        outbound = [name for name in outbound if name in project_function_names]
        inbound = [name for name in inbound if name in project_function_names]

        responsibilities = self._resolve_responsibility_labels(module)
        entrypoints = self._public_entrypoints(module_function_names, functions, reverse_call_graph)
        key_functions = sorted(
            [
                {
                    "name": func.get("name", "unknown"),
                    "file": self._normalize_path(func.get("file", "")),
                    "start_line": int(func.get("start_line", 0) or 0),
                    "end_line": int(func.get("end_line", 0) or 0),
                    "line_count": self._safe_line_count(func),
                    "signature": self._extract_signature_excerpt(func, 220),
                }
                for func in functions
            ],
            key=lambda item: (-item["line_count"], item["file"], item["start_line"], item["name"]),
        )[:12]

        summary = {
            "summary": self._make_static_summary_text(module, responsibilities, entrypoints, outbound),
            "responsibilities": responsibilities,
            "function_prefixes": self._top_function_prefixes(functions),
            "entrypoints": entrypoints,
            "key_functions": key_functions,
            "data_structures": sorted(set(module.get("structs", [])))[:20],
            "inbound_dependencies": inbound,
            "outbound_dependencies": outbound,
            "internal_call_count": internal_calls,
            "files": sorted(module.get("files", [])),
            "headers": sorted(module.get("headers", [])),
        }
        return summary

    def _attach_static_summaries(
        self,
        modules: List[Dict],
        dependency_graph: Dict,
        project_function_names: Set[str],
    ) -> List[Dict]:
        summarized = []
        for module in modules:
            updated = dict(module)
            static_summary = self._summarize_module_static(
                updated,
                dependency_graph,
                project_function_names,
            )
            updated["static_summary"] = static_summary
            updated["summary"] = static_summary["summary"]
            summarized.append(updated)
        return summarized

    def _resolve_module_category(self, dir_name: str) -> Tuple[str, str]:
        # 模块类别完全基于目录名启发式推断。
        # “high” 表示目录 token 明确命中关键字；“medium” 表示只是前缀/弱命中。
        tokens = self._tokenize_path(dir_name)
        token_set = set(tokens)

        for category, keywords in self.MODULE_CATEGORIES.items():
            if any(keyword in token_set for keyword in keywords):
                return category, "high"

        for category, keywords in self.MODULE_CATEGORIES.items():
            if any(any(keyword == token or token.startswith(f"{keyword}") for token in tokens) for keyword in keywords):
                return category, "medium"

        return "module", "medium"

    def _match_file(self, func_or_struct_file: str, module_files: Set[str]) -> bool:
        # 既支持完全相等，也支持 absolute_path.endswith(relative_path)。
        # 这样 parser 输出绝对路径或相对路径都能落进同一个模块。
        normalized = self._normalize_path(func_or_struct_file)
        if normalized in module_files:
            return True
        return any(normalized.endswith("/" + module_file) for module_file in module_files)

    def _collect_file_headers(self, module_files: Set[str]) -> List[str]:
        return sorted([path for path in module_files if path.endswith((".h", ".hpp", ".hh", ".hxx"))])

    def _build_project_function_set(self, functions: List[Dict]) -> Set[str]:
        return {func.get("name") for func in functions if func.get("name")}

    def _compute_module_cohesion(
        self,
        module_functions: List[Dict],
        call_graph: Dict[str, List[str]],
        project_function_names: Set[str],
    ) -> Tuple[int, int, float]:
        """
        计算模块内聚度。

        定义方式很朴素：
        - internal_calls：模块内函数互相调用的次数
        - external_calls：模块内函数调用模块外函数的次数
        - cohesion_score = internal / (internal + external + 1)

        它不是一个严格的软件工程指标，而是“这个目录像不像同一个模块”的经验信号。
        """
        module_func_names = {func.get("name") for func in module_functions if func.get("name")}
        internal_calls = 0
        external_calls = 0

        for func in module_functions:
            func_name = func.get("name")
            if not func_name:
                continue
            for called in call_graph.get(func_name, []):
                if called not in project_function_names:
                    continue
                if called in module_func_names:
                    internal_calls += 1
                else:
                    external_calls += 1

        if internal_calls == 0 and external_calls == 0:
            # 对于没有项目内调用关系的叶子模块，0 分会被误读成“坏模块”。
            # 这里把它视作“调用关系不足以否定模块边界”，给满分保底。
            cohesion_score = 1.0
        else:
            cohesion_score = internal_calls / (internal_calls + external_calls)
        return internal_calls, external_calls, cohesion_score

    def _refresh_final_module_metrics(
        self,
        modules: List[Dict],
        call_graph: Dict[str, List[str]],
        project_function_names: Set[str],
    ) -> List[Dict]:
        refreshed = []
        for module in modules:
            internal_calls, external_calls, cohesion_score = self._compute_module_cohesion(
                module.get("functions", []),
                call_graph,
                project_function_names,
            )

            updated = dict(module)
            updated["internal_calls"] = internal_calls
            updated["external_calls"] = external_calls
            updated["cohesion_score"] = cohesion_score
            refreshed.append(updated)

        return refreshed

    def _collect_struct_names(self, items: List[Dict]) -> List[str]:
        names = []
        for item in items:
            name = item.get("name") or item.get("struct_name")
            if name:
                names.append(name)
        return sorted(set(names))

    def _make_split_reasons(
        self,
        module_files: List[str],
        module_functions: List[Dict],
        cohesion_score: float,
        category: str,
    ) -> List[str]:
        # 是否拆模块，不只看内聚度，还要综合规模和目录职责是否清晰。
        reasons = []
        if len(module_files) > self.MAX_MODULE_FILES:
            reasons.append(f"文件数过多({len(module_files)})")
        if len(module_functions) > self.MAX_MODULE_FUNCTIONS:
            reasons.append(f"函数数过多({len(module_functions)})")
        if cohesion_score < 0.3 and len(module_files) > 3:
            reasons.append(f"内聚度较低({cohesion_score:.2f})")
        if category == "module" and len(module_files) > 5:
            reasons.append("职责不明确且目录范围较大")
        return reasons

    def _pick_primary_struct_key(
        self,
        funcs: List[Dict],
        struct_usage: Dict[str, List[str]],
    ) -> str | None:
        counter = Counter()
        for func in funcs:
            func_name = func.get("name")
            for struct_name in struct_usage.get(func_name, []):
                counter[struct_name] += 1
        if not counter:
            return None
        name, count = counter.most_common(1)[0]
        return name if count >= self.MIN_STRUCT_CLUSTER_SIZE else None

    def _group_functions_by_file(self, functions: List[Dict]) -> Dict[str, List[Dict]]:
        grouped = defaultdict(list)
        for func in functions:
            grouped[self._normalize_path(func.get("file", ""))].append(func)
        for funcs in grouped.values():
            funcs.sort(key=self._function_sort_key)
        return grouped

    def _chunk_large_group(self, funcs: List[Dict], cluster_type: str, cluster_key: str) -> List[Dict]:
        # 把一个已经归好的函数组继续切块，保证单块不超过函数数和总行数阈值。
        chunks = []
        current = []
        current_lines = 0

        for func in sorted(funcs, key=self._function_sort_key):
            func_lines = self._safe_line_count(func)
            if current and (
                len(current) >= self.MAX_CLUSTER_FUNCTIONS
                or current_lines + func_lines > self.MAX_CLUSTER_LINES
            ):
                chunks.append(
                    {
                        "cluster_type": cluster_type,
                        "cluster_key": cluster_key,
                        "functions": current,
                    }
                )
                current = []
                current_lines = 0

            current.append(func)
            current_lines += func_lines

        if current:
            chunks.append(
                {
                    "cluster_type": cluster_type,
                    "cluster_key": cluster_key,
                    "functions": current,
                }
            )

        return chunks

    def _build_clusters_from_functions(
        self,
        functions: List[Dict],
        struct_usage: Dict[str, List[str]],
    ) -> List[Dict]:
        """
        对“大模块里的函数集合”做第二层聚类。

        顺序是有意设计的：
        1. 先按共享结构体：最强的语义信号
        2. 再按函数名前缀：中等强度的命名信号
        3. 最后按文件局部性：保底，让剩余函数也能落到可解释的小组里
        """
        functions = sorted(functions, key=self._function_sort_key)
        remaining = list(functions)
        clusters = []

        # 第一轮：按共享结构体聚类。
        # 多个函数如果围绕同一个 struct 工作，通常就是天然的职责单元。
        struct_groups = defaultdict(list)
        for func in remaining:
            func_name = func.get("name")
            for struct_name in struct_usage.get(func_name, []):
                struct_groups[struct_name].append(func)

        claimed_names = set()
        for struct_name, funcs in sorted(struct_groups.items(), key=lambda item: (-len(item[1]), item[0])):
            available = [func for func in funcs if func.get("name") not in claimed_names]
            if len(available) < self.MIN_STRUCT_CLUSTER_SIZE:
                continue
            for chunk in self._chunk_large_group(available, "struct_based", struct_name):
                clusters.append(chunk)
            claimed_names.update(func.get("name") for func in available if func.get("name"))

        remaining = [func for func in remaining if func.get("name") not in claimed_names]

        # 第二轮：按函数名前缀聚类。
        # 这主要处理 “foo_init/foo_run/foo_close” 一类没有共享 struct 的函数族。
        prefix_groups = defaultdict(list)
        for func in remaining:
            prefix_groups[self._extract_function_prefix(func.get("name", ""))].append(func)

        for prefix, funcs in sorted(prefix_groups.items(), key=lambda item: (-len(item[1]), item[0])):
            if len(funcs) < self.MIN_PREFIX_CLUSTER_SIZE:
                continue
            for chunk in self._chunk_large_group(funcs, "prefix_based", prefix):
                clusters.append(chunk)
            claimed_names.update(func.get("name") for func in funcs if func.get("name"))

        remaining = [func for func in remaining if func.get("name") not in claimed_names]

        # 第三轮：按文件和相邻行号收尾，避免 misc 过大。
        # 这一步更多是工程保底，而不是强语义聚类。
        grouped_by_file = self._group_functions_by_file(remaining)
        for file_path, funcs in grouped_by_file.items():
            for chunk in self._chunk_large_group(funcs, "file_local", os.path.basename(file_path) or "unknown_file"):
                clusters.append(chunk)

        return clusters

    def _materialize_cluster_module(self, parent_module: Dict, cluster: Dict, struct_usage: Dict[str, List[str]], index: int) -> Dict:
        # 把中间态 cluster 转成与 module_units 同结构的“子模块对象”，方便后续统一消费。
        funcs = sorted(cluster["functions"], key=self._function_sort_key)
        func_names = [func.get("name") for func in funcs if func.get("name")]
        files = sorted({self._normalize_path(func.get("file", "")) for func in funcs if func.get("file")})
        structs = self._collect_struct_names(
            [{"name": struct_name} for struct_name in sorted({
                struct_name
                for func_name in func_names
                for struct_name in struct_usage.get(func_name, [])
            })]
        )
        total_lines = sum(self._safe_line_count(func) for func in funcs)

        return {
            "name": f"{parent_module['name']}_{cluster['cluster_key']}_{index:02d}",
            "category": f"{parent_module['category']}_cluster",
            "directory": parent_module["directory"],
            "files": files,
            "functions": funcs,
            "structs": structs,
            "headers": self._collect_file_headers(set(files)),
            "cluster_type": cluster["cluster_type"],
            "cluster_key": cluster["cluster_key"],
            "function_count": len(funcs),
            "total_lines": total_lines,
            "parent_module": parent_module["name"],
            "origin_needs_split": parent_module.get("needs_split", False),
            "origin_split_reasons": parent_module.get("split_reasons", []),
            "origin_split_reason": parent_module.get("split_reason", ""),
            "parent_cohesion_score": parent_module.get("cohesion_score", 0),
            "confidence": parent_module.get("confidence", "medium"),
        }

    def _identify_candidate_modules(self, project_info: Dict, dependency_graph: Dict) -> List[Dict]:
        """
        第一步：识别候选子模块（以目录为初始线索，但避免简单子串误判）
        """
        print("  识别候选子模块...")

        # 这里只看 c_files，对目录做第一层分桶。
        # 这一步故意偏“粗”，因为后面还有一轮语义收敛。
        dir_groups = defaultdict(list)
        for file in project_info.get("c_files", []):
            normalized = self._normalize_path(file)
            dir_name = os.path.dirname(normalized) or "root"
            dir_groups[dir_name].append(normalized)

        candidate_modules = []
        for dir_name, files in sorted(dir_groups.items()):
            category, confidence = self._resolve_module_category(dir_name)
            module_name = f"{category}_{dir_name.replace('/', '_')}"
            candidate_modules.append(
                {
                    "name": module_name,
                    "category": category,
                    "directory": dir_name,
                    "files": sorted(files),
                    "confidence": confidence,
                    "headers": self._collect_file_headers(set(files)),
                }
            )

        print(f"  OK 识别到 {len(candidate_modules)} 个候选子模块")
        return candidate_modules

    def _refine_modules_with_semantics(
        self,
        candidate_modules: List[Dict],
        project_analysis: Dict,
        dependency_graph: Dict,
    ) -> List[Dict]:
        """
        第二步：基于项目内调用、结构体和规模约束收敛模块划分。
        """
        print("  基于语义和依赖关系收敛模块...")

        functions = project_analysis.get("functions", [])
        structs = project_analysis.get("structs", [])
        call_graph = dependency_graph.get("call_graph", {})
        struct_usage = dependency_graph.get("struct_usage", {})
        project_function_names = self._build_project_function_set(functions)

        refined_modules = []
        for candidate in candidate_modules:
            module_files = set(candidate.get("files", []))

            # 这里真正把函数和结构体“挂”到候选模块上。
            # 如果 file 字段匹配不上，模块后面就会变成空壳，因此前面的 schema 归一化非常关键。
            module_functions = [
                func for func in functions if self._match_file(func.get("file", ""), module_files)
            ]
            module_structs = [
                struct for struct in structs if self._match_file(struct.get("file", ""), module_files)
            ]

            internal_calls, external_calls, cohesion_score = self._compute_module_cohesion(
                module_functions,
                call_graph,
                project_function_names,
            )
            split_reasons = self._make_split_reasons(
                sorted(module_files),
                module_functions,
                cohesion_score,
                candidate["category"],
            )

            refined_modules.append(
                {
                    "name": candidate["name"],
                    "category": candidate["category"],
                    "directory": candidate["directory"],
                    "files": sorted(module_files),
                    "functions": sorted(module_functions, key=self._function_sort_key),
                    "structs": self._collect_struct_names(module_structs),
                    "headers": candidate.get("headers", []),
                    "cohesion_score": cohesion_score,
                    "internal_calls": internal_calls,
                    "external_calls": external_calls,
                    "needs_split": bool(split_reasons),
                    "split_reasons": split_reasons,
                    "split_reason": "；".join(split_reasons),
                    "confidence": candidate["confidence"],
                }
            )

        final_modules = []
        for module in refined_modules:
            # 需要拆时，把一个大模块进一步细化成多个可消费的子模块。
            if module["needs_split"]:
                final_modules.extend(self._split_module_by_clusters(module, dependency_graph))
            else:
                final_modules.append(module)

        print(f"  OK 收敛到 {len(final_modules)} 个子模块")
        return final_modules

    def _split_module_by_clusters(self, module: Dict, dependency_graph: Dict) -> List[Dict]:
        """
        将过大的模块递归拆成更小、可用于后续 spec 生成的子模块。
        """
        print(f"    拆分模块 {module['name']}...")

        functions = module.get("functions", [])
        struct_usage = dependency_graph.get("struct_usage", {})
        raw_clusters = self._build_clusters_from_functions(functions, struct_usage)

        sub_modules = []
        for index, cluster in enumerate(raw_clusters, start=1):
            sub_module = self._materialize_cluster_module(module, cluster, struct_usage, index)
            if (
                sub_module["function_count"] > self.MAX_CLUSTER_FUNCTIONS
                or sub_module["total_lines"] > self.MAX_CLUSTER_LINES
            ):
                # 如果一个 cluster 仍然太大，再递归切一次。
                nested_clusters = self._build_clusters_from_functions(sub_module["functions"], struct_usage)
                for nested_index, nested_cluster in enumerate(nested_clusters, start=1):
                    sub_modules.append(
                        self._materialize_cluster_module(
                            sub_module,
                            nested_cluster,
                            struct_usage,
                            nested_index,
                        )
                    )
            else:
                sub_modules.append(sub_module)

        if not sub_modules:
            # 理论上很少走到这里，属于兜底路径：
            # 即便没有成功聚出子簇，也给后续阶段一个可消费的 misc 模块。
            sub_modules.append(
                {
                    "name": f"{module['name']}_misc_01",
                    "category": f"{module['category']}_cluster",
                    "directory": module["directory"],
                    "files": module.get("files", []),
                    "functions": functions,
                    "structs": module.get("structs", []),
                    "headers": module.get("headers", []),
                    "cluster_type": "misc",
                    "cluster_key": "misc",
                    "function_count": len(functions),
                    "total_lines": sum(self._safe_line_count(func) for func in functions),
                    "parent_module": module["name"],
                    "origin_needs_split": module.get("needs_split", False),
                    "origin_split_reasons": module.get("split_reasons", []),
                    "origin_split_reason": module.get("split_reason", ""),
                    "parent_cohesion_score": module.get("cohesion_score", 0),
                    "confidence": module.get("confidence", "medium"),
                }
            )

        print(f"      拆分为 {len(sub_modules)} 个子模块")
        return sub_modules

    def _identify_function_clusters(self, project_path: str, module_units: List[Dict]) -> List[Dict]:
        """
        识别最终函数簇（cluster_unit），保证单簇规模可控。
        """
        print("识别函数簇...")

        cluster_units = []
        for module in module_units:
            functions = sorted(module.get("functions", []), key=self._function_sort_key)
            if not functions:
                # 空模块不生成函数簇。
                continue

            if (
                len(functions) <= self.MAX_CLUSTER_FUNCTIONS
                and sum(self._safe_line_count(func) for func in functions) <= self.MAX_CLUSTER_LINES
            ):
                # 已经足够小的模块，直接视为一个 cluster_unit，避免过度切碎。
                cluster_units.append(
                    {
                        "module_name": module["name"],
                        "cluster_type": module.get("cluster_type", "module_level"),
                        "cluster_key": module.get("cluster_key", module["name"]),
                        "functions": functions,
                        "files": sorted({self._normalize_path(func.get("file", "")) for func in functions}),
                        "structs": module.get("structs", []),
                        "headers": module.get("headers", []),
                        "total_lines": sum(self._safe_line_count(func) for func in functions),
                    }
                )
                continue

            grouped_by_file = self._group_functions_by_file(functions)
            for file_path, file_funcs in grouped_by_file.items():
                # 模块仍然太大时，再按文件内前缀聚类或 file_local 切块。
                prefix_groups = defaultdict(list)
                for func in file_funcs:
                    prefix_groups[self._extract_function_prefix(func.get("name", ""))].append(func)

                created_group = False
                for prefix, funcs in sorted(prefix_groups.items(), key=lambda item: (-len(item[1]), item[0])):
                    for index, chunk in enumerate(self._chunk_large_group(funcs, "prefix_based", prefix), start=1):
                        cluster_funcs = chunk["functions"]
                        cluster_units.append(
                            {
                                "module_name": module["name"],
                                "cluster_type": "prefix_based",
                                "cluster_key": f"{prefix}_{index:02d}",
                                "functions": cluster_funcs,
                                "files": [file_path],
                                "structs": module.get("structs", []),
                                "headers": module.get("headers", []),
                                "total_lines": sum(self._safe_line_count(func) for func in cluster_funcs),
                            }
                        )
                        created_group = True

                if not created_group:
                    for index, chunk in enumerate(self._chunk_large_group(file_funcs, "file_local", os.path.basename(file_path)), start=1):
                        cluster_funcs = chunk["functions"]
                        cluster_units.append(
                            {
                                "module_name": module["name"],
                                "cluster_type": "file_local",
                                "cluster_key": f"{os.path.basename(file_path)}_{index:02d}",
                                "functions": cluster_funcs,
                                "files": [file_path],
                                "structs": module.get("structs", []),
                                "headers": module.get("headers", []),
                                "total_lines": sum(self._safe_line_count(func) for func in cluster_funcs),
                            }
                        )

        print(f"OK 识别到 {len(cluster_units)} 个函数簇")
        return cluster_units

    def split(self, project_info: Dict, project_analysis: Dict, dependency_graph: Dict) -> Tuple[List[Dict], List[Dict]]:
        """
        公共接口方法：执行完整的模块划分流程
        
        Args:
            project_info: 项目信息（包含 c_files 等）
            project_analysis: 项目分析结果（包含 functions, structs 等）
            dependency_graph: 依赖图（包含 call_graph, struct_usage 等）
            
        Returns:
            (module_units, cluster_units) 元组
        """
        # 先做输入 schema 归一化，避免 parser 输出字段名变化影响模块划分逻辑。
        normalized_project_analysis = dict(project_analysis)
        normalized_project_analysis["functions"] = [
            self._normalize_function_record(func)
            for func in project_analysis.get("functions", [])
        ]
        normalized_project_analysis["structs"] = [
            self._normalize_struct_record(struct)
            for struct in project_analysis.get("structs", [])
        ]

        # 第一步：识别候选子模块（粗粒度）
        candidate_modules = self._identify_candidate_modules(project_info, dependency_graph)
        
        # 第二步：用语义和依赖约束收敛模块（中粒度）
        static_module_units = self._refine_modules_with_semantics(
            candidate_modules, 
            normalized_project_analysis, 
            dependency_graph
        )

        # 第三步：如果配置了 LLM，则用动态查询/动态扩张方式重新判断模块边界。
        # LLM 只看到有限函数事实；所有函数归属最终仍由静态校验兜底。
        llm_module_units = self._llm_guided_module_split(
            project_info,
            normalized_project_analysis,
            dependency_graph,
            static_module_units,
        )
        module_units = llm_module_units or static_module_units

        call_graph = dependency_graph.get("call_graph", {})
        project_function_names = self._build_project_function_set(normalized_project_analysis["functions"])
        module_units = self._refresh_final_module_metrics(
            module_units,
            call_graph,
            project_function_names,
        )
        module_units = self._consolidate_modules_for_planning(
            module_units,
            dependency_graph,
            len(normalized_project_analysis["functions"]),
        )
        module_units = self._refresh_final_module_metrics(
            module_units,
            call_graph,
            project_function_names,
        )
        module_units = self._attach_static_summaries(
            module_units,
            dependency_graph,
            project_function_names,
        )
        
        # 第四步：识别函数簇（细粒度）
        cluster_units = self._identify_function_clusters('', module_units)
        
        return module_units, cluster_units

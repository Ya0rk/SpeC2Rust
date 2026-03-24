from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
from typing import Dict, List, Set, Tuple


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
    MIN_STRUCT_CLUSTER_SIZE = 2
    MIN_PREFIX_CLUSTER_SIZE = 2

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

        print(f"  ✓ 识别到 {len(candidate_modules)} 个候选子模块")
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

        print(f"  ✓ 收敛到 {len(final_modules)} 个子模块")
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

        print(f"✓ 识别到 {len(cluster_units)} 个函数簇")
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
        module_units = self._refine_modules_with_semantics(
            candidate_modules, 
            normalized_project_analysis, 
            dependency_graph
        )

        call_graph = dependency_graph.get("call_graph", {})
        project_function_names = self._build_project_function_set(normalized_project_analysis["functions"])
        module_units = self._refresh_final_module_metrics(
            module_units,
            call_graph,
            project_function_names,
        )
        
        # 第三步：识别函数簇（细粒度）
        cluster_units = self._identify_function_clusters('', module_units)
        
        return module_units, cluster_units

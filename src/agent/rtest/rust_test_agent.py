"""Rust 项目功能测试 Agent（通用，不针对任何特定 C 项目做特殊化）。

工作流程：
1. 把 C 项目的 ``test/`` 目录里所有 ``.sh`` 脚本以及目录里的其它非脚本文件
   （fixture / 期望输出 / 框架脚手架等）通用地拷贝到 Rust 项目的 ``test/`` 目录下。
2. ``cargo build --release`` 生成 Rust 可执行文件。
3. 把 Rust 可执行文件以 ``<bin_name>-rust`` 暴露在 PATH 上，并把用户预编译好的
   同名 C 参考可执行文件以 ``<bin_name>`` 暴露；同时 ``$RUST_BIN`` / ``$C_BIN``
   两个环境变量也都可用。测试脚本可以通过这两个名字做 Rust vs C 对照。
4. 逐个运行 sh 脚本，记录每个用例的退出码、stdout、stderr。
5. 对失败的用例逐个进入 LLM 修复循环：
   - 把项目结构设计文档 (``.cgr_generation_plan.json::project_structure``)、
     失败脚本内容、运行输出一起发给 LLM；
   - LLM 可以请求 C 源码（模仿 ``ContextualRustAgent`` 的 ``CGR_READ`` 形式，
     字段名为 ``cgr_read``）或 Rust 文件片段；
   - LLM 可以以 ``rust_repair_agent`` 的结构化编辑格式给出 ``edits``；
   - 应用编辑后重新 ``cargo build --release`` 并重跑该用例，仍失败则继续下一轮，
     最多 ``max_repair_iterations`` 轮。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# 将 src/ 加入 sys.path，使下面 ``from config.config import Config`` 等导入成立。
_SRC_DIR = Path(__file__).resolve().parents[2]
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from config.config import Config  # noqa: E402
from llm.model import Model  # noqa: E402
from agent.rust_repair_agent import RustRepairAgent  # noqa: E402


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


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

    def short_failure_excerpt(self, max_chars: int = 2000) -> str:
        text = (self.stderr.strip() or self.stdout.strip() or "(no output)")
        if len(text) <= max_chars:
            return text
        return text[-max_chars:]

    def failure_signature(self) -> str:
        material = (
            f"{self.exit_code}|{self.stderr[-800:]}|{self.stdout[-400:]}|{self.trace[-800:]}"
        )
        return hashlib.sha1(material.encode("utf-8", errors="ignore")).hexdigest()[:16]


@dataclass
class TestRunSummary:
    total: int
    passed: int
    failed: int
    results: List[TestCaseResult] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return self.failed == 0 and self.total > 0


# ---------------------------------------------------------------------------
# Agent 主体
# ---------------------------------------------------------------------------


class RustTestAgent:
    """根据 C 项目的 sh 测试脚本，验证翻译后的 Rust 项目功能完整性，并修复失败用例。"""

    def __init__(
        self,
        config: Optional[Config] = None,
        max_repair_iterations: int = 10,
        build_timeout_seconds: int = 600,
        test_timeout_seconds: int = 120,
    ):
        self.config = config or Config()
        self.llm = Model(self.config)
        self.max_repair_iterations = max_repair_iterations
        self.build_timeout_seconds = build_timeout_seconds
        self.test_timeout_seconds = test_timeout_seconds
        # 复用 RustRepairAgent 的本地清洗 / 结构化编辑能力。
        self.repair_helper = RustRepairAgent(config=self.config, max_iterations=1)

    # ---------------------------------------------------------------- public

    def run(
        self,
        rust_project_path: str,
        c_project_path: str,
        binary_name: Optional[str] = None,
    ) -> TestRunSummary:
        rust_project_path = str(Path(rust_project_path).resolve())
        c_project_path = str(Path(c_project_path).resolve())
        bin_name = binary_name or Path(c_project_path).name

        # 0. 定位 C 参考可执行文件（用户预编译并放在 C 项目根目录，与项目目录同名）。
        #    缺失则直接硬失败，不进入迭代修复。
        c_binary = self._locate_c_binary(c_project_path)
        if not c_binary:
            project_name = Path(c_project_path).name
            print(
                f"[rtest] 错误：未在 {c_project_path} 找到与项目同名的 C 参考可执行文件 "
                f"（期望 {project_name} 或 {project_name}.exe）。"
                "请先把 C 项目编译好（与项目目录同名），再运行本 agent。"
            )
            return TestRunSummary(0, 0, 0, [])
        self._c_binary_path = c_binary
        print(f"[rtest] 使用 C 参考可执行文件：{c_binary}")

        # 1. 拷贝 sh 测试脚本
        test_src = self._find_c_test_dir(c_project_path)
        if not test_src:
            print(f"[rtest] 未在 C 项目中找到 test/ 目录：{c_project_path}")
            return TestRunSummary(0, 0, 0, [])
        test_dst = os.path.join(rust_project_path, "test")
        copied = self._copy_tests(test_src, test_dst)
        if not copied:
            print(f"[rtest] {test_src} 内未找到任何 .sh 测试脚本")
            return TestRunSummary(0, 0, 0, [])

        # 2. 编译 Rust 项目
        if not self._cargo_build_release(rust_project_path):
            print("[rtest] cargo build --release 失败，无法运行测试")
            return TestRunSummary(0, 0, 0, [])

        # 命名约定：C 可执行 = <bin_name>；Rust 可执行 = <bin_name>-rust
        rust_bin_name = f"{bin_name}-rust"
        binary_path = self._locate_release_binary(rust_project_path, rust_bin_name)
        if not binary_path:
            expected = os.path.join(rust_project_path, "target", "release", rust_bin_name)
            print(
                f"[rtest] 错误：未找到 Rust 可执行文件 {expected}[.exe]。"
                f" 请确认 Cargo.toml 里 [[bin]] name = \"{rust_bin_name}\"，"
                f" 并已成功执行 cargo build --release。"
            )
            return TestRunSummary(0, 0, 0, [])
        print(f"[rtest] 使用 Rust 可执行文件：{binary_path}")

        # 3. 首次跑全量测试
        summary = self._run_all_tests(test_dst, binary_path, bin_name)
        self._print_summary(summary, label="首次测试结果")
        if summary.all_passed:
            return summary

        # 4. 逐个修复失败用例
        project_structure = self._load_project_structure(rust_project_path)
        source_records = self._load_source_records_for_project(c_project_path)

        # 记录"原本通过"的用例，做回归基线
        baseline_pass_names: Set[str] = {c.name for c in summary.results if c.passed}

        # 失败用例按脚本字节数升序：先修简单的，让上下文逐步收敛
        failing_cases = [c for c in summary.results if not c.passed]

        def _script_size(c: TestCaseResult) -> int:
            try:
                return os.path.getsize(c.script_path)
            except Exception:
                return 1 << 30

        failing_cases.sort(key=_script_size)

        for case in failing_cases:
            fixed = self._repair_failing_case(
                rust_project_path=rust_project_path,
                c_project_path=c_project_path,
                bin_name=bin_name,
                test_dst=test_dst,
                project_structure=project_structure,
                source_records=source_records,
                failing_case=case,
                baseline_pass_names=baseline_pass_names,
            )
            if fixed:
                # 修好的用例自动加入新基线，让后续修复时把它一起做回归保护
                baseline_pass_names.add(case.name)

        # 5. 复测
        final_binary = (
            self._locate_release_binary(rust_project_path, rust_bin_name) or binary_path
        )
        final_summary = self._run_all_tests(test_dst, final_binary, bin_name)
        self._print_summary(final_summary, label="最终测试结果")
        return final_summary

    # ------------------------------------------------------- file management

    def _find_c_test_dir(self, c_project_path: str) -> str:
        for candidate in ("test", "tests"):
            path = os.path.join(c_project_path, candidate)
            if os.path.isdir(path):
                return path
        return ""

    def _copy_tests(self, src_dir: str, dst_dir: str) -> List[str]:
        os.makedirs(dst_dir, exist_ok=True)
        copied: List[str] = []
        for name in os.listdir(src_dir):
            full = os.path.join(src_dir, name)
            if not os.path.isfile(full):
                continue
            if not name.endswith(".sh"):
                continue
            target = os.path.join(dst_dir, name)
            shutil.copy2(full, target)
            self._chmod_executable(target)
            copied.append(target)

        # 通用：把测试目录下所有非 .sh 文件（fixture / 期望输出 / 框架脚手架等）
        # 一并拷过去，避免对具体 C 项目做特殊化。
        for name in os.listdir(src_dir):
            if name.endswith(".sh") or name.startswith("."):
                continue
            full = os.path.join(src_dir, name)
            if os.path.isfile(full):
                try:
                    shutil.copy2(full, os.path.join(dst_dir, name))
                except Exception:
                    pass

        print(f"[rtest] 已拷贝 {len(copied)} 个测试脚本到 {dst_dir}")
        return copied

    @staticmethod
    def _chmod_executable(path: str) -> None:
        try:
            os.chmod(path, 0o755)
        except Exception:
            pass

    # ------------------------------------------------------------- compile

    def _cargo_build_release(self, project_dir: str) -> bool:
        print(f"[rtest] cargo build --release ({project_dir})")
        ok, output = self.repair_helper._run_command(
            "cargo build --release", project_dir, timeout_seconds=self.build_timeout_seconds
        )
        if not ok:
            print(output[-4000:])
        return ok

    def _locate_c_binary(self, c_project_path: str) -> str:
        """约定：C 项目根目录里有一个与目录同名的可执行文件（用户提前编译好）。"""
        if not os.path.isdir(c_project_path):
            return ""
        name = Path(c_project_path).name
        for cand in (name, f"{name}.exe"):
            full = os.path.join(c_project_path, cand)
            if os.path.isfile(full):
                return full
        return ""

    def _locate_release_binary(self, project_dir: str, bin_name: str) -> str:
        """严格定位 ``<project_dir>/target/release/<bin_name>[.exe]``，找不到返回空。

        不做任何兜底，避免悄悄选错二进制；上层 ``run()`` 会据此打印明确错误，
        让用户去修 ``Cargo.toml`` 的 ``[[bin]] name``。
        """
        release_dir = os.path.join(project_dir, "target", "release")
        if not os.path.isdir(release_dir):
            return ""
        for cand in (bin_name, f"{bin_name}.exe"):
            full = os.path.join(release_dir, cand)
            if os.path.isfile(full):
                return full
        return ""

    # ----------------------------------------------------------- run tests

    def _run_all_tests(self, test_dir: str, binary_path: str, bin_name: str) -> TestRunSummary:
        scripts = sorted(Path(test_dir).glob("*.sh"))
        results: List[TestCaseResult] = []
        passed = 0
        for script in scripts:
            result = self._run_single_test(script, binary_path, bin_name)
            results.append(result)
            if result.passed:
                passed += 1
                print(f"  ✓ {result.name} ({result.duration_seconds}s)")
            else:
                print(f"  ✗ {result.name} (exit={result.exit_code})")
        return TestRunSummary(
            total=len(results), passed=passed, failed=len(results) - passed, results=results
        )

    def _run_single_test(
        self,
        script_path: Path,
        binary_path: str,
        bin_name: str,
        capture_trace: bool = True,
    ) -> TestCaseResult:
        """运行单个 sh 测试。

        capture_trace=True 时，若用例失败会再以 ``bash -x`` 复跑一次以收集 trace；
        trace 单独存放在 ``TestCaseResult.trace``，不会污染原始 stderr。
        """
        # 准备 bin 包装目录（命名约定：C 可执行 = <bin_name>；Rust 可执行 = <bin_name>-rust）：
        #   - C 参考二进制（用户预编译，与项目目录同名）暴露为 `<bin_name>`
        #   - Rust 二进制暴露为 `<bin_name>-rust`
        # 同时通过 env 暴露 C_BIN / RUST_BIN 给脚本，方便对照测试
        wrapper_dir = script_path.parent / ".bin"
        wrapper_dir.mkdir(exist_ok=True)

        # Rust wrapper 保留 binary 自身后缀（Windows 上 .exe）
        rust_suffix = Path(binary_path).suffix
        rust_wrapper = wrapper_dir / f"{bin_name}-rust{rust_suffix}"
        try:
            if rust_wrapper.exists():
                rust_wrapper.unlink()
        except Exception:
            pass
        try:
            shutil.copy2(binary_path, rust_wrapper)
            self._chmod_executable(str(rust_wrapper))
        except Exception as exc:
            return TestCaseResult(
                name=script_path.name,
                script_path=str(script_path),
                passed=False,
                exit_code=-1,
                stdout="",
                stderr=f"failed to stage rust binary: {exc}",
            )

        c_binary_path = getattr(self, "_c_binary_path", "") or ""
        if c_binary_path and os.path.isfile(c_binary_path):
            c_suffix = Path(c_binary_path).suffix
            c_wrapper = wrapper_dir / f"{bin_name}{c_suffix}"
            try:
                if c_wrapper.exists():
                    c_wrapper.unlink()
                shutil.copy2(c_binary_path, c_wrapper)
                self._chmod_executable(str(c_wrapper))
            except Exception as exc:
                print(f"    [rtest] 警告：无法 stage C 参考二进制：{exc}")

        env = os.environ.copy()
        env["PATH"] = str(wrapper_dir.resolve()) + os.pathsep + env.get("PATH", "")
        env["RUST_BIN"] = str(Path(binary_path).resolve())
        if c_binary_path:
            env["C_BIN"] = str(Path(c_binary_path).resolve())
        env["LC_ALL"] = env.get("LC_ALL", "C")

        run_dir = script_path.parent / f".run_{script_path.stem}"
        if run_dir.exists():
            shutil.rmtree(run_dir, ignore_errors=True)
        run_dir.mkdir()

        # 把测试目录里非 .sh 的辅助 / fixture 文件拷到 run_dir，避免脚本里
        # 用相对路径读 fixture（如 `cmd in > out`）时找不到文件。
        self._copy_fixtures_into(script_path.parent, run_dir)

        started = time.time()
        try:
            proc = subprocess.run(
                ["bash", str(script_path)],
                cwd=str(run_dir),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.test_timeout_seconds,
            )
            stdout = proc.stdout.decode("utf-8", errors="ignore")
            stderr = proc.stderr.decode("utf-8", errors="ignore")
            exit_code = proc.returncode
            passed = exit_code == 0
        except FileNotFoundError as exc:
            stdout = ""
            stderr = f"bash 不可用，无法运行测试：{exc}"
            exit_code = -1
            passed = False
        except subprocess.TimeoutExpired:
            stdout = ""
            stderr = f"测试超时 (> {self.test_timeout_seconds}s)"
            exit_code = -1
            passed = False

        result = TestCaseResult(
            name=script_path.name,
            script_path=str(script_path),
            passed=passed,
            exit_code=exit_code,
            stdout=stdout[-4000:],
            stderr=stderr[-4000:],
            duration_seconds=round(time.time() - started, 2),
        )

        if not passed and capture_trace:
            result.trace = self._capture_bash_trace(script_path, run_dir, env)

        return result

    def _capture_bash_trace(self, script_path: Path, run_dir: Path, env: Dict[str, str]) -> str:
        """用 ``bash -x`` 复跑失败用例，收集 trace（写入 stderr）。"""
        try:
            proc = subprocess.run(
                ["bash", "-x", str(script_path)],
                cwd=str(run_dir),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.test_timeout_seconds,
            )
            trace = proc.stderr.decode("utf-8", errors="ignore")
        except Exception as exc:
            return f"<trace 捕获失败：{exc}>"
        # bash -x 的 trace 行以 '+ ' 开头；保留完整 stderr 即可，让 LLM 同时看到
        # trace 与真正的错误。只保留尾部，避免过长。
        if len(trace) > 6000:
            trace = trace[-6000:]
        return trace

    def _copy_fixtures_into(self, src_dir: Path, run_dir: Path) -> None:
        try:
            entries = os.listdir(src_dir)
        except Exception:
            return
        for name in entries:
            if name.startswith("."):
                continue
            if name.endswith(".sh"):
                continue
            full = os.path.join(str(src_dir), name)
            if not os.path.isfile(full):
                continue
            try:
                shutil.copy2(full, run_dir / name)
            except Exception:
                pass

    @staticmethod
    def _print_summary(summary: TestRunSummary, label: str = "测试结果") -> None:
        print(f"\n=== {label} ===")
        print(f"total={summary.total} passed={summary.passed} failed={summary.failed}")
        for case in summary.results:
            mark = "✓" if case.passed else "✗"
            print(f"  {mark} {case.name} (exit={case.exit_code}, {case.duration_seconds}s)")

    # ----------------------------------------------------------- repair

    def _load_project_structure(self, rust_project_path: str) -> str:
        plan_path = os.path.join(rust_project_path, ".cgr_generation_plan.json")
        if not os.path.exists(plan_path):
            return ""
        try:
            with open(plan_path, "r", encoding="utf-8") as f:
                plan = json.load(f)
        except Exception as exc:
            print(f"[rtest] 读取 generation_plan 失败：{exc}")
            return ""
        return str(plan.get("project_structure") or "")

    def _load_source_records_for_project(self, c_project_path: str) -> List[Dict]:
        if not c_project_path:
            return []
        repo_root = Path(__file__).resolve().parents[3]
        candidate = repo_root / "src" / "parse" / "res" / f"{Path(c_project_path).name}.json"
        if not candidate.exists():
            print(f"[rtest] 未找到源码 JSON：{candidate}（修复时无法提供 C 源码上下文）")
            return []
        try:
            with open(candidate, "r", encoding="utf-8", errors="ignore") as f:
                payload = json.load(f)
        except Exception as exc:
            print(f"[rtest] 加载源码 JSON 失败：{exc}")
            return []

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

        records: List[Dict] = []
        for item in raw:
            func_defid = item.get("func_defid", "") or ""
            name = item.get("name") or (func_defid.rsplit(":", 1)[-1] if ":" in func_defid else "")
            file_path = ""
            if ":" in func_defid:
                file_path = func_defid.rsplit(":", 1)[0]
            records.append(
                {
                    "name": name or "unknown",
                    "file": file_path,
                    "span": item.get("span", ""),
                    "source": item.get("source", "") or "",
                    "num_lines": item.get("num_lines")
                    or len(str(item.get("source", "")).splitlines()),
                    "func_defid": func_defid,
                }
            )
        return records

    def _build_rust_project_overview(self, project_dir: str, max_files: int = 40) -> str:
        entries: List[str] = []
        if os.path.exists(os.path.join(project_dir, "Cargo.toml")):
            entries.append("- Cargo.toml")
        src_dir = os.path.join(project_dir, "src")
        if os.path.isdir(src_dir):
            for root, _, files in os.walk(src_dir):
                rel_root = os.path.relpath(root, project_dir).replace("\\", "/")
                for name in sorted(files):
                    if not name.endswith(".rs"):
                        continue
                    rel = f"{rel_root}/{name}"
                    full = os.path.join(root, name)
                    try:
                        size = os.path.getsize(full)
                    except Exception:
                        size = 0
                    entries.append(f"- {rel} ({size} bytes)")
                    if len(entries) >= max_files:
                        break
                if len(entries) >= max_files:
                    break
        return "\n".join(entries[:max_files])

    def _build_c_source_index(self, source_records: List[Dict], max_items: int = 80) -> str:
        if not source_records:
            return "（C 项目源码索引未加载）"
        lines: List[str] = []
        for rec in source_records[:max_items]:
            lines.append(
                f"- `{rec.get('name')}` [{rec.get('file')}] "
                f"({rec.get('num_lines', '?')} lines)"
            )
        if len(source_records) > max_items:
            lines.append(
                f"... 另有 {len(source_records) - max_items} 条记录未展开，"
                "可按函数名或文件名通过 cgr_read 请求"
            )
        return "\n".join(lines)

    def _fulfill_c_source_request(
        self, source_records: List[Dict], request: Dict
    ) -> Optional[Dict]:
        if not source_records or not isinstance(request, dict):
            return None
        kind = (request.get("kind") or "function").lower()
        query = (request.get("query") or "").strip()
        if not query:
            return None
        lowered = query.lower()
        if kind == "function":
            for rec in source_records:
                if str(rec.get("name", "")).lower() == lowered:
                    return rec
        # 兜底：在 name / file 上做子串匹配
        for rec in source_records:
            name = str(rec.get("name", "")).lower()
            file_path = str(rec.get("file", "")).replace("\\", "/").lower()
            if lowered == name or lowered in file_path or lowered in name:
                return rec
        return None

    # ------------------------------------------------------ proactive seeding

    _FLAG_RE = re.compile(r"(?<![\w-])(-[A-Za-z]|--[a-z][a-z0-9_-]+)\b")
    _HEREDOC_RE = re.compile(
        r"<<-?\\?(?P<tag>[A-Za-z_][A-Za-z0-9_]*)\b[^\n]*\n(?P<body>.*?)\n(?P=tag)\b",
        re.DOTALL,
    )

    @classmethod
    def _extract_test_flags(cls, script_name: str, script_text: str) -> List[str]:
        """从测试脚本名 + 内容里推断被测的 CLI flag 候选集合（通用）。

        - 脚本里直接出现的 ``-x`` / ``--foo-bar`` 一律收下；
        - 从脚本名后缀派生的候选（如 ``foo-E.sh`` -> ``-E``）必须在脚本正文里
          也真实出现过才采信，避免对不遵循 ``<cmd>-<flag>.sh`` 命名约定的项目
          产生噪声。
        """
        body_flags: Set[str] = set()
        if script_text:
            for m in cls._FLAG_RE.finditer(script_text):
                body_flags.add(m.group(1))

        candidates_from_name: Set[str] = set()
        stem = Path(script_name).stem
        if "-" in stem:
            suffix = stem.split("-", 1)[1].strip()
            if suffix:
                if 1 <= len(suffix) <= 3 and not suffix.isdigit():
                    candidates_from_name.add(f"-{suffix}")
                long_form = suffix.replace("_", "-").lower()
                if len(long_form) >= 2:
                    candidates_from_name.add(f"--{long_form}")

        # 名字派生的候选必须在正文出现过才保留
        validated_from_name = candidates_from_name & body_flags

        flags = body_flags | validated_from_name
        # 剔除明显的 bash 内部 trace/control flag
        bash_internal = {"--", "-x", "-o"}
        return sorted(f for f in flags if f not in bash_internal)

    @staticmethod
    def _extract_test_keywords(script_name: str, script_text: str) -> List[str]:
        """从脚本名 / 内容里提取通用关键字（非 flag），用于在 C / Rust 源码里
        做标识符级匹配。对非 GNU 风格（子命令式 / 库式）项目也有效。"""
        keys: Set[str] = set()
        stem = Path(script_name).stem
        for token in re.split(r"[-_.\s]+", stem):
            if len(token) >= 3 and not token.isdigit():
                keys.add(token.lower())
        if script_text:
            # 抓取 "..." / '...' 里的标识符（长度 >= 3，开头是字母/下划线）
            for m in re.finditer(
                r"['\"]([A-Za-z_][A-Za-z0-9_-]{2,})['\"]", script_text
            ):
                keys.add(m.group(1).lower())
        return sorted(keys)

    @staticmethod
    def _extract_expected_outputs(script_text: str) -> List[str]:
        """提取脚本里 heredoc 写入到 ``exp`` / ``expected`` 文件的内容，用于反作弊检测。"""
        outs: List[str] = []
        if not script_text:
            return outs
        for m in RustTestAgent._HEREDOC_RE.finditer(script_text):
            body = m.group("body")
            if 0 < len(body) < 4000:
                outs.append(body)
        return outs

    def _seed_c_sources_for_flags(
        self,
        flags: List[str],
        source_records: List[Dict],
        keywords: Optional[List[str]] = None,
        limit: int = 4,
    ) -> List[Dict]:
        keywords = keywords or []
        if not source_records:
            return []
        if not flags and not keywords:
            return []
        scored: List[Tuple[int, Dict]] = []
        for rec in source_records:
            src = rec.get("source", "") or ""
            if not src:
                continue
            score = 0
            rec_name = str(rec.get("name", "")).lower()
            rec_file = str(rec.get("file", "")).replace("\\", "/").lower()
            for flag in flags:
                if f'"{flag}"' in src or f"'{flag}'" in src:
                    score += 6
                if len(flag) == 2 and flag.startswith("-"):
                    ch = flag[1]
                    if f"case '{ch}'" in src or f"'{ch}':" in src:
                        score += 4
                if flag.startswith("--"):
                    long_stem = flag[2:]
                    if f'"{long_stem}"' in src:
                        score += 4
                    snake = long_stem.replace("-", "_")
                    if snake and re.search(rf"\b{re.escape(snake)}\b", src):
                        score += 2
            for kw in keywords:
                if not kw:
                    continue
                if kw == rec_name:
                    score += 5
                elif kw in rec_name:
                    score += 3
                elif kw in rec_file:
                    score += 2
                elif re.search(rf"\b{re.escape(kw)}\b", src, re.IGNORECASE):
                    score += 1
            if score > 0:
                scored.append((score, rec))
        scored.sort(key=lambda item: -item[0])
        seen_ids: Set[int] = set()
        out: List[Dict] = []
        for _, rec in scored:
            if id(rec) in seen_ids:
                continue
            seen_ids.add(id(rec))
            out.append(rec)
            if len(out) >= limit:
                break
        return out

    def _seed_rust_files_for_flags(
        self,
        flags: List[str],
        project_dir: str,
        keywords: Optional[List[str]] = None,
        limit: int = 3,
    ) -> Dict[str, str]:
        keywords = keywords or []
        if not flags and not keywords:
            return {}
        src_dir = os.path.join(project_dir, "src")
        if not os.path.isdir(src_dir):
            return {}
        scored: List[Tuple[int, str, str]] = []
        for root, _, files in os.walk(src_dir):
            for name in files:
                if not name.endswith(".rs"):
                    continue
                full = os.path.join(root, name)
                try:
                    with open(full, "r", encoding="utf-8", errors="ignore") as f:
                        text = f.read()
                except Exception:
                    continue
                score = 0
                file_stem = name[:-3].lower()
                for flag in flags:
                    if f'"{flag}"' in text:
                        score += 6
                    if flag.startswith("--"):
                        long_stem = flag[2:]
                        if f'"{long_stem}"' in text:
                            score += 4
                        snake = long_stem.replace("-", "_")
                        if snake and re.search(rf"\b{re.escape(snake)}\b", text):
                            score += 2
                    if len(flag) == 2 and flag.startswith("-"):
                        ch = flag[1]
                        if f"'{ch}'" in text:
                            score += 2
                for kw in keywords:
                    if not kw:
                        continue
                    if kw == file_stem:
                        score += 5
                    elif kw in file_stem:
                        score += 3
                    elif re.search(rf"\b{re.escape(kw)}\b", text, re.IGNORECASE):
                        score += 1
                if score > 0:
                    rel = os.path.relpath(full, project_dir).replace("\\", "/")
                    scored.append((score, rel, text))
        scored.sort(key=lambda item: -item[0])
        return {rel: text for _, rel, text in scored[:limit]}

    # ----------------------------------------------------------- anti-fake-impl

    _FAKE_IMPL_RE = re.compile(
        r"\bunimplemented!\s*\(|\btodo!\s*\(|"
        r'panic!\s*\(\s*"[^"]*(?:not\s+implemented|todo|stub|fixme|placeholder)',
        re.IGNORECASE,
    )

    def _violates_no_fake_impl(self, content: str, expected_outputs: List[str]) -> str:
        if not content:
            return ""
        if self._FAKE_IMPL_RE.search(content):
            return "包含 unimplemented!/todo!/panic 占位标记"
        for exp in expected_outputs:
            block = exp.strip()
            if len(block) >= 32 and block in content:
                return "包含与测试期望输出完全一致的字面量（疑似硬编码作弊）"
        return ""

    def _filter_fake_impl_edits(
        self, edits: List[Dict], expected_outputs: List[str]
    ) -> List[Dict]:
        clean: List[Dict] = []
        for edit in edits:
            if not isinstance(edit, dict):
                continue
            content = edit.get("content") or ""
            reason = self._violates_no_fake_impl(content, expected_outputs)
            if reason:
                print(
                    f"    [rtest] 已拒绝疑似假实现 edit："
                    f"{edit.get('path')}:{edit.get('start_line')} - {reason}"
                )
                continue
            clean.append(edit)
        return clean

    # ----------------------------------------------------------- snapshot

    _SNAPSHOT_TARGETS = ("src", "Cargo.toml", "Cargo.lock", "build.rs")

    def _snapshot_project(self, project_dir: str) -> str:
        snap = tempfile.mkdtemp(prefix="rtest_snap_")
        for item in self._SNAPSHOT_TARGETS:
            src = os.path.join(project_dir, item)
            dst = os.path.join(snap, item)
            try:
                if os.path.isdir(src):
                    shutil.copytree(src, dst)
                elif os.path.isfile(src):
                    shutil.copy2(src, dst)
            except Exception as exc:
                print(f"    [rtest] 快照 {item} 失败：{exc}")
        return snap

    def _restore_project(self, project_dir: str, snapshot_dir: str) -> None:
        for item in self._SNAPSHOT_TARGETS:
            dst = os.path.join(project_dir, item)
            src = os.path.join(snapshot_dir, item)
            try:
                if os.path.isdir(dst):
                    shutil.rmtree(dst, ignore_errors=True)
                elif os.path.isfile(dst):
                    os.remove(dst)
            except Exception:
                pass
            try:
                if os.path.isdir(src):
                    shutil.copytree(src, dst)
                elif os.path.isfile(src):
                    shutil.copy2(src, dst)
            except Exception as exc:
                print(f"    [rtest] 还原 {item} 失败：{exc}")

    def _discard_snapshot(self, snapshot_dir: str) -> None:
        if snapshot_dir and os.path.isdir(snapshot_dir):
            shutil.rmtree(snapshot_dir, ignore_errors=True)

    @staticmethod
    def _is_editable_rust_path(rel_path: str) -> bool:
        """只允许修改/读取翻译出来的 Rust 工程文件，禁止改 sh 测试脚本及测试目录。"""
        normalized = (rel_path or "").replace("\\", "/").strip().lstrip("/").lower()
        if not normalized:
            return False
        # 禁止访问任何测试相关目录
        forbidden_prefixes = ("test/", "tests/", ".bin/", "target/")
        if any(normalized.startswith(p) or f"/{p}" in f"/{normalized}" for p in forbidden_prefixes):
            return False
        # 禁止 sh / bash / Makefile 等非 Rust 工程文件
        forbidden_suffixes = (".sh", ".bash", ".mk", "makefile", "makefile.am", "makefile.in")
        if any(normalized.endswith(s) for s in forbidden_suffixes):
            return False
        # 仅放行 Rust 工程文件
        allowed_suffixes = (".rs", "cargo.toml", "cargo.lock", "build.rs")
        return any(normalized.endswith(s) for s in allowed_suffixes)

    def _filter_disallowed_edits(self, edits: List[Dict]) -> List[Dict]:
        """过滤掉作用于 sh 测试脚本 / 测试目录的编辑请求，确保 LLM 不会篡改测试脚本。"""
        clean: List[Dict] = []
        for edit in edits or []:
            if not isinstance(edit, dict):
                continue
            rel = (edit.get("path") or "").replace("\\", "/")
            if not self._is_editable_rust_path(rel):
                print(f"    [rtest] 已拒绝对非 Rust 源文件的编辑请求：{rel or '(空 path)'}")
                continue
            clean.append(edit)
        return clean

    def _read_script_text(self, path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception:
            return ""

    def _build_repair_prompt(
        self,
        failing_case: TestCaseResult,
        script_content: str,
        project_structure: str,
        rust_overview: str,
        provided_c_records: List[Dict],
        provided_rust_files: Dict[str, str],
        history_summary: str,
        source_records_index: str,
        attempt: int,
        max_attempts: int,
        last_build_error: str = "",
        flags: Optional[List[str]] = None,
        keywords: Optional[List[str]] = None,
        expected_outputs: Optional[List[str]] = None,
        regression_warning: str = "",
    ) -> str:
        c_blocks = []
        for rec in provided_c_records:
            c_blocks.append(
                f"### `{rec.get('name')}` [{rec.get('file')} {rec.get('span')}]"
                f"\n```c\n{rec.get('source','')}\n```"
            )
        rust_blocks = []
        for path, content in provided_rust_files.items():
            rust_blocks.append(f"### {path}\n```rust\n{content}\n```")

        build_error_block = ""
        if last_build_error:
            build_error_block = (
                "\n上一次编辑后 cargo build --release 仍然失败，请优先修复以下编译错误：\n"
                f"```text\n{last_build_error[-2000:]}\n```\n"
            )

        regression_block = ""
        if regression_warning:
            regression_block = (
                "\n[警告] 上一次的修复虽然让本用例通过了，但破坏了原本通过的其它用例，"
                "已被自动回滚。请换一个更聚焦的修复思路：\n"
                f"```text\n{regression_warning[-1500:]}\n```\n"
            )

        flags_line = ", ".join(flags) if flags else "（未识别）"
        keywords_line = ", ".join(keywords) if keywords else "（未识别）"
        expected_block = ""
        if expected_outputs:
            chunks = []
            for idx, body in enumerate(expected_outputs[:3], start=1):
                chunks.append(
                    f"#{idx} (脚本中作为期望输出存在，禁止把这段字面量直接复制进 Rust 源码)\n"
                    f"```text\n{body[:1200]}\n```"
                )
            expected_block = "\n脚本中检测到的期望输出片段（只用于你理解被测行为，不允许"
            expected_block += "在 Rust 里硬编码这些字符串作为返回值）：\n"
            expected_block += "\n".join(chunks) + "\n"

        trace_block = ""
        if failing_case.trace:
            trace_block = (
                "\nbash -x 复跑 trace（行首 `+ ` 是被执行的命令，最后一条非 `+` 行通常就是真正"
                "失败的地方）：\n"
                f"```text\n{failing_case.trace[-3500:]}\n```\n"
            )

        return f"""你正在修复一个由 C 项目翻译而来的 Rust 项目，让它通过 sh 功能测试。
当前用例失败（第 {attempt}/{max_attempts} 轮修复）。

测试脚本运行环境约定（不需要你修改脚本，只需理解）：
- 脚本里 `<bin_name>` 命令指 C 参考可执行文件（用户预编译的同名 binary），
  等价于环境变量 `$C_BIN`
- 脚本里 `<bin_name>-rust` 命令指 Rust 可执行文件，等价于环境变量 `$RUST_BIN`
- 测试经常做「Rust 输出 vs C 输出」对照（如 `diff <($C_BIN args) <($RUST_BIN args)`），
  脚本失败通常意味着 Rust 实现与 C 参考行为不一致，请围绕 C 实现修 Rust

被测特征推断（来自脚本名 / 内容；可能是 CLI flag、子命令、关键字符串，不针对任何特定项目风格）：
- flag 候选：{flags_line}
- 关键字候选：{keywords_line}
（请优先去 C 源码里找处理这些参数 / 子命令 / 输入特征的代码路径，再修对应的 Rust 实现，
 而不是猜 Rust 该返回什么）

测试脚本：{failing_case.name}
```bash
{script_content}
```

最近一次执行结果：
- exit_code: {failing_case.exit_code}
- stdout (尾部):
```
{failing_case.stdout}
```
- stderr (尾部):
```
{failing_case.stderr}
```
{trace_block}{expected_block}{build_error_block}{regression_block}
项目结构设计文档（spec agent 产出，作为修改指引）：
```
{project_structure or '（未提供，请保守地只改与失败相关的文件）'}
```

Rust 项目概览：
```
{rust_overview}
```

C 源码索引（按需请求；模仿 ContextualRustAgent 的语义按函数名或文件名请求）：
{source_records_index}

已经提供给你的 C 源码（首轮已根据被测特征自动注入了相关函数，请优先看这部分）：
{chr(10).join(c_blocks) if c_blocks else '（无）'}

已经提供给你的 Rust 文件（首轮已根据被测特征自动注入了最相关的 Rust 文件）：
{chr(10).join(rust_blocks) if rust_blocks else '（无）'}

历史摘要：
{history_summary or '（无）'}

只返回 JSON，不要任何解释，结构如下：
{{
  "summary": "本轮分析（必须明确说明：被测特征在 C 里是怎么实现 / 处理的，当前 Rust 哪一段缺失或错误）",
  "cgr_read": [
    {{"kind": "function", "query": "C 函数名"}},
    {{"kind": "file", "query": "C 文件名或相对路径"}}
  ],
  "rust_read_requests": [
    {{"path": "src/<your_module>.rs"}}
  ],
  "edits": [
    {{
      "path": "src/<your_module>.rs",
      "mode": "replace_range",
      "start_line": 10,
      "end_line": 20,
      "content": "替换后的合法 Rust 片段"
    }}
  ],
  "complete": false,
  "updated_summary": "更新后的简短记忆"
}}

要求：
1. 只允许局部编辑：replace_range / delete_range / insert_before / insert_after。
2. 行号必须基于已经读到的 Rust 文件实际行号。如果还没读到目标 Rust 文件，
   请先用 rust_read_requests 把它读出来，本轮可以不返回 edits。
3. 如果需要 C 源码加深理解，使用 cgr_read（kind 为 "function" 或 "file"，
   query 为名字或相对路径），下一轮我会把 C 源码贴给你。
4. 严禁修改任何 sh 测试脚本或 test/ 目录下的任何文件；只允许编辑翻译出来的 Rust 工程
   （`*.rs` / `Cargo.toml` 等）。任何指向 test/、tests/ 或以 `.sh` 结尾的 path 都会被
   程序直接拒绝。也不要修改与本次失败测试无关的文件。
5. **严禁假实现**：禁止用 `unimplemented!()` / `todo!()` / `panic!("not implemented")` /
   `panic!("stub")` 等占位写法；也禁止把脚本里检测到的期望输出字面量原样塞进 Rust 源码
   作为返回值。这两种写法会被程序自动驳回。修复必须基于 C 源码的真实逻辑。
6. 如果你的修改让本用例通过但导致其它原本通过的用例失败（回归），整次修改会被回滚，
   请优先做最小改动 / 只修与本用例特征相关的代码路径。
7. 如果当前材料不足以安全编辑，可以只请求材料；下一轮你会看到响应。
"""

    def _repair_failing_case(
        self,
        rust_project_path: str,
        c_project_path: str,
        bin_name: str,
        test_dst: str,
        project_structure: str,
        source_records: List[Dict],
        failing_case: TestCaseResult,
        baseline_pass_names: Optional[Set[str]] = None,
    ) -> bool:
        print(f"\n[rtest] --- 修复失败用例：{failing_case.name} ---")
        rust_overview = self._build_rust_project_overview(rust_project_path)
        source_index = self._build_c_source_index(source_records)
        script_content = self._read_script_text(failing_case.script_path)
        baseline_pass_names = baseline_pass_names or set()

        # 若初始用例没有 trace（首次跑时跳过了），现在补一次
        if not failing_case.trace and os.path.exists(failing_case.script_path):
            current_binary = self._locate_release_binary(
                rust_project_path, f"{bin_name}-rust"
            )
            if current_binary:
                refreshed = self._run_single_test(
                    Path(failing_case.script_path), current_binary, bin_name, capture_trace=True
                )
                failing_case.trace = refreshed.trace

        # 通用：推断被测特征（flag + 关键字），提取期望输出
        flags = self._extract_test_flags(failing_case.name, script_content)
        keywords = self._extract_test_keywords(failing_case.name, script_content)
        expected_outputs = self._extract_expected_outputs(script_content)
        if flags:
            print(f"  [rtest] 推断被测 flag：{', '.join(flags)}")
        if keywords:
            print(f"  [rtest] 推断被测关键字：{', '.join(keywords)}")

        provided_c_records: List[Dict] = []
        provided_rust_files: Dict[str, str] = {}

        # 首轮：根据 flag + 关键字主动注入相关 C 源码 + 相关 Rust 文件（通用打分，
        # 不针对任何具体项目；若都没有线索则跳过 seeding，由后续 LLM cgr_read 兜底）
        seeded_c = self._seed_c_sources_for_flags(flags, source_records, keywords=keywords)
        for rec in seeded_c:
            provided_c_records.append(rec)
            print(
                f"  [rtest] 首轮注入 C 源码：{rec.get('name')} [{rec.get('file')}]"
            )
        seeded_rust = self._seed_rust_files_for_flags(
            flags, rust_project_path, keywords=keywords
        )
        provided_rust_files.update(seeded_rust)
        for rel in seeded_rust:
            print(f"  [rtest] 首轮注入 Rust 文件：{rel}")

        history_summary = ""
        last_build_error = ""
        regression_warning = ""
        last_failure_signature = failing_case.failure_signature()
        stall_count = 0

        # 在动手修之前，先打个项目快照，便于回归回滚
        snapshot_dir = self._snapshot_project(rust_project_path)

        for attempt in range(1, self.max_repair_iterations + 1):
            print(f"  [rtest] 修复迭代 {attempt}/{self.max_repair_iterations}")
            prompt = self._build_repair_prompt(
                failing_case=failing_case,
                script_content=script_content,
                project_structure=project_structure,
                rust_overview=rust_overview,
                provided_c_records=provided_c_records,
                provided_rust_files=provided_rust_files,
                history_summary=history_summary,
                source_records_index=source_index,
                attempt=attempt,
                max_attempts=self.max_repair_iterations,
                last_build_error=last_build_error,
                flags=flags,
                keywords=keywords,
                expected_outputs=expected_outputs,
                regression_warning=regression_warning,
            )
            regression_warning = ""  # 仅在下一轮提示一次
            self.llm.set_request_label(f"测试修复 {failing_case.name} #{attempt}")
            reply = self.llm.generate(
                [
                    {
                        "role": "system",
                        "content": (
                            "你是经验丰富的 Rust 修复助手，擅长根据 sh 测试脚本失败信息"
                            "定位并修复 C->Rust 翻译产物中的功能缺陷。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ]
            )
            text = reply[0] if isinstance(reply, list) else reply
            payload = self.repair_helper._extract_json_payload(text)
            if not isinstance(payload, dict):
                print("    [rtest] LLM 返回不可解析为 JSON，终止本用例修复")
                return False

            history_summary = (
                payload.get("updated_summary") or payload.get("summary") or history_summary
            ).strip()

            cgr_requests = payload.get("cgr_read") or payload.get("c_source_requests") or []
            rust_read_requests = payload.get("rust_read_requests") or []
            raw_edits = self._filter_disallowed_edits(payload.get("edits") or [])
            edits = self._filter_fake_impl_edits(raw_edits, expected_outputs)
            if raw_edits and not edits:
                history_summary += (
                    "\n[系统] 上一轮提交的所有 edit 都被反作弊检测拒绝，"
                    "请根据 C 源码真正实现该 flag 的逻辑，而不是占位/复读期望输出。"
                )

            new_material = False
            for req in cgr_requests:
                rec = self._fulfill_c_source_request(source_records, req)
                if rec and rec not in provided_c_records:
                    provided_c_records.append(rec)
                    new_material = True
                    print(f"    [rtest] 提供 C 源码：{rec.get('name')} [{rec.get('file')}]")

            for req in rust_read_requests:
                rel = (req.get("path") if isinstance(req, dict) else str(req) or "").replace(
                    "\\", "/"
                )
                if not rel or rel in provided_rust_files:
                    continue
                if not self._is_editable_rust_path(rel):
                    print(f"    [rtest] 拒绝读取非 Rust 源文件：{rel}")
                    continue
                content = self.repair_helper._read_file_slice(rust_project_path, rel)
                if content:
                    provided_rust_files[rel] = content
                    new_material = True
                    print(f"    [rtest] 提供 Rust 文件：{rel} ({len(content)} chars)")

            if edits:
                applied, audit = self.repair_helper._apply_structured_edits_with_audit(
                    rust_project_path, edits
                )
                print(f"    [rtest] 应用编辑：applied={applied}, edits={len(edits)}")
                if applied:
                    # 编辑后被改动的 rust 文件，刷新（或首次加入）已提供给 LLM 的内容，
                    # 让下一轮 LLM 能看到自己刚改完的最新版本，避免基于旧行号做新编辑。
                    edited_paths = {
                        (e.get("path") or "").replace("\\", "/")
                        for e in edits
                        if isinstance(e, dict) and e.get("path")
                    }
                    for rel in edited_paths:
                        if not rel:
                            continue
                        refreshed = self.repair_helper._read_file_slice(
                            rust_project_path, rel
                        )
                        if refreshed:
                            provided_rust_files[rel] = refreshed

                    if not self._cargo_build_release(rust_project_path):
                        # 重新读取错误，反馈给下一轮
                        ok, build_output = self.repair_helper._run_command(
                            "cargo build --release",
                            rust_project_path,
                            timeout_seconds=self.build_timeout_seconds,
                        )
                        last_build_error = build_output if not ok else ""
                        print("    [rtest] 修复后编译失败，将编译错误带入下一轮")
                        history_summary += "\n[系统] 上一次编辑导致编译失败，请优先修复编译错误。"
                        continue

                    last_build_error = ""
                    new_binary = self._locate_release_binary(
                        rust_project_path, f"{bin_name}-rust"
                    )
                    if not new_binary:
                        print("    [rtest] 编译产物缺失，跳过本轮验证")
                        continue
                    new_result = self._run_single_test(
                        Path(failing_case.script_path), new_binary, bin_name
                    )
                    failing_case.passed = new_result.passed
                    failing_case.exit_code = new_result.exit_code
                    failing_case.stdout = new_result.stdout
                    failing_case.stderr = new_result.stderr
                    failing_case.trace = new_result.trace
                    failing_case.duration_seconds = new_result.duration_seconds

                    if new_result.passed:
                        # 回归检查：跑一遍原本通过的用例，看是否被改坏
                        regressed = self._check_regression(
                            test_dst, new_binary, bin_name, baseline_pass_names, failing_case.name
                        )
                        if regressed:
                            print(
                                f"    [rtest] ⚠ 修复 {failing_case.name} 引入回归："
                                f"{', '.join(sorted(regressed))}，回滚本次编辑"
                            )
                            self._restore_project(rust_project_path, snapshot_dir)
                            # 重建以恢复二进制；失败也无所谓，下一轮会再 build
                            self._cargo_build_release(rust_project_path)
                            regression_warning = (
                                "本次 edits 让 "
                                f"{failing_case.name} 通过，但同时让以下用例回归失败："
                                + ", ".join(sorted(regressed))
                                + "。请只修与本用例特征相关的代码路径。"
                            )
                            failing_case.passed = False
                            continue
                        print(f"    [rtest] ✓ 用例 {failing_case.name} 修复成功（无回归）")
                        self._discard_snapshot(snapshot_dir)
                        return True

                    # 仍失败：stall 检测
                    new_sig = new_result.failure_signature()
                    if new_sig == last_failure_signature:
                        stall_count += 1
                    else:
                        stall_count = 0
                        last_failure_signature = new_sig
                    if stall_count >= 2:
                        print(
                            f"    [rtest] 连续 {stall_count + 1} 轮失败签名相同，"
                            "判定为停滞，终止本用例修复"
                        )
                        self._restore_project(rust_project_path, snapshot_dir)
                        self._cargo_build_release(rust_project_path)
                        self._discard_snapshot(snapshot_dir)
                        return False

                    print(
                        f"    [rtest] 用例仍失败 (exit={new_result.exit_code})，"
                        f"继续下一轮"
                    )
                    continue

            if payload.get("complete"):
                print("    [rtest] LLM 标记 complete=true，但用例仍未通过，终止本用例修复")
                self._discard_snapshot(snapshot_dir)
                return False

            if not new_material and not edits:
                print("    [rtest] LLM 既没请求材料也没产生编辑，终止本用例修复")
                self._discard_snapshot(snapshot_dir)
                return False

        print(f"  [rtest] 已达最大修复轮数，仍未修复 {failing_case.name}")
        self._discard_snapshot(snapshot_dir)
        return False

    def _check_regression(
        self,
        test_dir: str,
        binary_path: str,
        bin_name: str,
        baseline_pass_names: Set[str],
        skip_case_name: str,
    ) -> Set[str]:
        """跑一遍 baseline 通过过的用例，返回此次回归失败的用例名集合。"""
        if not baseline_pass_names:
            return set()
        regressed: Set[str] = set()
        for script in sorted(Path(test_dir).glob("*.sh")):
            if script.name == skip_case_name:
                continue
            if script.name not in baseline_pass_names:
                continue
            r = self._run_single_test(script, binary_path, bin_name, capture_trace=False)
            if not r.passed:
                regressed.add(script.name)
        return regressed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rust 项目功能测试 / 修复 Agent")
    parser.add_argument("--rust-project-path", required=True, help="Rust 项目根目录")
    parser.add_argument("--c-project-path", required=True, help="原始 C 项目根目录")
    parser.add_argument(
        "--binary-name",
        default="",
        help="Rust 可执行文件期望名（默认与 C 项目目录名相同）",
    )
    parser.add_argument(
        "--config-file",
        default=str(Path(__file__).resolve().parents[3] / "local_config.json"),
        help="LLM 配置文件路径",
    )
    parser.add_argument("--max-repair-iterations", type=int, default=5)
    parser.add_argument("--build-timeout-seconds", type=int, default=600)
    parser.add_argument("--test-timeout-seconds", type=int, default=120)
    return parser


def main() -> int:
    parser = _build_argparser()
    args = parser.parse_args()
    config = Config(config_path=args.config_file)
    agent = RustTestAgent(
        config=config,
        max_repair_iterations=args.max_repair_iterations,
        build_timeout_seconds=args.build_timeout_seconds,
        test_timeout_seconds=args.test_timeout_seconds,
    )
    summary = agent.run(
        rust_project_path=args.rust_project_path,
        c_project_path=args.c_project_path,
        binary_name=args.binary_name or None,
    )
    if summary.total == 0:
        return 2
    return 0 if summary.all_passed else 1


if __name__ == "__main__":
    sys.exit(main())

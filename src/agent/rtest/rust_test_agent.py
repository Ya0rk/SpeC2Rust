"""Rust 项目功能测试 / 修复 Agent（重构后）。

本文件只负责**编排**：

- 定位 C 参考二进制、拷贝测试脚本、首次跑测试
- 逐个失败用例进入 LLM 修复循环
- 回归检测与回滚
- 最终复测

具体实现拆到了 signals / source_loader / seeding / snapshot / test_runner /
repair_prompt / repair_adapter 等模块，便于单独测试与维护。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# 将 src/ 加入 sys.path，使 ``from config.config import Config`` 等导入成立。
_SRC_DIR = Path(__file__).resolve().parents[2]
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from config.config import Config  # noqa: E402
from llm.model import Model  # noqa: E402
from agent.rust_repair_agent import RustRepairAgent  # noqa: E402

from .constants import (  # noqa: E402
    C_SOURCE_INDEX_MAX_ITEMS,
    PROJECT_OVERVIEW_MAX_FILES,
    PROMPT_MATERIAL_BUDGET_CHARS,
    SEED_C_LIMIT,
    SEED_RUST_LIMIT,
    STALL_SAME_SIGNATURE_ROUNDS,
)
from .c_project_builder import CProjectBuilder  # noqa: E402
from .log_agent import LogAgent  # noqa: E402
from .material_policy import (  # noqa: E402
    SMALL_FILE_WHOLE_FILE_CHARS,
    read_text_file_slice,
    should_upgrade_line_range_to_whole_file,
)
from .models import TestCaseResult, TestRunSummary  # noqa: E402
from .repair_adapter import RepairAdapter  # noqa: E402
from .repair_prompt import MaterialBudget, build_repair_prompt  # noqa: E402
from .response_contract import RepairResponseContract  # noqa: E402
from .runtime_probe import RuntimeProbeService  # noqa: E402
from .seeding import seed_c_sources, seed_rust_files  # noqa: E402
from .signals import (  # noqa: E402
    extract_expected_outputs,
    extract_test_flags,
    extract_test_keywords,
    violates_no_fake_impl,
)
from .snapshot import ProjectSnapshot, SnapshotError  # noqa: E402
from .source_loader import (  # noqa: E402
    CSourceIndex,
    build_source_index_display,
    load_source_records,
)
from .suite_repair_coordinator import SuiteRepairContext, SuiteRepairCoordinator  # noqa: E402
from .test_runner import TestRunner  # noqa: E402


EDIT_REGION_BUCKET_LINES = 150
AUTO_CONTEXT_LINES = 180


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class RustTestAgent:
    """根据 C 项目的 sh 测试脚本，验证翻译后的 Rust 项目功能完整性，并修复失败用例。"""

    def __init__(
        self,
        config: Optional[Config] = None,
        max_repair_iterations: int = 20,
        max_suite_repair_cycles: int = 3,
        build_timeout_seconds: int = 600,
        test_timeout_seconds: int = 30,
        verbose: bool = False,
        source_records_path: Optional[str] = None,
        translate_tests: bool = False,
        enable_log_agent: bool = False,
        max_debug_probes: int = 6,
        prompt_budget_chars: int = PROMPT_MATERIAL_BUDGET_CHARS,
        allow_c_materials: bool = True,
    ):
        self.config = config or Config()
        self.llm = Model(self.config)
        self.max_repair_iterations = max_repair_iterations
        self.max_suite_repair_cycles = max_suite_repair_cycles
        self.build_timeout_seconds = build_timeout_seconds
        self.test_timeout_seconds = test_timeout_seconds
        self.verbose = verbose
        self.source_records_path = source_records_path
        self.translate_tests = translate_tests
        self.enable_log_agent = enable_log_agent
        self.max_debug_probes = max(1, max_debug_probes)
        self.prompt_budget_chars = max(1, int(prompt_budget_chars or PROMPT_MATERIAL_BUDGET_CHARS))
        self.allow_c_materials = bool(allow_c_materials)

        # 复用 RustRepairAgent 的本地清洗 / 结构化编辑能力，通过 adapter 访问
        # 它的私有方法，避免耦合未来 RustRepairAgent 的重构。
        self._repair_helper = RustRepairAgent(config=self.config, max_iterations=1)
        self.adapter = RepairAdapter(self._repair_helper)
        self.runtime_probe_service = RuntimeProbeService(
            self._locate_release_binary,
            test_timeout_seconds=self.test_timeout_seconds,
            build_timeout_seconds=self.build_timeout_seconds,
        )

    # ---------------------------------------------------------------- public

    def run(
        self,
        rust_project_path: str,
        c_project_path: str,
        binary_name: Optional[str] = None,
    ) -> TestRunSummary:
        rust_project_path = str(Path(rust_project_path).resolve())
        c_project_path = str(Path(c_project_path).resolve())
        self._repair_helper.configure_context_sources(c_project_path=c_project_path)
        bin_name = binary_name or self._infer_bin_name(c_project_path, rust_project_path)
        if not binary_name:
            print(f"[rtest] 自动推断 bin_name = {bin_name}（C 项目目录 / Cargo.toml）")

        c_build = CProjectBuilder(timeout_seconds=self.build_timeout_seconds).clean_and_build(
            c_project_path, expected_bin_name=bin_name
        )
        if not c_build.ok:
            print(f"[rtest] C 项目构建/校验失败：{c_build.error}")
            if c_build.stdout.strip():
                print(c_build.stdout[-4000:])
            if c_build.stderr.strip():
                print(c_build.stderr[-4000:])
            return TestRunSummary(0, 0, 0, [])
        c_binary = c_build.binary_path
        print(f"[rtest] 使用 C 参考可执行文件：{c_binary}")
        self.runtime_probe_service.configure_c_target(c_project_path, c_binary)
        print(f"[rtest] LogAgent：{'开启' if self.enable_log_agent else '关闭'}")

        test_src = c_build.test_dir
        test_dst = os.path.join(rust_project_path, "test")
        copied_files = self._copy_test_tree(test_src, test_dst)
        print(f"[rtest] 已整体复制测试目录：{test_src} -> {test_dst} ({copied_files} 个文件)")
        self._ensure_test_framework_shim(test_dst)

        if self.translate_tests:
            print(
                "[rtest] 忽略 --translate-tests：测试脚本已由人工预处理并作为只读基准，"
                "不允许 LLM 生成或改写 .sh"
            )
        # 测试脚本只读：始终使用预处理后原样复制的 sh；TestRunner 只负责
        # Rust/C wrapper、srcdir/abs_srcdir 等执行环境适配。
        scripts = self._collect_original_test_scripts(test_dst)
        if not scripts:
            print(f"[rtest] {test_src} 内未找到任何 .sh 测试脚本")
            return TestRunSummary(0, 0, 0, [])

        if not self._cargo_build_release(rust_project_path):
            print("[rtest] cargo build --release 失败，无法运行测试")
            return TestRunSummary(0, 0, 0, [])

        rust_bin_name = f"{bin_name}-rust"
        binary_path = self._locate_release_binary(rust_project_path, rust_bin_name)
        if not binary_path:
            expected = os.path.join(rust_project_path, "target", "release", rust_bin_name)
            print(
                f"[rtest] Error: Rust executable not found at {expected}[.exe]."
                f" Please confirm that Cargo.toml has [[bin]] name = \"{rust_bin_name}\","
                f" and that `cargo build --release` completed successfully."
            )
            return TestRunSummary(0, 0, 0, [])
        print(f"[rtest] 使用 Rust 可执行文件：{binary_path}")

        runner = TestRunner(
            test_dir=test_dst,
            bin_name=bin_name,
            timeout_seconds=self.test_timeout_seconds,
            enable_logging=self.enable_log_agent,
        )
        runner.stage(binary_path, c_binary)
        try:
            summary = runner.run_all(scripts)
            self._print_summary(summary, label="首次测试结果")
            if summary.all_passed:
                return summary

            project_structure = self._load_project_structure(rust_project_path)
            source_index = load_source_records(
                c_project_path, explicit_path=self.source_records_path
            )
            summary = SuiteRepairCoordinator(
                self,
                SuiteRepairContext(
                    rust_project_path=rust_project_path,
                    bin_name=bin_name,
                    runner=runner,
                    project_structure=project_structure,
                    source_index=source_index,
                    summary=summary,
                    scripts=scripts,
                    initial_binary_path=binary_path,
                    max_suite_repair_cycles=self.max_suite_repair_cycles,
                ),
            ).run()
            self._print_summary(summary, label="最终测试结果")
            return summary
        finally:
            runner.cleanup()

    def _repair_suite_until_stable(
        self,
        *,
        rust_project_path: str,
        bin_name: str,
        runner: TestRunner,
        project_structure: str,
        source_index: CSourceIndex,
        summary: TestRunSummary,
        scripts: List[Path],
        test_dst: str,
        initial_binary_path: str,
    ) -> TestRunSummary:
        return SuiteRepairCoordinator(
            self,
            SuiteRepairContext(
                rust_project_path=rust_project_path,
                bin_name=bin_name,
                runner=runner,
                project_structure=project_structure,
                source_index=source_index,
                summary=summary,
                scripts=scripts,
                initial_binary_path=initial_binary_path,
                max_suite_repair_cycles=self.max_suite_repair_cycles,
            ),
        ).run()

    # ----------------------------------------------------- file management

    @staticmethod
    def _find_c_test_dir(c_project_path: str) -> str:
        for candidate in ("test", "tests"):
            path = os.path.join(c_project_path, candidate)
            if os.path.isdir(path):
                return path
        return ""

    @staticmethod
    def _copy_test_tree(src_dir: str, dst_dir: str) -> int:
        """Recursively copy the C project's test directory before staging wrappers."""
        src_path = Path(src_dir)
        dst_path = Path(dst_dir)
        if dst_path.exists():
            shutil.rmtree(dst_path, ignore_errors=True)

        def ignore_generated(_dir: str, names: List[str]) -> Set[str]:
            ignored: Set[str] = set()
            for name in names:
                if (
                    name == ".bin"
                    or name.startswith(".run_")
                    or name == "__pycache__"
                    or name.endswith(".pyc")
                    or name.endswith(".orig")
                    or name.endswith(".llm_raw.txt")
                    or name.endswith(".invalid")
                ):
                    ignored.add(name)
            return ignored

        shutil.copytree(src_path, dst_path, ignore=ignore_generated)
        copied = 0
        for path in dst_path.rglob("*"):
            if path.is_file():
                copied += 1
        return copied

    @staticmethod
    def _copy_tests(src_dir: str, dst_dir: str) -> List[str]:
        import shutil

        os.makedirs(dst_dir, exist_ok=True)
        copied: List[str] = []
        for name in os.listdir(src_dir):
            full = os.path.join(src_dir, name)
            if not os.path.isfile(full) or not name.endswith(".sh"):
                continue
            target = os.path.join(dst_dir, name)
            shutil.copy2(full, target)
            if os.name != "nt":
                try:
                    os.chmod(target, 0o755)
                except OSError:
                    pass
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
                except OSError:
                    pass

        print(f"[rtest] 已拷贝 {len(copied)} 个测试脚本到 {dst_dir}")
        return copied

    @staticmethod
    def _discover_shell_scripts(test_dir: Path) -> List[Path]:
        scripts: List[Path] = []
        for path in sorted(test_dir.rglob("*.sh")):
            parts = set(path.relative_to(test_dir).parts)
            if ".bin" in parts or any(part.startswith(".run_") for part in parts):
                continue
            if path.name.endswith(".sh.orig"):
                continue
            scripts.append(path)
        return scripts

    @staticmethod
    def _collect_original_test_scripts(dst_dir: str) -> List[Path]:
        """Normalize copied shell tests and keep .orig backups for repair diffs."""
        dst_path = Path(dst_dir)
        copied: List[Path] = []
        for script in RustTestAgent._discover_shell_scripts(dst_path):
            try:
                body = script.read_text(encoding="utf-8", errors="ignore")
                script.write_text(body, encoding="utf-8", newline="\n")
                if os.name != "nt":
                    os.chmod(script, 0o755)
                orig = script.with_name(f"{script.name}.orig")
                orig.write_text(body, encoding="utf-8", newline="\n")
            except OSError as exc:
                print(f"[rtest] 处理原始测试脚本失败：{script}: {exc}")
                continue
            copied.append(script)

        print(f"[rtest] 已原样准备 {len(copied)} 个 sh 测试脚本")
        return copied

    @staticmethod
    def _copy_test_fixtures(src_dir: str, dst_dir: str) -> int:
        """Copy non-shell test fixtures while leaving sh scripts to the translator."""
        import shutil

        os.makedirs(dst_dir, exist_ok=True)
        copied = 0
        for name in os.listdir(src_dir):
            if name.startswith(".") or name.endswith(".sh"):
                continue
            src = os.path.join(src_dir, name)
            dst = os.path.join(dst_dir, name)
            if os.path.isfile(src):
                try:
                    shutil.copy2(src, dst)
                    copied += 1
                except OSError:
                    pass
        return copied

    @staticmethod
    def _copy_original_test_scripts(src_dir: str, dst_dir: str) -> List[Path]:
        """Copy original shell tests without LLM rewriting.

        Existing generated ``*.sh`` files in the destination are removed first
        to avoid stale translated tests participating in the next run.
        """
        src_path = Path(src_dir)
        dst_path = Path(dst_dir)
        dst_path.mkdir(parents=True, exist_ok=True)

        for old in dst_path.glob("*.sh"):
            try:
                old.unlink()
            except OSError:
                pass

        copied: List[Path] = []
        for entry in sorted(src_path.iterdir()):
            if not entry.is_file() or not entry.name.endswith(".sh"):
                continue
            target = dst_path / entry.name
            try:
                shutil.copy2(entry, target)
                body = entry.read_text(encoding="utf-8", errors="ignore")
                target.write_text(body, encoding="utf-8", newline="\n")
                if os.name != "nt":
                    os.chmod(target, 0o755)
                (dst_path / f"{entry.name}.orig").write_text(
                    body,
                    encoding="utf-8",
                    newline="\n",
                )
            except OSError as exc:
                print(f"[rtest] 拷贝原始测试脚本失败：{entry.name}: {exc}")
                continue
            copied.append(target)

        print(f"[rtest] 已原样复制 {len(copied)} 个 sh 测试脚本到 {dst_dir}")
        return copied

    @staticmethod
    def _ensure_test_framework_shim(test_dst: str) -> None:
        """Create a minimal tests/init.sh for extracted GNU-style test scripts.

        Many datasets keep only a few ``test/*.sh`` files from larger projects
        such as coreutils. Those scripts often source ``../../tests/init.sh``.
        In the generated Rust project layout, ``test_dst`` is
        ``output/<project>/<rust-project>/test``, so ``../../tests/init.sh``
        resolves to ``output/<project>/tests/init.sh``.
        """
        test_dir = Path(test_dst).resolve()
        if len(test_dir.parents) < 2:
            return
        shim_dir = test_dir.parents[1] / "tests"
        shim_path = shim_dir / "init.sh"
        try:
            shim_dir.mkdir(parents=True, exist_ok=True)
            shim_path.write_text(_MINIMAL_TEST_INIT_SH, encoding="utf-8", newline="\n")
            if os.name != "nt":
                os.chmod(shim_path, 0o755)
            print(f"[rtest] 已准备测试框架 shim：{shim_path}")
        except OSError as exc:
            print(f"[rtest] 写入测试框架 shim 失败：{exc}")

    # ----------------------------------------------------------- compile

    def _cargo_build_release(self, project_dir: str) -> bool:
        print(f"[rtest] cargo build --release ({project_dir})")
        ok, output = self.adapter.run_command(
            "cargo build --release", project_dir, timeout_seconds=self.build_timeout_seconds
        )
        if not ok:
            print(output[-4000:])
        return ok

    @staticmethod
    def _infer_bin_name(c_project_path: str, rust_project_path: str) -> str:
        """Pick the test program name shared by C scripts and Cargo manifest.

        Resolution order:

        1. If the Rust crate's ``Cargo.toml`` declares a single ``[[bin]]
           name`` (or only one ``[[bin]]`` entry), use it stripped of any
           ``-rust`` suffix. This is the strongest signal because the test
           scripts call ``./<bin>`` and we want the staged wrapper to match.
        2. Otherwise fall back to the C project directory name (legacy
           behaviour).

        The chosen name doubles as ``<bin>``, ``<bin>-rust`` (wrapper) and
        ``<bin>-c`` (C reference) inside the run dir, so picking it
        consistently is what makes ``./sds-test`` resolve when the cargo
        target is named ``sds-test`` rather than ``sds``.
        """
        cargo = os.path.join(rust_project_path, "Cargo.toml")
        if os.path.isfile(cargo):
            try:
                text = Path(cargo).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                text = ""
            names = re.findall(
                r'^\s*\[\[bin\]\][^\[]*?^\s*name\s*=\s*"([^"]+)"',
                text,
                flags=re.MULTILINE | re.DOTALL,
            )
            # 去重保序
            seen: Set[str] = set()
            unique = [n for n in names if not (n in seen or seen.add(n))]
            if len(unique) == 1:
                only = unique[0]
                # 与命名约定保持一致：剥离 ``-rust`` 后缀让它跟 C 端测试脚本里
                # ``./<bin>`` 的 token 对齐。
                if only.endswith("-rust"):
                    only = only[: -len("-rust")]
                if only:
                    return only
        return Path(c_project_path).name

    @staticmethod
    def _locate_c_binary(c_project_path: str, bin_name: Optional[str] = None) -> str:
        if not os.path.isdir(c_project_path):
            return ""
        # 优先使用调用方传入的 bin_name（来自 --rust-test-agent-binary-name），
        # 否则回退到 C 项目目录名。两个候选都试一遍，加上 .exe 后缀。
        candidates: List[str] = []
        for raw in (bin_name, Path(c_project_path).name):
            if raw and raw not in candidates:
                candidates.append(raw)
        for raw in list(candidates):
            exe = f"{raw}.exe"
            if exe not in candidates:
                candidates.append(exe)
        for cand in candidates:
            full = os.path.join(c_project_path, cand)
            if os.path.isfile(full):
                return full
        return ""

    @staticmethod
    def _locate_release_binary(project_dir: str, bin_name: str) -> str:
        """Locate a usable release binary.

        Cargo names the executable after the ``[[bin]] name`` entry, which is
        not always ``<bin_name>-rust``. Older projects in this repo used
        ``-rust`` as a discriminator from the C reference; newer ones (or
        crates auto-generated from upstream Cargo manifests) often keep the
        original C tool name. ``bin_name`` here is whatever the call site
        guessed (typically ``<bin>-rust``). We additionally try the variant
        without the ``-rust`` suffix so the agent works even when the cargo
        manifest sticks with the C tool's name.
        """
        release_dir = os.path.join(project_dir, "target", "release")
        if not os.path.isdir(release_dir):
            return ""
        suffix_candidates = [".exe", ""] if os.name == "nt" else ["", ".exe"]
        name_candidates: List[str] = [bin_name]
        if bin_name.endswith("-rust"):
            stripped = bin_name[: -len("-rust")]
            if stripped and stripped not in name_candidates:
                name_candidates.append(stripped)
        # 优先返回原生可执行：先一遍只挑通过 ELF/PE/Mach-O magic 校验的文件，
        # 避免命中目录里残留的 bash 包装脚本或同名占位文件。
        for name in name_candidates:
            for suffix in suffix_candidates:
                full = os.path.join(release_dir, f"{name}{suffix}")
                if os.path.isfile(full) and _looks_like_native_executable(full):
                    return full
        # 兜底：允许命中非原生 wrapper（保持旧行为）。
        for name in name_candidates:
            for suffix in suffix_candidates:
                full = os.path.join(release_dir, f"{name}{suffix}")
                if os.path.isfile(full):
                    return full
        return ""

    # --------------------------------------------------------------- summary

    @staticmethod
    def _print_summary(summary: TestRunSummary, label: str = "测试结果") -> None:
        print(f"\n=== {label} ===")
        print(f"total={summary.total} passed={summary.passed} failed={summary.failed}")
        for case in summary.results:
            mark = "✓" if case.passed else "✗"
            print(f"  {mark} {case.name} (exit={case.exit_code}, {case.duration_seconds}s)")

    # ------------------------------------------------------------ overview

    @staticmethod
    def _build_rust_project_overview(
        project_dir: str, max_files: int = PROJECT_OVERVIEW_MAX_FILES
    ) -> str:
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
                    except OSError:
                        size = 0
                    entries.append(f"- {rel} ({size} bytes)")
                    if len(entries) >= max_files:
                        break
                if len(entries) >= max_files:
                    break
        return "\n".join(entries[:max_files])

    @staticmethod
    def _load_project_structure(rust_project_path: str) -> str:
        plan_path = os.path.join(rust_project_path, ".cgr_generation_plan.json")
        if not os.path.exists(plan_path):
            return ""
        try:
            with open(plan_path, "r", encoding="utf-8") as f:
                plan = json.load(f)
        except Exception as exc:  # noqa: BLE001
            print(f"[rtest] 读取 generation_plan 失败：{exc}")
            return ""
        return str(plan.get("project_structure") or "")

    # --------------------------------------------------------- editable path

    @staticmethod
    def _is_editable_rust_path(rel_path: str) -> bool:
        normalized = (rel_path or "").replace("\\", "/").strip().lstrip("/").lower()
        if not normalized:
            return False
        forbidden_prefixes = ("test/", "tests/", ".bin/", "target/")
        if any(normalized.startswith(p) or f"/{p}" in f"/{normalized}" for p in forbidden_prefixes):
            return False
        forbidden_suffixes = (".sh", ".bash", ".mk", "makefile", "makefile.am", "makefile.in")
        if any(normalized.endswith(s) for s in forbidden_suffixes):
            return False
        allowed_suffixes = (".rs", "cargo.toml", "cargo.lock", "build.rs")
        return any(normalized.endswith(s) for s in allowed_suffixes)

    def _filter_disallowed_edits(self, edits: List[Dict]) -> List[Dict]:
        clean: List[Dict] = []
        for edit in edits or []:
            if not isinstance(edit, dict):
                continue
            rel = (edit.get("path") or "").replace("\\", "/")
            if self._is_editable_rust_path(rel):
                clean.append(edit)
                continue
            print(f"    [rtest] 已拒绝对非 Rust 源文件的编辑请求：{rel or '(空 path)'}")
        return clean

    @staticmethod
    def _resolve_under_root(root: Path, rel_path: str) -> Optional[Path]:
        rel = (rel_path or "").replace("\\", "/").strip().lstrip("/")
        if not rel or rel.startswith("../") or "/../" in f"/{rel}/":
            return None
        full = (root.resolve() / rel).resolve()
        try:
            if os.path.commonpath([str(root.resolve()), str(full)]) != str(root.resolve()):
                return None
        except ValueError:
            return None
        return full

    @staticmethod
    def _material_count_for_file(material: MaterialBudget, kind: str, rel_path: str) -> int:
        normalized = (rel_path or "").replace("\\", "/").strip().lower()
        if not normalized:
            return 0
        count = 0
        if kind == "rust":
            for entry in material.rust_file_entries():
                path = str(entry.get("path") or "").replace("\\", "/").lower()
                if path == normalized:
                    count += 1
        elif kind == "c":
            for rec in material.c_records():
                path = str(rec.get("file") or "").replace("\\", "/").lower()
                if path == normalized or Path(path).name == Path(normalized).name:
                    count += 1
        return count

    def _filter_fake_impl_edits(
        self,
        edits: List[Dict],
        expected_outputs: List[str],
    ) -> List[Dict]:
        clean: List[Dict] = []
        for edit in edits:
            if not isinstance(edit, dict):
                continue
            content = edit.get("content") or ""
            reason = violates_no_fake_impl(content, expected_outputs)
            if reason:
                print(
                    f"    [rtest] 已拒绝疑似假实现 edit："
                    f"{edit.get('path')}:{edit.get('start_line')} - {reason}"
                )
                continue
            clean.append(edit)
        return clean

    @staticmethod
    def _read_script_text(path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except OSError:
            return ""

    @staticmethod
    def _run_dir_for_case(failing_case: TestCaseResult) -> Path:
        if getattr(failing_case, "run_dir", ""):
            return Path(failing_case.run_dir)
        script = Path(failing_case.script_path)
        return script.parent / f".run_{script.stem}"

    @staticmethod
    def _list_test_artifacts(failing_case: TestCaseResult, limit: int = 80) -> str:
        run_dir = RustTestAgent._run_dir_for_case(failing_case)
        if not run_dir.is_dir():
            return ""
        rows: List[str] = []
        try:
            entries = sorted(run_dir.rglob("*"))
        except OSError:
            # 某些测试（如 pwd-long）会创建超过 PATH_MAX 的深层目录树，
            # pathlib.rglob 在遍历时会抛 OSError(ENAMETOOLONG)。
            # 此时退化为只列出 run_dir 直接子文件。
            try:
                entries = sorted(run_dir.iterdir())
            except OSError:
                return ""
        for path in entries:
            try:
                if not path.is_file():
                    continue
                rel = path.relative_to(run_dir).as_posix()
            except (OSError, ValueError):
                continue
            if rel.startswith(".") or "/." in rel:
                continue
            if rel.lower().endswith((".sh", ".bash")):
                continue
            if path.name in {"a.out", "a.exe"}:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            rows.append(f"- {rel} ({size} bytes)")
            if len(rows) >= limit:
                break
        return "\n".join(rows)

    @staticmethod
    def _read_test_artifact(
        failing_case: TestCaseResult,
        rel_path: str,
        max_chars: int = 64000,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> str:
        run_dir = RustTestAgent._run_dir_for_case(failing_case).resolve()
        rel = (rel_path or "").replace("\\", "/").strip().lstrip("/")
        if not rel or rel.startswith("../") or "/../" in rel:
            return ""
        full = (run_dir / rel).resolve()
        try:
            if os.path.commonpath([str(run_dir), str(full)]) != str(run_dir):
                return ""
        except ValueError:
            return ""
        if not full.is_file():
            return ""
        resolved = read_text_file_slice(full)
        if resolved is None:
            return ""
        text = resolved.content
        if text == "":
            try:
                if full.stat().st_size == 0:
                    return "<empty file>\n"
            except OSError:
                pass
        if isinstance(start_line, int) and isinstance(end_line, int):
            resolved_range = read_text_file_slice(
                full,
                start_line=start_line,
                end_line=end_line,
            )
            if resolved_range is None:
                return ""
            return resolved_range.content
        return text[-max_chars:]

    def _seed_test_artifacts(
        self,
        failing_case: TestCaseResult,
        material: MaterialBudget,
        *,
        keywords: List[str],
        limit: int = 8,
    ) -> None:
        """Seed concrete run artifacts before the first LLM repair round.

        Generated-code projects often fail in an intermediate file (`*.x.c`,
        compiler stderr, wrapper stdout). Waiting for the LLM to request those
        files wastes rounds and encourages speculative generator rewrites.
        """
        if not self.allow_c_materials:
            return

        run_dir = self._run_dir_for_case(failing_case)
        if not run_dir.is_dir():
            return

        script_stem = Path(failing_case.name).stem.lower()
        tokens: Set[str] = {
            t.lower()
            for t in re.split(r"[^A-Za-z0-9]+", script_stem)
            if len(t) >= 2
        }
        tokens.update(k.lower() for k in keywords if len(k) >= 2)
        if "-" in script_stem:
            tokens.add(script_stem.rsplit("-", 1)[-1])

        candidates: List[Tuple[int, str, int]] = []
        try:
            paths = sorted(run_dir.rglob("*"))
        except OSError:
            try:
                paths = sorted(run_dir.iterdir())
            except OSError:
                return

        for path in paths:
            try:
                if not path.is_file():
                    continue
                rel = path.relative_to(run_dir).as_posix()
            except (OSError, ValueError):
                continue
            if rel.startswith(".") or "/." in rel:
                continue
            if rel.lower().endswith((".sh", ".bash")):
                continue
            if path.name in {"a.out", "a.exe"}:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            if size > 32000:
                continue

            rel_l = rel.lower()
            name_l = path.name.lower()
            score = 0
            if rel_l.startswith("ttest_artifacts/") or "/artifacts/" in rel_l:
                score += 80
            if any(t and t in rel_l for t in tokens):
                score += 70
            if name_l.endswith(".x.c"):
                score += 60
            if name_l in {"shc.stderr", "shc.stdout", "a.out.stdout", "a.out.stderr"}:
                score += 55
            if name_l.endswith((".stderr", ".stdout", ".log", ".out", ".err")):
                score += 35
            if "default" in rel_l:
                score += 10
            if score <= 0:
                continue
            candidates.append((score, rel, size))

        for _, rel, _size in sorted(candidates, key=lambda item: (-item[0], item[1]))[:limit]:
            if material.has_test_artifact(rel):
                continue
            content = self._read_test_artifact(failing_case, rel, max_chars=20000)
            if material.add_test_artifact(rel, content):
                print(f"  [rtest] 首轮注入测试产物：{rel}")

    @staticmethod
    def _read_runtime_evidence(failing_case: TestCaseResult) -> Dict[str, object]:
        return RuntimeProbeService.read_runtime_evidence(failing_case)

    @staticmethod
    def _stdout_stderr_only_case(failing_case: TestCaseResult) -> TestCaseResult:
        return TestCaseResult(
            name="stdout-stderr-only",
            script_path="",
            passed=failing_case.passed,
            exit_code=failing_case.exit_code,
            stdout=failing_case.stdout,
            stderr=failing_case.stderr,
            duration_seconds=failing_case.duration_seconds,
            trace="",
            run_dir=failing_case.run_dir,
        )

    def _execute_debug_probe(
        self,
        *,
        rust_project_path: str,
        bin_name: str,
        failing_case: TestCaseResult,
        probe_spec: Dict[str, object],
        attempt: int,
    ) -> bool:
        return self.runtime_probe_service.execute_debug_probe(
            rust_project_path=rust_project_path,
            bin_name=bin_name,
            failing_case=failing_case,
            probe_spec=probe_spec,
            attempt=attempt,
        )

    @staticmethod
    def _trace_subcase_context(
        failing_case: TestCaseResult,
        *,
        script_content: str,
        bin_name: str,
    ) -> str:
        trace = failing_case.trace or ""
        if not trace.strip():
            return ""

        trace_lines = trace.splitlines()
        tested_commands: List[Tuple[int, str, str]] = []
        compare_markers: List[Tuple[int, str]] = []
        failure_markers: List[Tuple[int, str]] = []

        for idx, raw in enumerate(trace_lines):
            cmd = _strip_bash_xtrace_command(raw)
            if not cmd:
                if "trace 捕获超时" in raw or "timed out" in raw.lower():
                    failure_markers.append((idx, raw.strip()))
                continue

            normalized = _normalize_tested_command(cmd, bin_name)
            if normalized:
                if not tested_commands or tested_commands[-1][1] != normalized:
                    tested_commands.append((idx, normalized, cmd))
                continue

            if _is_compare_or_diff_command(cmd):
                compare_markers.append((idx, cmd))
            if _is_failure_marker_command(cmd):
                failure_markers.append((idx, cmd))

        if not tested_commands:
            return ""

        marker_idx = failure_markers[-1][0] if failure_markers else len(trace_lines)
        compare_before_failure = [
            item for item in compare_markers if item[0] <= marker_idx
        ]
        anchor_idx = (
            compare_before_failure[-1][0]
            if compare_before_failure
            else marker_idx
        )
        current_candidates = [
            item for item in tested_commands if item[0] < anchor_idx
        ]
        current = current_candidates[-1] if current_candidates else tested_commands[-1]
        current_idx, current_cmd, raw_current_cmd = current
        previous = _unique_preserving_order(
            cmd for idx, cmd, _raw in tested_commands if idx < current_idx
        )
        previous = [cmd for cmd in previous if cmd != current_cmd][-8:]
        script_matches = [] if not script_content else _find_matching_script_lines(
            script_content,
            current_cmd,
            bin_name,
            limit=4,
        )

        lines = [
            "Current unresolved subcase inferred from latest trace:",
            f"- likely tested command: {current_cmd}",
        ]
        if raw_current_cmd != current_cmd:
            lines.append(f"- raw trace command: {raw_current_cmd}")
        if compare_before_failure:
            lines.append(
                f"- nearest compare/diff before failure: {compare_before_failure[-1][1]}"
            )
        if failure_markers:
            lines.append(f"- failure marker: {failure_markers[-1][1]}")
        if script_matches:
            lines.append("- matching script line(s):")
            lines.extend(f"  * {line}" for line in script_matches)
        if previous:
            lines.append(
                "Resolved/earlier tested commands in the same latest trace "
                "(lower priority; do not repair these again unless they still appear in the current failure):"
            )
            lines.extend(f"  - {cmd}" for cmd in previous)
        else:
            lines.append("- no earlier tested command was identified before this subcase")
        return "\n".join(lines)

    @staticmethod
    def _focused_failure_block(failing_case: TestCaseResult) -> str:
        text = failing_case.stdout or ""
        lines = text.splitlines()
        fail_index = -1
        for idx, line in enumerate(lines):
            if re.search(r"Test #\d+ failed", line):
                fail_index = idx
        if fail_index < 0:
            parts: List[str] = []
            if failing_case.stderr.strip():
                parts.append("[stderr]\n" + failing_case.stderr.strip()[-1800:])
            if failing_case.stdout.strip():
                parts.append("[stdout]\n" + failing_case.stdout.strip()[-3200:])
            if not parts:
                return failing_case.short_failure_excerpt(3000)
            return "\n\n".join(parts)[-5000:]
        start = fail_index
        while start > 0 and not re.match(r"Running test #\d+:", lines[start - 1]):
            start -= 1
        end = min(len(lines), fail_index + 80)
        while end < len(lines) and not re.match(r"Running test #\d+:", lines[end]):
            end += 1
        block = "\n".join(lines[start:end]).strip()
        if len(block) > 5000:
            block = block[:1500] + "\n...\n" + block[-3000:]
        if _looks_like_argv0_path_diff(block):
            block += (
                "\n\n[System diagnosis] The current diff appears to mainly come from argv[0] / absolute path differences between C_BIN and RUST_BIN. "
                "The test scripts are human-preprocessed read-only inputs. Do not edit them or hardcode paths in Rust code; "
                "report this evidence for human review unless a real Rust behavioral defect can be identified."
            )
        return block

    # ----------------------------------------------------------- repair

    def _repair_failing_case(
        self,
        *,
        rust_project_path: str,
        bin_name: str,
        runner: TestRunner,
        project_structure: str,
        source_index: CSourceIndex,
        failing_case: TestCaseResult,
        baseline_pass_names: Set[str],
    ) -> bool:
        print(f"\n[rtest] --- 修复失败用例：{failing_case.name} ---")
        rust_overview = self._build_rust_project_overview(rust_project_path)
        source_index_display = build_source_index_display(source_index, C_SOURCE_INDEX_MAX_ITEMS)
        script_content = self._read_script_text(failing_case.script_path)

        # 原始首跑跳过 trace（#11），这里按需懒加载
        if not failing_case.trace and os.path.exists(failing_case.script_path):
            failing_case.trace = runner.capture_trace_for(Path(failing_case.script_path))

        flags = extract_test_flags(failing_case.name, script_content)
        keywords = extract_test_keywords(failing_case.name, script_content)
        expected_outputs = extract_expected_outputs(script_content)
        if not self.allow_c_materials:
            flags = []
            keywords = []
            expected_outputs = []
        if flags:
            print(f"  [rtest] 推断被测 flag：{', '.join(flags)}")
        if keywords:
            print(f"  [rtest] 推断被测关键字：{', '.join(keywords)}")

        material = MaterialBudget(budget_chars=self.prompt_budget_chars)

        # 首轮主动注入
        for rec in seed_c_sources(flags, source_index, keywords=keywords, limit=SEED_C_LIMIT):
            if not self.allow_c_materials:
                continue
            if material.add_c_record(rec):
                print(f"  [rtest] 首轮注入 C 源码：{rec.get('name')} [{rec.get('file')}]")
        for rel, text in seed_rust_files(
            flags, rust_project_path, keywords=keywords, limit=SEED_RUST_LIMIT
        ).items():
            if material.add_rust_file(rel, text):
                print(f"  [rtest] 首轮注入 Rust 文件：{rel}")
        self._seed_test_artifacts(
            failing_case,
            material,
            keywords=keywords,
        )

        # 快照项目，便于回归回滚
        snapshot = ProjectSnapshot(rust_project_path)
        try:
            snapshot.create()
        except SnapshotError as exc:
            print(f"  [rtest] 创建快照失败，跳过回归保护：{exc}")

        state = _RepairLoopState(
            history_summary="",
            last_build_error="",
            regression_warning="",
            last_failure_signature=failing_case.failure_signature(),
            stall_count=0,
            debug_probe_count=0,
            edit_region_counts={},
            static_probes={},
            static_program_args=[],
        )

        repaired = False
        try:
            for attempt in range(1, self.max_repair_iterations + 1):
                print(f"  [rtest] 修复迭代 {attempt}/{self.max_repair_iterations}")
                script_content = self._read_script_text(failing_case.script_path)
                outcome = self._repair_one_round(
                    rust_project_path=rust_project_path,
                    bin_name=bin_name,
                    runner=runner,
                    project_structure=project_structure,
                    source_index=source_index,
                    source_index_display=source_index_display,
                    rust_overview=rust_overview,
                    failing_case=failing_case,
                    script_content=script_content,
                    flags=flags,
                    keywords=keywords,
                    expected_outputs=expected_outputs,
                    baseline_pass_names=baseline_pass_names,
                    material=material,
                    state=state,
                    attempt=attempt,
                    snapshot=snapshot,
                )
                if outcome == "passed":
                    repaired = True
                    return True
                if outcome == "abort":
                    return False
                # "continue" -> 下一轮
            print(f"  [rtest] 已达最大修复轮数，仍未修复 {failing_case.name}")
            return False
        finally:
            if not repaired:
                try:
                    snapshot.restore()
                    print(f"  [rtest] 未修复 {failing_case.name}，已回滚本用例的 edits")
                    self._rebuild_and_restage_after_restore(
                        rust_project_path=rust_project_path,
                        bin_name=bin_name,
                        runner=runner,
                    )
                except SnapshotError as exc:
                    print(f"  [rtest] 回滚失败，项目可能包含未完成修复：{exc}")
            snapshot.discard()

    def _rebuild_and_restage_after_restore(
        self,
        *,
        rust_project_path: str,
        bin_name: str,
        runner: TestRunner,
    ) -> bool:
        """After restoring sources, rebuild target/release and refresh wrappers."""
        self._touch_rebuild_inputs(rust_project_path)
        if not self._cargo_build_release(rust_project_path):
            print("  [rtest] 回滚后重新编译失败，后续测试可能仍使用旧二进制")
            return False
        restored_binary = self._locate_release_binary(
            rust_project_path, f"{bin_name}-rust"
        )
        if not restored_binary:
            print("  [rtest] 回滚后找不到 release 二进制，后续测试可能仍使用旧 wrapper")
            return False
        runner.restage_rust_binary(restored_binary)
        print("  [rtest] 回滚后已重新编译并刷新测试 wrapper")
        return True

    @staticmethod
    def _touch_rebuild_inputs(rust_project_path: str) -> None:
        """Bump mtimes after snapshot restore so Cargo cannot reuse stale binaries."""
        root = Path(rust_project_path)
        candidates = [root / "Cargo.toml", root / "Cargo.lock", root / "build.rs"]
        src_dir = root / "src"
        if src_dir.is_dir():
            candidates.extend(src_dir.rglob("*.rs"))
        for path in candidates:
            try:
                if path.exists():
                    os.utime(path, None)
            except OSError:
                pass

    def _repair_one_round(
        self,
        *,
        rust_project_path: str,
        bin_name: str,
        runner: TestRunner,
        project_structure: str,
        source_index: CSourceIndex,
        source_index_display: str,
        rust_overview: str,
        failing_case: TestCaseResult,
        script_content: str,
        flags: List[str],
        keywords: List[str],
        expected_outputs: List[str],
        baseline_pass_names: Set[str],
        material: MaterialBudget,
        state: "_RepairLoopState",
        attempt: int,
        snapshot: ProjectSnapshot,
    ) -> str:
        """返回 ``passed`` / ``abort`` / ``continue``。"""
        runtime_evidence = (
            self._read_runtime_evidence(failing_case) if self.enable_log_agent else {}
        )
        prompt_case = failing_case
        prompt_script_content = script_content
        prompt_flags = flags
        prompt_keywords = keywords
        prompt_expected_outputs = expected_outputs
        prompt_subcase_context = self._trace_subcase_context(
            failing_case,
            script_content=script_content,
            bin_name=bin_name,
        )
        prompt_test_artifact_index = self._list_test_artifacts(failing_case)
        if not self.allow_c_materials:
            prompt_case = self._stdout_stderr_only_case(failing_case)
            prompt_script_content = ""
            prompt_flags = []
            prompt_keywords = []
            prompt_expected_outputs = []
            prompt_subcase_context = ""
            prompt_test_artifact_index = ""
        prompt = build_repair_prompt(
            failing_case=prompt_case,
            script_content=prompt_script_content,
            project_structure=project_structure,
            rust_overview=rust_overview,
            material=material,
            history_summary=state.history_summary,
            source_records_index=source_index_display,
            attempt=attempt,
            max_attempts=self.max_repair_iterations,
            last_build_error=state.last_build_error,
            flags=prompt_flags,
            keywords=prompt_keywords,
            expected_outputs=prompt_expected_outputs,
            regression_warning=state.regression_warning,
            focused_failure=self._focused_failure_block(prompt_case),
            subcase_context=prompt_subcase_context,
            test_artifact_index=prompt_test_artifact_index,
            material_request_feedback=state.material_request_feedback,
            runtime_evidence=runtime_evidence,
            log_agent_enabled=self.enable_log_agent,
            active_static_probes=list(state.static_probes.values()),
        )
        state.regression_warning = ""  # 只提示一次
        state.material_request_feedback = ""  # 只提示一次

        self.llm.set_request_label(f"测试修复 {failing_case.name} #{attempt}")
        reply = self.llm.generate(
            [
                {
                    "role": "system",
                    "content": (
                        "You are an experienced Rust repair assistant skilled at using sh test script failure information "
                        "to locate and fix functional defects in C-to-Rust translation outputs."
                    ),
                },
                {"role": "user", "content": prompt},
            ]
        )
        text = reply[0] if isinstance(reply, list) else reply
        if self.verbose:
            print("    [rtest] LLM raw response（verbose）:")
            print("    " + (text or "")[:4000].replace("\n", "\n    "))

        payload = self.adapter.extract_json_payload(text)
        if not isinstance(payload, dict):
            state.response_contract_failures += 1
            usage = getattr(getattr(self.llm, "llm", None), "last_usage", None)
            violation = RepairResponseContract.parse_failure(
                text=text or "",
                usage=usage,
                attempt=attempt,
                consecutive_count=state.response_contract_failures,
            )
            state.history_summary += "\n" + violation.history_feedback
            print(
                f"    [rtest] {violation.log_message}，"
                "继续下一轮协议修正"
            )
            return "continue"

        violation = RepairResponseContract.validate_payload(payload)
        if violation:
            state.history_summary += "\n" + violation.history_feedback
            print(
                f"    [rtest] {violation.log_message}，"
                "本轮继续执行有效请求"
            )

        # JSON 解析成功，重置连续失败计数
        state.response_contract_failures = 0

        state.history_summary = (
            payload.get("updated_summary") or payload.get("summary") or state.history_summary
        ).strip()

        cgr_requests = payload.get("cgr_read") or payload.get("c_source_requests") or []
        rust_read_requests = payload.get("rust_read_requests") or []
        test_artifact_requests = (
            payload.get("test_artifact_read")
            or payload.get("test_artifact_requests")
            or []
        )
        material_keep = payload.get("material_keep")
        if isinstance(material_keep, dict):
            # `material_keep` is only a priority hint. Hard-pruning here made later
            # rounds lose the edited file tail or the compile-error context and then
            # repair against stale fragments. Budget pressure is still handled by
            # MaterialBudget's LRU eviction when new material is added.
            pass
        requested_material = bool(cgr_requests or rust_read_requests or test_artifact_requests)
        submitted_edits = [
            edit for edit in (payload.get("edits") or []) if isinstance(edit, dict)
        ]
        raw_edits = self._filter_disallowed_edits(submitted_edits)
        if len(raw_edits) < len(submitted_edits):
            state.history_summary += (
                "\n[System] One or more edits outside the permitted Rust/Cargo source set were rejected. "
                "Test scripts and fixtures are human-preprocessed read-only inputs; "
                "do not propose changes to them in later rounds."
            )
        edits = self._filter_fake_impl_edits(raw_edits, expected_outputs)
        if raw_edits and not edits:
            state.history_summary += (
                "\n[System] All edits submitted in the previous round were rejected by the anti-cheat check; "
                "implement the real flag logic from the C source instead of placeholders or repeating expected output."
            )
        material_result = self._absorb_material_requests(
            rust_project_path, source_index, cgr_requests, rust_read_requests, material
        )
        artifact_result = self._absorb_test_artifact_requests(
            failing_case, test_artifact_requests, material
        )
        material_result.merge(artifact_result)
        if requested_material:
            state.material_request_feedback = _format_material_request_feedback(material_result)
            self._print_material_request_feedback(material_result)
        if test_artifact_requests and edits and artifact_result.new_material:
            print("    [rtest] 本轮同时新增 test artifact 和 edits，优先提供材料并跳过 edits")
            state.history_summary += (
                "\n[System] The previous round requested new test artifacts and also submitted edits. "
                "The newly provided artifacts must be reviewed before implementation edits, so those edits were skipped. "
                "If the requested artifact was already available in the prompt, edits are allowed and will not be skipped."
            )
            edits = []
        new_material = material_result.new_material
        if new_material:
            # 新证据进入后，允许模型在同一区域基于新材料重新尝试。
            state.edit_region_counts.clear()

        if edits:
            for key in _edit_region_keys(edits):
                state.edit_region_counts[key] = state.edit_region_counts.get(key, 0) + 1

        debug_probe = payload.get("debug_probe") or payload.get("instrumentation")
        static_probe_update = payload.get("static_probe_update")
        if not self.enable_log_agent and (
            isinstance(debug_probe, dict) or isinstance(static_probe_update, dict)
        ):
            state.history_summary += (
                "\n[System] Unsupported response fields were ignored; "
                "use source reads or concrete edits."
            )
            debug_probe = None
            static_probe_update = None
        if edits and isinstance(static_probe_update, dict):
            print("    [rtest] 本轮包含 edits，忽略同轮 static_probe_update")
            state.history_summary += (
                "\n[System] Static instrumentation updates are evidence-gathering actions and "
                "cannot be combined with implementation edits in one round."
            )
            static_probe_update = None
        if edits and isinstance(debug_probe, dict):
            print("    [rtest] 本轮包含 edits，忽略同轮 debug_probe")
            state.history_summary += (
                "\n[System] The previous round included both edits and debug_probe. "
                "The probe was ignored because debug_probe is only for evidence-gathering rounds with empty edits."
            )
            debug_probe = None
        if new_material and isinstance(static_probe_update, dict):
            state.history_summary += (
                "\n[System] Requested materials have been provided; static instrumentation "
                "from the same round was skipped until the new source context is reviewed."
            )
            return "continue"
        if new_material and isinstance(debug_probe, dict):
            state.history_summary += (
                "\n[System] Source or test materials requested in the previous round have now been provided. "
                "The debug_probe from that same round was skipped so the next round can use the new materials first."
            )
            return "continue"

        if isinstance(static_probe_update, dict):
            try:
                update = LogAgent.parse_static_probe_update(static_probe_update)
            except Exception as exc:  # noqa: BLE001
                state.history_summary += f"\n[System] Invalid static_probe_update: {exc}."
                return "continue"
            if update.clear:
                state.static_probes.clear()
            for probe_id in update.remove:
                state.static_probes.pop(probe_id, None)
            for probe in update.add:
                state.static_probes[probe.probe_id] = probe
            if update.program_args:
                state.static_program_args = list(update.program_args)
            if not state.static_probes:
                state.history_summary += (
                    "\n[System] Static instrumentation set is now empty; no static run was performed."
                )
                return "continue"
            self.runtime_probe_service.execute_static_probes(
                rust_project_path=rust_project_path,
                bin_name=bin_name,
                failing_case=failing_case,
                probes=state.static_probes.values(),
                program_args=state.static_program_args,
                attempt=attempt,
            )
            state.history_summary += (
                "\n[System] Static probes were applied to temporary Rust/C project copies and executed. "
                "Inspect the Static probe evidence in the next round; use static_probe_update to add, replace, "
                "remove, or clear probe points as needed."
            )
            return "continue"

        if isinstance(debug_probe, dict):
            if not _has_meaningful_debug_probe(debug_probe):
                print("    [rtest] 跳过无效 debug_probe：缺少断点")
                state.history_summary += (
                    "\n[System] The previous debug_probe was skipped because it had no breakpoints. "
                    "A valid debug_probe must include at least one concrete Rust source breakpoint and must not be combined with edits."
                )
            else:
                if state.debug_probe_count >= self.max_debug_probes:
                    print("    [rtest] 本用例 debug_probe 已达配置上限，跳过新的请求")
                    state.history_summary += (
                        "\n[System] The configured maximum number of dynamic debug probes has been reached. "
                        "Use the existing evidence, static probes, source reads, or submit concrete edits."
                    )
                    return "continue"
                state.debug_probe_count += 1
                if self._execute_debug_probe(
                    rust_project_path=rust_project_path,
                    bin_name=bin_name,
                    failing_case=failing_case,
                    probe_spec=debug_probe,
                    attempt=attempt,
                ):
                    state.history_summary += (
                        "\n[System] A runtime debug probe was executed. The next round must use the new probe evidence "
                        "before changing the implementation again. A later distinct probe is allowed only if the current evidence "
                        "cannot distinguish the remaining hypotheses."
                    )
                return "continue"

        if edits:
            applied, _audit = self.adapter.apply_structured_edits(rust_project_path, edits)
            print(f"    [rtest] 应用编辑：applied={applied}, edits={len(edits)}")
            if applied:
                # 刷新已 stage 的 Rust 文件内容，让下一轮看到最新版本
                edited_paths = {
                    (e.get("path") or "").replace("\\", "/")
                    for e in edits
                    if isinstance(e, dict) and e.get("path")
                }
                for rel in edited_paths:
                    if not rel:
                        continue
                    refreshed = self.adapter.read_file_slice(rust_project_path, rel)
                    if refreshed:
                        material.add_rust_file(rel, refreshed)
                self._refresh_post_edit_materials(
                    rust_project_path=rust_project_path,
                    edits=edits,
                    material=material,
                )

                return self._build_and_verify(
                    rust_project_path=rust_project_path,
                    bin_name=bin_name,
                    runner=runner,
                    failing_case=failing_case,
                    baseline_pass_names=baseline_pass_names,
                    state=state,
                    snapshot=snapshot,
                    material=material,
                )

        if payload.get("complete"):
            print("    [rtest] LLM 标记 complete=true，但用例仍未通过，继续修复并要求新证据或编辑")
            state.history_summary += (
                "\n[System] The previous round set complete=true, but the current test case still fails. "
                "That signal is not accepted as a stop condition for a failing case. "
                "If you believe the failure is caused by the runner/environment, request fresh trace or test artifacts "
                "that prove it for the current run; otherwise provide a focused Rust edit. "
                "Do not repeat stale diagnoses from earlier runs."
            )
            return "continue"

        if not new_material and not edits:
            if requested_material:
                print("    [rtest] LLM 请求了材料，但没有新增可读材料，继续下一轮并要求调整请求")
                state.history_summary += "\n" + _format_material_request_feedback(material_result)
            else:
                print("    [rtest] LLM 既没请求材料也没产生新编辑，继续下一轮并要求改变策略")
                state.history_summary += (
                    "\n[System] The previous round did not produce any executable action. The next round must request missing materials "
                    "or provide new valid edits."
                )
            return "continue"
        return "continue"

    @staticmethod
    def _print_material_request_feedback(result: "_MaterialRequestResult") -> None:
        if result.added:
            print(
                "    [rtest] 本轮新增材料："
                + ", ".join(_unique_preserving_order(result.added)[:8])
                + (" ..." if len(result.added) > 8 else "")
            )
        if result.already_available:
            print(
                "    [rtest] 本轮请求的材料已在当前 prompt 中："
                + ", ".join(_unique_preserving_order(result.already_available)[:8])
                + (" ..." if len(result.already_available) > 8 else "")
            )
        if result.unavailable:
            print(
                "    [rtest] 本轮请求的材料不可读或未匹配："
                + ", ".join(_unique_preserving_order(result.unavailable)[:8])
                + (" ..." if len(result.unavailable) > 8 else "")
            )

    def _absorb_material_requests(
        self,
        rust_project_path: str,
        source_index: CSourceIndex,
        cgr_requests: List[Dict],
        rust_read_requests: List[Dict],
        material: MaterialBudget,
    ) -> "_MaterialRequestResult":
        result = _MaterialRequestResult()
        for req in cgr_requests:
            if not isinstance(req, dict):
                continue
            request = dict(req)
            kind = str(request.get("kind") or "function").strip().lower()
            mode = str(request.get("mode") or "").strip().lower()
            query = str(
                request.get("query") or request.get("path") or request.get("file") or ""
            ).strip()
            request_label = _format_c_request_label(request)
            if not self.allow_c_materials:
                result.unavailable.append(f"c:{request_label} (C source material disabled by ablation)")
                continue
            start_line = request.get("start_line")
            end_line = request.get("end_line")
            if kind == "file" and mode in {"line_range", "range"}:
                try:
                    start_int = int(start_line)
                    end_int = int(end_line)
                except (TypeError, ValueError):
                    start_int = None
                    end_int = None
                full = source_index.resolve_source_file_path(query)
                canonical = source_index._canonical_file_path(query) if query else query
                if (
                    full
                    and should_upgrade_line_range_to_whole_file(
                        full,
                        existing_material_count=self._material_count_for_file(material, "c", canonical),
                        start_line=start_int,
                        end_line=end_int,
                    )
                ):
                    request["mode"] = "whole_file"
                    request.pop("start_line", None)
                    request.pop("end_line", None)
                    print(
                        "    [rtest] C 小文件/多片段请求升级为 whole_file："
                        f"{canonical} ({full.stat().st_size} bytes <= {SMALL_FILE_WHOLE_FILE_CHARS})"
                    )
            rec = source_index.fulfill_request(request)
            if not rec:
                result.unavailable.append(f"c:{request_label}")
                continue
            material_id = MaterialBudget._c_key(rec)
            if material.has_c_record(rec):
                result.already_available.append(f"c:{material_id}")
                continue
            if material.add_c_record(rec):
                kind_tag = "file" if rec.get("is_file_aggregate") else "function"
                print(
                    f"    [rtest] 提供 C {kind_tag}：{rec.get('name')} [{rec.get('file')}]"
                )
                result.added.append(f"c:{material_id}")
                requested_start = rec.get("requested_start_line")
                requested_end = rec.get("requested_end_line")
                actual_start = rec.get("start_line")
                actual_end = rec.get("end_line")
                if (
                    rec.get("is_line_range")
                    and (requested_start, requested_end) != (actual_start, actual_end)
                ):
                    print(
                        "    [rtest] C 行范围按实际文件范围提供："
                        f"requested={requested_start}-{requested_end}, actual={actual_start}-{actual_end}"
                    )
                result.new_material = True

        for req in rust_read_requests:
            if isinstance(req, dict):
                rel = str(req.get("path") or "").replace("\\", "/")
                mode = str(req.get("mode") or "whole_file").strip().lower()
                try:
                    start_line = int(req.get("start_line"))
                except Exception:
                    start_line = None
                try:
                    end_line = int(req.get("end_line"))
                except Exception:
                    end_line = None
            else:
                rel = str(req or "").replace("\\", "/")
                mode = "whole_file"
                start_line = None
                end_line = None
            if not rel:
                continue
            if not self._is_editable_rust_path(rel):
                print(f"    [rtest] 拒绝读取非 Rust 源文件：{rel}")
                result.unavailable.append(f"rust:{rel}")
                continue
            full_path = self._resolve_under_root(Path(rust_project_path), rel)
            if not full_path or not full_path.is_file():
                result.unavailable.append(f"rust:{rel}")
                continue
            if mode == "line_range" and isinstance(start_line, int) and isinstance(end_line, int):
                if should_upgrade_line_range_to_whole_file(
                    full_path,
                    existing_material_count=self._material_count_for_file(material, "rust", rel),
                    start_line=start_line,
                    end_line=end_line,
                ):
                    mode = "whole_file"
                    start_line = None
                    end_line = None
                    print(
                        "    [rtest] Rust 小文件/多片段请求升级为 whole_file："
                        f"{rel} ({full_path.stat().st_size} bytes <= {SMALL_FILE_WHOLE_FILE_CHARS})"
                    )
            if mode == "line_range" and isinstance(start_line, int) and isinstance(end_line, int):
                if end_line < start_line:
                    start_line, end_line = end_line, start_line
                file_slice = read_text_file_slice(
                    full_path,
                    start_line=start_line,
                    end_line=end_line,
                )
                if not file_slice or not file_slice.content:
                    result.unavailable.append(f"rust:{rel}:{start_line}-{end_line}")
                    continue
                if file_slice.range_changed:
                    print(
                        "    [rtest] Rust 行范围按实际文件范围提供："
                        f"{rel} requested={start_line}-{end_line}, "
                        f"actual={file_slice.start_line}-{file_slice.end_line}, "
                        f"total_lines={file_slice.total_lines}"
                    )
                uncovered = material.uncovered_rust_ranges(
                    rel,
                    file_slice.start_line,
                    file_slice.end_line,
                )
                if not uncovered:
                    print(
                        f"    [rtest] 跳过重复 Rust 行范围：{rel} "
                        f"[{file_slice.start_line}-{file_slice.end_line}] "
                        "已由现有片段覆盖"
                    )
                    result.already_available.append(
                        f"rust:{rel}:{file_slice.start_line}-{file_slice.end_line}"
                    )
                    continue
                for uncovered_start, uncovered_end in uncovered:
                    uncovered_slice = read_text_file_slice(
                        full_path,
                        start_line=uncovered_start,
                        end_line=uncovered_end,
                    )
                    content = uncovered_slice.content if uncovered_slice else ""
                    if content and material.add_rust_file(
                        rel,
                        content,
                        start_line=uncovered_start,
                        end_line=uncovered_end,
                        mode="line_range",
                    ):
                        result.new_material = True
                        result.added.append(f"rust:{rel}:{uncovered_start}-{uncovered_end}")
                        print(
                            f"    [rtest] 提供 Rust 文件片段：{rel} "
                            f"[{uncovered_start}-{uncovered_end}] ({len(content)} chars)"
                        )
                continue
            else:
                if material.has_rust_file(rel):
                    result.already_available.append(f"rust:{rel}")
                    continue
                file_slice = read_text_file_slice(full_path)
                content = file_slice.content if file_slice else ""
                start_line = None
                end_line = None
                mode = "whole_file"
            if content and material.add_rust_file(
                rel,
                content,
                start_line=start_line,
                end_line=end_line,
                mode=mode,
            ):
                result.new_material = True
                if mode == "line_range" and isinstance(start_line, int) and isinstance(end_line, int):
                    result.added.append(f"rust:{rel}:{start_line}-{end_line}")
                    print(
                        f"    [rtest] 提供 Rust 文件片段：{rel} [{start_line}-{end_line}] "
                        f"({len(content)} chars)"
                    )
                else:
                    result.added.append(f"rust:{rel}")
                    print(f"    [rtest] 提供 Rust 文件：{rel} ({len(content)} chars)")
            elif not content:
                result.unavailable.append(f"rust:{rel}")
        return result

    def _absorb_test_artifact_requests(
        self,
        failing_case: TestCaseResult,
        requests: List[Dict],
        material: MaterialBudget,
    ) -> "_MaterialRequestResult":
        result = _MaterialRequestResult()
        for req in requests or []:
            if isinstance(req, dict):
                rel = str(req.get("path") or "").replace("\\", "/")
                mode = str(req.get("mode") or "whole_file").strip().lower()
                try:
                    start_line = int(req.get("start_line"))
                except Exception:
                    start_line = None
                try:
                    end_line = int(req.get("end_line"))
                except Exception:
                    end_line = None
            else:
                rel = str(req or "").replace("\\", "/")
                mode = "whole_file"
                start_line = None
                end_line = None
            rel = rel.strip().lstrip("/")
            if not rel:
                continue
            if not self.allow_c_materials:
                result.unavailable.append(f"test:{rel} (test material disabled by ablation; use stdout/stderr only)")
                continue
            if (
                mode == "line_range"
                and isinstance(start_line, int)
                and isinstance(end_line, int)
                and end_line >= start_line
                and not material.has_test_artifact(rel)
            ):
                content = self._read_test_artifact(
                    failing_case,
                    rel,
                    start_line=start_line,
                    end_line=end_line,
                )
            else:
                # Test artifacts are runtime products and may change after every
                # edit/build/rerun. Always re-read explicit artifact requests;
                # path-only dedupe can otherwise keep stale generated sources in
                # the prompt and mislead the next repair round.
                content = self._read_test_artifact(failing_case, rel, max_chars=64000)
                start_line = None
                end_line = None
                mode = "whole_file"
            if content and material.add_test_artifact(
                rel,
                content,
                start_line=start_line,
                end_line=end_line,
                mode=mode,
            ):
                if mode == "line_range" and isinstance(start_line, int) and isinstance(end_line, int):
                    result.added.append(f"test:{rel}:{start_line}-{end_line}")
                    print(
                        f"    [rtest] 提供测试产物片段：{rel} [{start_line}-{end_line}] "
                        f"({len(content)} chars)"
                    )
                else:
                    result.added.append(f"test:{rel}")
                    print(f"    [rtest] 提供测试产物：{rel} ({len(content)} chars)")
                result.new_material = True
            elif not content:
                print(f"    [rtest] 测试产物不存在或不可读：{rel}")
                result.unavailable.append(f"test:{rel}")
            else:
                if mode == "line_range" and isinstance(start_line, int) and isinstance(end_line, int):
                    result.already_available.append(f"test:{rel}:{start_line}-{end_line}")
                else:
                    result.already_available.append(f"test:{rel}")
        return result

    def _refresh_post_edit_materials(
        self,
        *,
        rust_project_path: str,
        edits: List[Dict],
        material: MaterialBudget,
    ) -> None:
        """After edits, add current source windows around every touched range.

        The LLM should never repair the next round from stale pre-edit snippets.
        We still refresh the whole edited file in the caller when possible; these
        focused windows make the exact edit area survive even under budget
        pressure and show the post-edit line numbers.
        """
        ranges: Dict[str, Tuple[int, int]] = {}
        whole_files: Set[str] = set()
        for edit in edits or []:
            if not isinstance(edit, dict):
                continue
            rel = str(edit.get("path") or "").replace("\\", "/").strip()
            if not rel or not self._is_editable_rust_path(rel):
                continue
            mode = str(edit.get("mode") or "").strip().lower()
            if mode in {
                "copy_range_after",
                "cp",
                "copy_c_string_array_after",
                "copy_c_str_array_after",
            }:
                try:
                    start = int(edit.get("target_line") or edit.get("after_line") or 1)
                except (TypeError, ValueError):
                    start = 1
                end = start
            elif mode == "insert_before":
                try:
                    start = int(edit.get("before_line") or edit.get("target_line") or edit.get("start_line") or 1)
                except (TypeError, ValueError):
                    start = 1
                end = start
            elif mode == "insert_after":
                try:
                    start = int(edit.get("after_line") or edit.get("target_line") or edit.get("end_line") or edit.get("start_line") or 1)
                except (TypeError, ValueError):
                    start = 1
                end = start
            else:
                try:
                    start = int(edit.get("start_line") or edit.get("line") or 1)
                except (TypeError, ValueError):
                    start = 1
                try:
                    end = int(edit.get("end_line") or start)
                except (TypeError, ValueError):
                    end = start
            if start <= 0 or end <= 0:
                whole_files.add(rel)
                continue
            if end < start:
                start, end = end, start
            ranges[rel] = (
                min(start, ranges.get(rel, (start, end))[0]),
                max(end, ranges.get(rel, (start, end))[1]),
            )

        for rel in sorted(whole_files):
            content = self.adapter.read_file_slice(rust_project_path, rel)
            if content and material.add_rust_file(rel, content):
                print(f"    [rtest] 编辑后刷新 Rust 文件：{rel} ({len(content)} chars)")

        for rel, (start, end) in sorted(ranges.items()):
            self._add_rust_context_window(
                rust_project_path=rust_project_path,
                rel=rel,
                center_start=start,
                center_end=end,
                material=material,
                reason="编辑后上下文",
            )

    def _refresh_build_error_materials(
        self,
        *,
        rust_project_path: str,
        build_output: str,
        material: MaterialBudget,
    ) -> None:
        """Add current Rust source windows around rustc error locations."""
        locations = self._rustc_error_locations(rust_project_path, build_output)
        if not locations:
            return
        for rel, line in locations[:16]:
            self._add_rust_context_window(
                rust_project_path=rust_project_path,
                rel=rel,
                center_start=line,
                center_end=line,
                material=material,
                reason="编译错误上下文",
            )

    def _add_rust_context_window(
        self,
        *,
        rust_project_path: str,
        rel: str,
        center_start: int,
        center_end: int,
        material: MaterialBudget,
        reason: str,
    ) -> None:
        rel = rel.replace("\\", "/").strip().lstrip("/")
        if not rel or not self._is_editable_rust_path(rel):
            return
        line_count = self._rust_file_line_count(rust_project_path, rel)
        start = max(1, int(center_start) - AUTO_CONTEXT_LINES)
        end = max(start, int(center_end) + AUTO_CONTEXT_LINES)
        if line_count:
            end = min(line_count, end)
        content = self.adapter.read_file_slice(
            rust_project_path,
            rel,
            start_line=start,
            end_line=end,
        )
        if content and material.add_rust_file(
            rel,
            content,
            start_line=start,
            end_line=end,
            mode="line_range",
        ):
            print(f"    [rtest] 提供 {reason}：{rel} [{start}-{end}] ({len(content)} chars)")

    @staticmethod
    def _rust_file_line_count(rust_project_path: str, rel: str) -> int:
        full = Path(rust_project_path) / rel.replace("/", os.sep)
        try:
            return len(full.read_text(encoding="utf-8", errors="ignore").splitlines())
        except OSError:
            return 0

    @staticmethod
    def _rustc_error_locations(
        rust_project_path: str,
        build_output: str,
    ) -> List[Tuple[str, int]]:
        root = Path(rust_project_path).resolve()
        seen: Set[Tuple[str, int]] = set()
        locations: List[Tuple[str, int]] = []
        pattern = re.compile(r"-->\s+(.+?\.rs):(\d+):(\d+)")
        for match in pattern.finditer(build_output or ""):
            raw_path = match.group(1).strip().replace("\\", "/")
            try:
                line = int(match.group(2))
            except ValueError:
                continue
            rel = raw_path
            path_obj = Path(raw_path)
            if path_obj.is_absolute():
                try:
                    rel = path_obj.resolve().relative_to(root).as_posix()
                except (OSError, ValueError):
                    continue
            else:
                rel = rel.lstrip("./")
            if not rel.startswith("src/") and "/" not in rel:
                rel = f"src/{rel}"
            key = (rel, line)
            if key in seen:
                continue
            seen.add(key)
            locations.append(key)
        return locations

    def _build_and_verify(
        self,
        *,
        rust_project_path: str,
        bin_name: str,
        runner: TestRunner,
        failing_case: TestCaseResult,
        baseline_pass_names: Set[str],
        state: "_RepairLoopState",
        snapshot: ProjectSnapshot,
        material: MaterialBudget,
    ) -> str:
        if not self._cargo_build_release(rust_project_path):
            ok, build_output = self.adapter.run_command(
                "cargo build --release",
                rust_project_path,
                timeout_seconds=self.build_timeout_seconds,
            )
            state.last_build_error = build_output if not ok else ""
            self._refresh_build_error_materials(
                rust_project_path=rust_project_path,
                build_output=state.last_build_error,
                material=material,
            )
            print("    [rtest] 修复后编译失败，将编译错误带入下一轮")
            state.history_summary += "\n[System] The previous edit caused a compile failure; prioritize fixing the compile error."
            return "continue"

        state.last_build_error = ""
        new_binary = self._locate_release_binary(rust_project_path, f"{bin_name}-rust")
        if not new_binary:
            print("    [rtest] 编译产物缺失，跳过本轮验证")
            return "continue"
        runner.restage_rust_binary(new_binary)

        print(f"    [rtest] 重跑当前用例：{failing_case.name}")
        new_result = runner.run_single(
            Path(failing_case.script_path), capture_trace=True
        )
        mark = "passed" if new_result.passed else f"failed exit={new_result.exit_code}"
        print(
            f"    [rtest] 当前用例结果：{mark}, "
            f"duration={new_result.duration_seconds}s"
        )
        failing_case.passed = new_result.passed
        failing_case.exit_code = new_result.exit_code
        failing_case.stdout = new_result.stdout
        failing_case.stderr = new_result.stderr
        failing_case.trace = new_result.trace
        failing_case.duration_seconds = new_result.duration_seconds
        failing_case.run_dir = new_result.run_dir

        if new_result.passed:
            regressed = self._check_regression(
                runner, baseline_pass_names, failing_case.name
            )
            if regressed:
                regression_detail = _format_regression_details(regressed)
                print(
                    f"    [rtest] ⚠ 修复 {failing_case.name} 引入回归："
                    f"{', '.join(sorted(regressed))}，回滚本次编辑"
                )
                for line in regression_detail.splitlines():
                    print(f"      {line}")
                try:
                    snapshot.restore()
                except SnapshotError as exc:
                    print(f"    [rtest] 回滚失败，后续结果可能不可靠：{exc}")
                    state.history_summary += (
                        f"\n[System] Rollback after a regression failed: {exc}. "
                        "Do not repair the regressed case as a new target; the current case repair state is unsafe."
                    )
                    return "abort"
                if not self._rebuild_and_restage_after_restore(
                    rust_project_path=rust_project_path,
                    bin_name=bin_name,
                    runner=runner,
                ):
                    state.history_summary += (
                        "\n[System] Rollback after a regression restored files, but rebuild/restage failed. "
                        "Do not proceed to unrelated cases until the restored binary is available."
                    )
                    return "abort"
                restored_result = runner.run_single(
                    Path(failing_case.script_path), capture_trace=True
                )
                post_restore_regressed = self._check_regression(
                    runner, baseline_pass_names, failing_case.name
                )
                failing_case.passed = restored_result.passed
                failing_case.exit_code = restored_result.exit_code
                failing_case.stdout = restored_result.stdout
                failing_case.stderr = restored_result.stderr
                failing_case.trace = restored_result.trace
                failing_case.duration_seconds = restored_result.duration_seconds
                failing_case.run_dir = restored_result.run_dir
                if restored_result.passed and not post_restore_regressed:
                    print(
                        f"    [rtest] 回滚后 {failing_case.name} 已通过，"
                        "且回归用例已恢复，无需继续修复本用例"
                    )
                    return "passed"
                if post_restore_regressed:
                    restored_regression_detail = _format_regression_details(
                        post_restore_regressed
                    )
                    print(
                        "    [rtest] 回滚后仍检测到回归，保持当前用例修复上下文，"
                        "不会切换去修老用例"
                    )
                    for line in restored_regression_detail.splitlines():
                        print(f"      {line}")
                state.regression_warning = (
                    "This round's edits made "
                    f"{failing_case.name} pass, but also caused the following cases to regress: "
                    + ", ".join(sorted(regressed))
                    + ". These edits have been rolled back. Regression evidence follows:\n"
                    + regression_detail
                    + (
                        "\n\nAfter rollback, these baseline cases still failed, so the next round must first account for stale binary or rollback-state issues:\n"
                        + _format_regression_details(post_restore_regressed)
                        if post_restore_regressed
                        else ""
                    )
                    + "\nThe next round must keep the current case passing and explain why these regression cases will not be broken again."
                )
                # 回归回滚后重置 stall 计数（#29）
                state.stall_count = 0
                return "continue"
            print(f"    [rtest] ✓ 用例 {failing_case.name} 修复成功（无回归）")
            return "passed"

        # 仍失败：stall 检测
        new_sig = new_result.failure_signature()
        if new_sig == state.last_failure_signature:
            state.stall_count += 1
        else:
            state.stall_count = 0
            state.last_failure_signature = new_sig
            state.edit_region_counts.clear()
        if state.stall_count + 1 >= STALL_SAME_SIGNATURE_ROUNDS:
            print(
                f"    [rtest] 连续 {state.stall_count + 1} 轮失败签名相同，"
                "判定为停滞风险；继续下一轮但要求改变策略"
            )
            state.history_summary += (
                "\n[System] The current failure signature has remained unchanged for multiple rounds. The next round must change strategy: "
                "prioritize reading the latest Rust files, necessary C functions, or test artifacts; do not keep submitting the same kind of edits."
            )
            return "continue"

        print(
            f"    [rtest] 用例仍失败 (exit={new_result.exit_code})，继续下一轮"
        )
        return "continue"

    # ---------------------------------------------------------- regression

    def _check_regression(
        self,
        runner: TestRunner,
        baseline_pass_names: Set[str],
        skip_case_name: str,
    ) -> Dict[str, TestCaseResult]:
        if not baseline_pass_names:
            return {}
        regressed: Dict[str, TestCaseResult] = {}
        for script in sorted(runner.test_dir.glob("*.sh")):
            if script.name == skip_case_name or script.name not in baseline_pass_names:
                continue
            r = runner.run_single(script, capture_trace=True)
            if not r.passed:
                regressed[script.name] = r
        return regressed


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@dataclass
class _RepairLoopState:
    history_summary: str
    last_build_error: str
    regression_warning: str
    last_failure_signature: str
    stall_count: int
    debug_probe_count: int
    edit_region_counts: Dict[str, int] = field(default_factory=dict)
    response_contract_failures: int = 0
    static_probes: Dict[str, object] = field(default_factory=dict)
    static_program_args: List[str] = field(default_factory=list)
    material_request_feedback: str = ""


@dataclass
class _MaterialRequestResult:
    new_material: bool = False
    added: List[str] = field(default_factory=list)
    already_available: List[str] = field(default_factory=list)
    unavailable: List[str] = field(default_factory=list)

    def merge(self, other: "_MaterialRequestResult") -> None:
        self.new_material = self.new_material or other.new_material
        self.added.extend(other.added)
        self.already_available.extend(other.already_available)
        self.unavailable.extend(other.unavailable)


def _script_size(case: TestCaseResult) -> int:
    try:
        return os.path.getsize(case.script_path)
    except OSError:
        return 1 << 30


def _strip_bash_xtrace_command(line: str) -> str:
    match = re.match(r"^\++\s+(.*)$", line.strip())
    return match.group(1).strip() if match else ""


def _normalize_tested_command(command: str, bin_name: str) -> str:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    if not tokens:
        return ""

    executable = tokens[0]
    base = Path(executable).name
    if base.endswith(".exe"):
        base = base[:-4]
    aliases = {
        bin_name,
        f"{bin_name}-rust",
        f"{bin_name}_rust",
    }
    if base not in aliases:
        return ""

    return " ".join([bin_name] + [shlex.quote(token) for token in tokens[1:]])


def _is_compare_or_diff_command(command: str) -> bool:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    if not tokens:
        return False
    name = Path(tokens[0]).name
    return name in {"compare", "diff", "cmp"}


def _is_failure_marker_command(command: str) -> bool:
    command = command.strip()
    return bool(
        re.match(r"(?:fail=1|Exit\s+\$?fail|exit\s+[1-9]\d*|return\s+[1-9]\d*)\b", command)
    )


def _unique_preserving_order(items) -> List[str]:
    seen: Set[str] = set()
    result: List[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _find_matching_script_lines(
    script_content: str,
    normalized_command: str,
    bin_name: str,
    *,
    limit: int,
) -> List[str]:
    try:
        command_tokens = shlex.split(normalized_command, posix=True)
    except ValueError:
        command_tokens = normalized_command.split()
    args = command_tokens[1:]
    matches: List[str] = []
    bin_pattern = re.compile(rf"(^|[\s;(]){re.escape(bin_name)}([\s;&|)]|$)")
    for raw in script_content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not bin_pattern.search(line):
            continue
        if all(arg == "-" or arg in line for arg in args[:4]):
            matches.append(line)
            if len(matches) >= limit:
                break
    return matches




def _edit_region_keys(edits: List[Dict]) -> List[str]:
    """粗粒度标记 edit 落点，避免同一失败下反复重写同一区域。"""
    keys: Set[str] = set()
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        path = str(edit.get("path") or "").replace("\\", "/")
        if not path:
            continue
        mode = str(edit.get("mode") or "").strip().lower()
        if mode in {
            "copy_range_after",
            "cp",
            "copy_c_string_array_after",
            "copy_c_str_array_after",
        }:
            try:
                start = int(edit.get("target_line") or edit.get("after_line") or 1)
            except (TypeError, ValueError):
                start = 1
            end = start
        elif mode == "insert_before":
            try:
                start = int(edit.get("before_line") or edit.get("target_line") or edit.get("start_line") or 1)
            except (TypeError, ValueError):
                start = 1
            end = start
        elif mode == "insert_after":
            try:
                start = int(edit.get("after_line") or edit.get("target_line") or edit.get("end_line") or edit.get("start_line") or 1)
            except (TypeError, ValueError):
                start = 1
            end = start
        else:
            try:
                start = int(edit.get("start_line") or edit.get("line") or 1)
            except (TypeError, ValueError):
                start = 1
            try:
                end = int(edit.get("end_line") or start)
            except (TypeError, ValueError):
                end = start
        if end < start:
            start, end = end, start
        midpoint = max(1, (start + end) // 2)
        bucket = ((midpoint - 1) // EDIT_REGION_BUCKET_LINES) * EDIT_REGION_BUCKET_LINES + 1
        keys.add(f"{path}:{bucket}-{bucket + EDIT_REGION_BUCKET_LINES - 1}")
    return sorted(keys)


def _format_material_request_feedback(result: _MaterialRequestResult) -> str:
    lines = ["[System] Material request result from the previous round:"]
    if result.added:
        lines.append("New material added to the current prompt:")
        for item in _unique_preserving_order(result.added)[:16]:
            lines.append(f"- {item}")
    if result.already_available:
        lines.append("Requested material already available in the current prompt:")
        for item in _unique_preserving_order(result.already_available)[:16]:
            lines.append(f"- {item}")
        lines.append(
            "Use these materials directly from the provided C/Rust/test blocks; repeating the same read request will not add more context."
        )
    if result.unavailable:
        unavailable = _unique_preserving_order(result.unavailable)
        ablation_rejections = [
            item for item in unavailable
            if "disabled by ablation" in item
        ]
        ordinary_unavailable = [
            item for item in unavailable
            if "disabled by ablation" not in item
        ]
        if ablation_rejections:
            lines.append("Material requests rejected by the ablation setting:")
            for item in ablation_rejections[:16]:
                lines.append(f"- {item}")
            lines.append(
                "These requests were intentionally refused for this experiment. Do not retry refused C source or test material reads with a different function, file, path, or range; use stdout/stderr plus the current Rust files and outputs instead."
            )
        if not ablation_rejections:
            lines.append("Unreadable or unmatched material requests:")
            for item in _unique_preserving_order(result.unavailable)[:16]:
                lines.append(f"- {item}")
            lines.append(
                "If this evidence is still needed, adjust the path or line range based on the source index/material table."
            )
        elif ordinary_unavailable:
            lines.append("Unreadable or unmatched material requests:")
            for item in ordinary_unavailable[:16]:
                lines.append(f"- {item}")
            lines.append(
                "If this evidence is still needed, adjust the path or line range based on the source index/material table."
            )
    if not result.added and not result.already_available and not result.unavailable:
        lines.append(
            "No resolver details were available. Use the current material table, request a more precise range, or provide concrete edits."
        )
    return "\n".join(lines)


def _format_c_request_label(req: Dict) -> str:
    query = str(req.get("query") or req.get("path") or req.get("file") or "").strip()
    mode = str(req.get("mode") or "").strip()
    if mode in {"line_range", "range"}:
        return f"{query}:{req.get('start_line')}-{req.get('end_line')}"
    return query or "<empty>"


def _has_meaningful_debug_probe(probe: Dict[str, object]) -> bool:
    candidate_lists = [probe.get("breakpoints")]
    targets = probe.get("targets")
    if isinstance(targets, dict):
        for target in ("rust", "c"):
            spec = targets.get(target)
            if isinstance(spec, dict):
                candidate_lists.append(spec.get("breakpoints"))
    for breakpoints in candidate_lists:
        if not isinstance(breakpoints, list):
            continue
        for item in breakpoints:
            if not isinstance(item, dict):
                continue
            file = str(item.get("file") or "").strip()
            try:
                line = int(item.get("line"))
            except (TypeError, ValueError):
                line = 0
            if file and line > 0:
                return True
    return False


def _format_regression_details(regressed: Dict[str, TestCaseResult]) -> str:
    """把回归用例失败证据压缩成可打印、可注入 prompt 的文本。"""
    lines: List[str] = []
    for name in sorted(regressed):
        result = regressed[name]
        lines.append(
            f"- {name}: exit={result.exit_code}, duration={result.duration_seconds}s"
        )
        excerpt = result.short_failure_excerpt(1200).strip()
        if excerpt:
            lines.append("  failure excerpt:")
            lines.extend(f"    {line}" for line in excerpt.splitlines()[-18:])
        trace = (result.trace or "").strip()
        if trace:
            lines.append("  trace tail:")
            lines.extend(f"    {line}" for line in trace.splitlines()[-18:])
    return "\n".join(lines)


def _looks_like_argv0_path_diff(text: str) -> bool:
    lowered = (text or "").lower()
    return (
        "usage:" in lowered
        and "datasets/" in lowered
        and "target/release" in lowered
        and ("which-rust" in lowered or "-rust" in lowered)
    )


def _looks_like_native_executable(path: str) -> bool:
    """Best-effort check whether a file is a native binary, not a text wrapper.

    Cargo's ``target/release/`` may end up holding tiny bash/sh stubs left
    behind by earlier repair attempts. Treat anything starting with ``#!`` as
    a script and reject it; otherwise accept files whose first bytes match a
    common executable magic (ELF / PE-MZ / Mach-O / shebang-less binaries).
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(4)
    except OSError:
        return False
    if not head:
        return False
    if head.startswith(b"#!"):
        return False
    # ELF
    if head.startswith(b"\x7fELF"):
        return True
    # PE / MZ
    if head[:2] == b"MZ":
        return True
    # Mach-O (32 / 64 / fat, both endians)
    macho_magics = {
        b"\xfe\xed\xfa\xce",
        b"\xfe\xed\xfa\xcf",
        b"\xce\xfa\xed\xfe",
        b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",
    }
    if head in macho_magics:
        return True
    # Wasm
    if head == b"\x00asm":
        return True
    return False


_MINIMAL_TEST_INIT_SH = r'''# Minimal test-framework shim for extracted shell tests.
# This intentionally implements only common helpers used by small benchmark
# datasets. It is not a full replacement for gnulib/coreutils tests/init.sh.

: "${fail:=0}"

path_prepend_()
{
  # The runner exposes the program under test through exported functions and
  # ./<program> wrappers. Avoid mutating PATH by default; PATH-sensitive tools
  # such as which must see the test's own PATH changes.
  :
}

framework_failure_()
{
  echo "framework failure" >&2
  exit 99
}

skip_()
{
  echo "skipped: $*" >&2
  exit 77
}

mkfifo_or_skip_()
{
  name=$1
  if command -v mkfifo >/dev/null 2>&1; then
    mkfifo "$name" 2>/dev/null && return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    python3 -c 'import os,sys; os.mkfifo(sys.argv[1])' "$name" 2>/dev/null && return 0
  fi
  if command -v python >/dev/null 2>&1; then
    python -c 'import os,sys; os.mkfifo(sys.argv[1])' "$name" 2>/dev/null && return 0
  fi
  skip_ "cannot create fifo $name"
}

retry_delay_()
{
  func=$1
  delay=$2
  retries=$3
  shift 3

  i=0
  while :; do
    "$func" "$delay" "$@" && return 0
    i=$((i + 1))
    test "$i" -ge "$retries" && return 1
    sleep "$delay"
  done
}

compare()
{
  diff -u "$@"
}

returns_()
{
  expected=$1
  shift
  "$@"
  status=$?
  test "$status" -eq "$expected"
}

Exit()
{
  exit "$1"
}

getlimits_()
{
  : "${SSIZE_MAX:=9223372036854775807}"
  export SSIZE_MAX
}

get_min_ulimit_v_()
{
  # Conservative default: enough for small tests, low enough for allocation
  # guard checks that add a small margin.
  echo 65536
}

# Perl support: many coreutils tests embed Perl scripts.
# Locate perl and export $PERL; skip the test if unavailable.
if command -v perl >/dev/null 2>&1; then
  PERL=$(command -v perl)
else
  PERL=
fi
export PERL

require_perl_()
{
  if test -z "$PERL"; then
    skip_ "this test requires perl"
  fi
}

require_readable_root_()
{
  test -r / || skip_ "/ is not readable"
}

require_root_()
{
  test "$(id -u)" -eq 0 || skip_ "this test requires root"
}

_cgr_cleanup_on_exit_()
{
  status=$?
  if command -v cleanup_ >/dev/null 2>&1; then
    cleanup_
  fi
  exit "$status"
}
trap _cgr_cleanup_on_exit_ 0
'''


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
    # 与 RustTestAgent.__init__ 默认值保持一致
    parser.add_argument("--max-repair-iterations", type=int, default=20)
    parser.add_argument("--build-timeout-seconds", type=int, default=600)
    parser.add_argument("--test-timeout-seconds", type=int, default=30)
    parser.add_argument(
        "--prompt-budget-chars",
        type=int,
        default=PROMPT_MATERIAL_BUDGET_CHARS,
        help="提示词材料预算（按字符近似 token，默认 256000，约 64k token 级别）",
    )
    parser.add_argument(
        "--source-records",
        default="",
        help="C 源码 JSON 路径；不传则回落到 src/parse/res/<name>.json",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="打印 LLM 原始响应（调试用）",
    )
    parser.add_argument(
        "--translate-tests",
        action="store_true",
        help="兼容旧命令的保留参数；测试脚本现为只读输入，传入后也不会调用 LLM 改写",
    )
    parser.add_argument(
        "--use-log-agent",
        action="store_true",
        help="启用运行时日志，以及由 LLM 发起的 Rust/C 动态或静态插桩",
    )
    parser.add_argument("--log-agent-max-debug-probes", type=int, default=6)
    return parser


def main() -> int:
    parser = _build_argparser()
    args = parser.parse_args()
    config = Config(config_path=args.config_file)
    config.round_log_project_name = Path(args.c_project_path).name or ""
    agent = RustTestAgent(
        config=config,
        max_repair_iterations=args.max_repair_iterations,
        build_timeout_seconds=args.build_timeout_seconds,
        test_timeout_seconds=args.test_timeout_seconds,
        verbose=args.verbose,
        source_records_path=args.source_records or None,
        translate_tests=args.translate_tests,
        enable_log_agent=args.use_log_agent,
        max_debug_probes=args.log_agent_max_debug_probes,
        prompt_budget_chars=args.prompt_budget_chars,
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

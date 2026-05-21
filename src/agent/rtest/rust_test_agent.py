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
import hashlib
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set

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
    SEED_C_LIMIT,
    SEED_RUST_LIMIT,
    STALL_SAME_SIGNATURE_ROUNDS,
)
from .models import TestCaseResult, TestRunSummary  # noqa: E402
from .repair_adapter import RepairAdapter  # noqa: E402
from .repair_prompt import MaterialBudget, build_repair_prompt  # noqa: E402
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
from .test_runner import TestRunner  # noqa: E402
from .test_translator import translate_shell_tests  # noqa: E402


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class RustTestAgent:
    """根据 C 项目的 sh 测试脚本，验证翻译后的 Rust 项目功能完整性，并修复失败用例。"""

    def __init__(
        self,
        config: Optional[Config] = None,
        max_repair_iterations: int = 20,
        build_timeout_seconds: int = 600,
        test_timeout_seconds: int = 30,
        verbose: bool = False,
        source_records_path: Optional[str] = None,
        translate_tests: bool = False,
    ):
        self.config = config or Config()
        self.llm = Model(self.config)
        self.max_repair_iterations = max_repair_iterations
        self.build_timeout_seconds = build_timeout_seconds
        self.test_timeout_seconds = test_timeout_seconds
        self.verbose = verbose
        self.source_records_path = source_records_path
        self.translate_tests = translate_tests

        # 复用 RustRepairAgent 的本地清洗 / 结构化编辑能力，通过 adapter 访问
        # 它的私有方法，避免耦合未来 RustRepairAgent 的重构。
        self._repair_helper = RustRepairAgent(config=self.config, max_iterations=1)
        self.adapter = RepairAdapter(self._repair_helper)

    # ---------------------------------------------------------------- public

    def run(
        self,
        rust_project_path: str,
        c_project_path: str,
        binary_name: Optional[str] = None,
    ) -> TestRunSummary:
        rust_project_path = str(Path(rust_project_path).resolve())
        c_project_path = str(Path(c_project_path).resolve())
        bin_name = binary_name or self._infer_bin_name(c_project_path, rust_project_path)
        if not binary_name:
            print(f"[rtest] 自动推断 bin_name = {bin_name}（C 项目目录 / Cargo.toml）")

        # C 参考可执行文件现在是**可选**的：测试可以纯粹基于 Rust 输出
        # 与期望输出（grep / diff fixture）做比对，不强依赖 C 对照实现。
        c_binary = self._locate_c_binary(c_project_path, bin_name) or ""
        if c_binary:
            print(f"[rtest] 使用 C 参考可执行文件：{c_binary}")
        else:
            project_name = Path(c_project_path).name
            print(
                f"[rtest] 未在 {c_project_path} 找到 C 参考可执行文件 "
                f"（期望 {bin_name} / {project_name} 或带 .exe 后缀），"
                f"将仅基于 Rust 输出做测试"
            )

        test_src = self._find_c_test_dir(c_project_path)
        if not test_src:
            print(f"[rtest] 未在 C 项目中找到 test/ 目录：{c_project_path}")
            return TestRunSummary(0, 0, 0, [])
        test_dst = os.path.join(rust_project_path, "test")
        copied_files = self._copy_test_tree(test_src, test_dst)
        print(f"[rtest] 已整体复制测试目录：{test_src} -> {test_dst} ({copied_files} 个文件)")
        self._ensure_test_framework_shim(test_dst)

        if self.translate_tests:
            print("[rtest] 启用 LLM 测试脚本翻译模式")
            scripts = translate_shell_tests(
                src_dir=Path(test_src),
                dst_dir=Path(test_dst),
                project_name=bin_name,
                c_binary_available=bool(c_binary),
                llm=self.llm,
                adapter=self.adapter,
                verbose=self.verbose,
            )
        else:
            # 默认不改写测试脚本：先原样复制 C 项目的 sh，由 TestRunner 提供
            # Rust/C wrapper、srcdir/abs_srcdir 等兼容环境。LLM 修复阶段只在确认为
            # 测试迁移问题时，才允许局部编辑当前失败脚本。
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
                f"[rtest] 错误：未找到 Rust 可执行文件 {expected}[.exe]。"
                f" 请确认 Cargo.toml 里 [[bin]] name = \"{rust_bin_name}\"，"
                f" 并已成功执行 cargo build --release。"
            )
            return TestRunSummary(0, 0, 0, [])
        print(f"[rtest] 使用 Rust 可执行文件：{binary_path}")

        runner = TestRunner(
            test_dir=test_dst,
            bin_name=bin_name,
            timeout_seconds=self.test_timeout_seconds,
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

            baseline_pass_names: Set[str] = {c.name for c in summary.results if c.passed}

            failing_cases = [c for c in summary.results if not c.passed]
            failing_cases.sort(key=_script_size)

            for case in failing_cases:
                fixed = self._repair_failing_case(
                    rust_project_path=rust_project_path,
                    bin_name=bin_name,
                    runner=runner,
                    project_structure=project_structure,
                    source_index=source_index,
                    failing_case=case,
                    baseline_pass_names=baseline_pass_names,
                )
                if fixed:
                    baseline_pass_names.add(case.name)

            final_binary = (
                self._locate_release_binary(rust_project_path, rust_bin_name) or binary_path
            )
            if final_binary != binary_path:
                runner.restage_rust_binary(final_binary)
            final_summary = runner.run_all(self._discover_shell_scripts(Path(test_dst)))
            self._print_summary(final_summary, label="最终测试结果")
            return final_summary
        finally:
            runner.cleanup()

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

    @staticmethod
    def _is_current_translated_test_script(rel_path: str, failing_case: TestCaseResult) -> bool:
        normalized = (rel_path or "").replace("\\", "/").strip().lstrip("/")
        return normalized == f"test/{Path(failing_case.script_path).name}"

    def _filter_disallowed_edits(self, edits: List[Dict], failing_case: TestCaseResult) -> List[Dict]:
        clean: List[Dict] = []
        for edit in edits or []:
            if not isinstance(edit, dict):
                continue
            rel = (edit.get("path") or "").replace("\\", "/")
            if self._is_editable_rust_path(rel) or self._is_current_translated_test_script(rel, failing_case):
                clean.append(edit)
                continue
            if rel == Path(failing_case.script_path).name:
                edit = dict(edit)
                edit["path"] = f"test/{rel}"
                clean.append(edit)
                continue
            else:
                print(f"    [rtest] 已拒绝对非 Rust 源文件的编辑请求：{rel or '(空 path)'}")
                continue
        return clean

    def _filter_fake_impl_edits(
        self,
        edits: List[Dict],
        expected_outputs: List[str],
        failing_case: TestCaseResult,
    ) -> List[Dict]:
        clean: List[Dict] = []
        for edit in edits:
            if not isinstance(edit, dict):
                continue
            rel = (edit.get("path") or "").replace("\\", "/").strip().lstrip("/")
            if self._is_current_translated_test_script(rel, failing_case):
                clean.append(edit)
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
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            rows.append(f"- {rel} ({size} bytes)")
            if len(rows) >= limit:
                break
        return "\n".join(rows)

    @staticmethod
    def _read_test_artifact(failing_case: TestCaseResult, rel_path: str, max_chars: int = 12000) -> str:
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
        try:
            text = full.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""
        return text[-max_chars:]

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
                "\n\n[系统诊断] 当前 diff 看起来主要来自 C_BIN 与 RUST_BIN 的 argv[0]/绝对路径不同。"
                "这通常是测试迁移问题；优先修当前 test 脚本的运行/归一化方式，不要在 Rust 代码里硬编码路径。"
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
        if flags:
            print(f"  [rtest] 推断被测 flag：{', '.join(flags)}")
        if keywords:
            print(f"  [rtest] 推断被测关键字：{', '.join(keywords)}")

        material = MaterialBudget()

        # 首轮主动注入
        for rec in seed_c_sources(flags, source_index, keywords=keywords, limit=SEED_C_LIMIT):
            if material.add_c_record(rec):
                print(f"  [rtest] 首轮注入 C 源码：{rec.get('name')} [{rec.get('file')}]")
        for rel, text in seed_rust_files(
            flags, rust_project_path, keywords=keywords, limit=SEED_RUST_LIMIT
        ).items():
            if material.add_rust_file(rel, text):
                print(f"  [rtest] 首轮注入 Rust 文件：{rel}")

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
            last_edits_fingerprint="",
            stall_count=0,
            dup_edits_count=0,
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
                except SnapshotError as exc:
                    print(f"  [rtest] 回滚失败，项目可能包含未完成修复：{exc}")
            snapshot.discard()

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
        prompt = build_repair_prompt(
            failing_case=failing_case,
            script_content=script_content,
            project_structure=project_structure,
            rust_overview=rust_overview,
            material=material,
            history_summary=state.history_summary,
            source_records_index=source_index_display,
            attempt=attempt,
            max_attempts=self.max_repair_iterations,
            last_build_error=state.last_build_error,
            flags=flags,
            keywords=keywords,
            expected_outputs=expected_outputs,
            regression_warning=state.regression_warning,
            focused_failure=self._focused_failure_block(failing_case),
            test_artifact_index=self._list_test_artifacts(failing_case),
        )
        state.regression_warning = ""  # 只提示一次

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
        if self.verbose:
            print("    [rtest] LLM raw response（verbose）:")
            print("    " + (text or "")[:4000].replace("\n", "\n    "))

        payload = self.adapter.extract_json_payload(text)
        if not isinstance(payload, dict):
            state.json_parse_failures += 1
            max_json_retries = 3
            if state.json_parse_failures >= max_json_retries:
                print(
                    f"    [rtest] LLM 连续 {state.json_parse_failures} 轮返回不可解析 JSON，"
                    "终止本用例修复"
                )
                return "abort"
            # 把 LLM 原始回复尾部带入下一轮，让模型看到自己的格式错误并自我纠正。
            raw_tail = (text or "")[-1500:].strip()
            state.history_summary += (
                f"\n[系统] 上一轮（第 {attempt} 轮）LLM 返回无法解析为 JSON，"
                f"已跳过（连续 {state.json_parse_failures}/{max_json_retries} 次）。"
                f"\n上一轮原始回复尾部：\n```\n{raw_tail}\n```\n"
                "下一轮必须严格只返回 JSON 对象，不要包含任何 markdown 围栏之外的文字。"
            )
            print(
                f"    [rtest] LLM 返回不可解析为 JSON（第 {state.json_parse_failures} 次），"
                "继续下一轮重试"
            )
            return "continue"

        # JSON 解析成功，重置连续失败计数
        state.json_parse_failures = 0

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
        raw_edits = self._filter_disallowed_edits(payload.get("edits") or [], failing_case)
        edits = self._filter_fake_impl_edits(raw_edits, expected_outputs, failing_case)
        if raw_edits and not edits:
            state.history_summary += (
                "\n[系统] 上一轮提交的所有 edit 都被反作弊检测拒绝，"
                "请根据 C 源码真正实现该 flag 的逻辑，而不是占位/复读期望输出。"
            )

        # Edits 去重（#20）
        if edits:
            fp = _edits_fingerprint(edits)
            if fp == state.last_edits_fingerprint:
                state.dup_edits_count += 1
                print(
                    f"    [rtest] LLM 返回与上一轮完全相同的 edits（第 {state.dup_edits_count} 次），"
                    "跳过重复应用"
                )
                state.history_summary += (
                    "\n[系统] 上一轮 edits 与更早一轮完全相同，已跳过应用。"
                    "下一轮必须读取新材料或给出不同的局部修复。"
                )
                edits = []
            else:
                state.last_edits_fingerprint = fp
                state.dup_edits_count = 0

        new_material = self._absorb_material_requests(
            rust_project_path, source_index, cgr_requests, rust_read_requests, material
        )
        if self._absorb_test_artifact_requests(
            failing_case, test_artifact_requests, material
        ):
            new_material = True

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
                    if self._is_current_translated_test_script(rel, failing_case):
                        continue
                    else:
                        refreshed = self.adapter.read_file_slice(rust_project_path, rel)
                        if refreshed:
                            material.add_rust_file(rel, refreshed)

                return self._build_and_verify(
                    rust_project_path=rust_project_path,
                    bin_name=bin_name,
                    runner=runner,
                    failing_case=failing_case,
                    baseline_pass_names=baseline_pass_names,
                    state=state,
                    snapshot=snapshot,
                )

        if payload.get("complete"):
            print("    [rtest] LLM 标记 complete=true，但用例仍未通过，终止本用例修复")
            return "abort"

        if not new_material and not edits:
            print("    [rtest] LLM 既没请求材料也没产生新编辑，继续下一轮并要求改变策略")
            state.history_summary += (
                "\n[系统] 上一轮没有产生可执行动作。下一轮必须请求缺失材料，"
                "或基于已有材料给出新的有效 edits。"
            )
            return "continue"
        return "continue"

    def _absorb_material_requests(
        self,
        rust_project_path: str,
        source_index: CSourceIndex,
        cgr_requests: List[Dict],
        rust_read_requests: List[Dict],
        material: MaterialBudget,
    ) -> bool:
        new_material = False
        for req in cgr_requests:
            if not isinstance(req, dict):
                continue
            rec = source_index.fulfill_request(req)
            if rec and not material.has_c_record(rec):
                if material.add_c_record(rec):
                    kind_tag = "file" if rec.get("is_file_aggregate") else "function"
                    print(
                        f"    [rtest] 提供 C {kind_tag}：{rec.get('name')} [{rec.get('file')}]"
                    )
                    new_material = True

        for req in rust_read_requests:
            if isinstance(req, dict):
                rel = str(req.get("path") or "").replace("\\", "/")
            else:
                rel = str(req or "").replace("\\", "/")
            if not rel or material.has_rust_file(rel):
                continue
            if not self._is_editable_rust_path(rel):
                print(f"    [rtest] 拒绝读取非 Rust 源文件：{rel}")
                continue
            content = self.adapter.read_file_slice(rust_project_path, rel)
            if content and material.add_rust_file(rel, content):
                new_material = True
                print(f"    [rtest] 提供 Rust 文件：{rel} ({len(content)} chars)")
        return new_material

    def _absorb_test_artifact_requests(
        self,
        failing_case: TestCaseResult,
        requests: List[Dict],
        material: MaterialBudget,
    ) -> bool:
        new_material = False
        for req in requests or []:
            if isinstance(req, dict):
                rel = str(req.get("path") or "").replace("\\", "/")
            else:
                rel = str(req or "").replace("\\", "/")
            rel = rel.strip().lstrip("/")
            if not rel or material.has_test_artifact(rel):
                continue
            content = self._read_test_artifact(failing_case, rel)
            if content and material.add_test_artifact(rel, content):
                print(f"    [rtest] 提供测试产物：{rel} ({len(content)} chars)")
                new_material = True
            elif not content:
                print(f"    [rtest] 测试产物不存在或不可读：{rel}")
        return new_material

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
    ) -> str:
        if not self._cargo_build_release(rust_project_path):
            ok, build_output = self.adapter.run_command(
                "cargo build --release",
                rust_project_path,
                timeout_seconds=self.build_timeout_seconds,
            )
            state.last_build_error = build_output if not ok else ""
            print("    [rtest] 修复后编译失败，将编译错误带入下一轮")
            state.history_summary += "\n[系统] 上一次编辑导致编译失败，请优先修复编译错误。"
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
                self._cargo_build_release(rust_project_path)
                restored_binary = self._locate_release_binary(
                    rust_project_path, f"{bin_name}-rust"
                )
                if restored_binary:
                    runner.restage_rust_binary(restored_binary)
                state.regression_warning = (
                    "本次 edits 让 "
                    f"{failing_case.name} 通过，但同时让以下用例回归失败："
                    + ", ".join(sorted(regressed))
                    + "。已回滚本次 edits。回归失败证据如下：\n"
                    + regression_detail
                    + "\n下一轮必须同时保持当前用例通过，并解释为什么不会再次破坏这些回归用例。"
                )
                failing_case.passed = False
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
        if state.stall_count + 1 >= STALL_SAME_SIGNATURE_ROUNDS:
            print(
                f"    [rtest] 连续 {state.stall_count + 1} 轮失败签名相同，"
                "判定为停滞风险；继续下一轮但要求改变策略"
            )
            state.history_summary += (
                "\n[系统] 当前失败签名已连续多轮不变。下一轮必须改变策略："
                "优先读取最新 Rust 文件、必要 C 函数或测试产物；不要继续提交同类 edits。"
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
    last_edits_fingerprint: str
    stall_count: int
    dup_edits_count: int
    json_parse_failures: int = 0


def _script_size(case: TestCaseResult) -> int:
    try:
        return os.path.getsize(case.script_path)
    except OSError:
        return 1 << 30


def _edits_fingerprint(edits: List[Dict]) -> str:
    """对一组 edits 计算稳定指纹，用于去重（#20）。"""
    try:
        payload = json.dumps(edits, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        payload = repr(edits)
    return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()[:16]


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
        help="使用旧的 LLM 测试脚本翻译模式；默认原样复制并运行 C 项目 sh",
    )
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
        verbose=args.verbose,
        source_records_path=args.source_records or None,
        translate_tests=args.translate_tests,
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

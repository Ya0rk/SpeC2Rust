"""测试运行器。

修复原实现：

- **#9**：原 ``_run_single_test`` 每跑一个测试都 ``shutil.copy2`` Rust/C 二进制。
  现在 TestRunner 在 ``stage(...)`` 阶段一次性 stage 到 wrapper_dir，之后每个测试
  直接复用；只有当外部调用者显式 ``restage_rust_binary`` 时（例如 rebuild 之后）
  才重新拷贝。
- **#11**：首次全量跑测试时默认 **不** 捕获 bash -x trace，大幅加速首跑；
  修复循环阶段若需要再懒加载 trace。
- **#14**：新增 ``cleanup()`` 显式清理 wrapper_dir / .run_<stem> 临时目录。
- **#15**：不再把 wrapper_dir 注入 PATH。对 ``which`` 这类 PATH-sensitive
- **#15**：不再把 wrapper_dir 注入 PATH。对 ``which`` 这类 PATH-sensitive
  项目，测试框架自己的 wrapper 不能污染被测程序看到的 PATH；直接命令调用通过
  bash function 提供。原始 C 测试脚本里的项目同名命令会映射到 Rust binary，
  C 参考 binary 只通过 ``$C_BIN`` / ``<bin>-c`` 暴露。
"""

from __future__ import annotations

import os
import signal
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .constants import TEST_OUTPUT_TAIL_CHARS, TRACE_TAIL_CHARS
from .models import TestCaseResult, TestRunSummary


@dataclass
class TestEnvironment:
    """单次测试调用时需要的环境信息。"""

    wrapper_dir: Path
    rust_wrapper: Path
    c_wrapper: Optional[Path]
    rust_named_wrapper: Path
    c_named_wrapper: Optional[Path]
    base_env: Dict[str, str]
    rust_binary_path: str
    c_binary_path: Optional[str]


class TestRunner:
    """把 staging / 运行 / trace 捕获的职责集中到一处。"""

    def __init__(
        self,
        test_dir: str,
        bin_name: str,
        timeout_seconds: int = 120,
    ):
        self.test_dir = Path(test_dir).resolve()
        self.bin_name = bin_name
        self.timeout_seconds = timeout_seconds
        self.wrapper_dir = self.test_dir / ".bin"
        self._env: Optional[TestEnvironment] = None
        self._run_dirs: List[Path] = []

    # ------------------------------------------------------------- staging

    def stage(self, rust_binary_path: str, c_binary_path: Optional[str]) -> None:
        """一次性 stage 两个二进制到 wrapper_dir，供整个测试过程复用。"""
        self.wrapper_dir.mkdir(exist_ok=True)

        rust_bin = Path(rust_binary_path)
        rust_suffix = rust_bin.suffix
        rust_wrapper = self.wrapper_dir / f"{self.bin_name}-rust{rust_suffix}"
        _copy_replace(rust_bin, rust_wrapper)
        _chmod_executable(rust_wrapper)

        # 原始 C 测试脚本通常直接调用项目同名命令（如 `head`）。
        # 在 RustTestAgent 中，这个同名命令必须指向 Rust binary。
        rust_named_wrapper = self.wrapper_dir / f"{self.bin_name}{rust_suffix}"
        _copy_replace(rust_bin, rust_named_wrapper)
        _chmod_executable(rust_named_wrapper)

        c_wrapper: Optional[Path] = None
        c_named_wrapper: Optional[Path] = None
        if c_binary_path and os.path.isfile(c_binary_path):
            c_bin = Path(c_binary_path)
            c_suffix = c_bin.suffix
            c_wrapper = self.wrapper_dir / f"{self.bin_name}-c{c_suffix}"
            _copy_replace(c_bin, c_wrapper)
            _chmod_executable(c_wrapper)
            c_named_wrapper = c_wrapper

        env = os.environ.copy()
        env["CGR_WRAPPER_DIR"] = _to_bash_path(self.wrapper_dir.resolve())
        env["RUST_BIN"] = _to_bash_path(Path(rust_binary_path).resolve())
        env["RUST_WRAPPER_BIN"] = _to_bash_path(rust_wrapper.resolve())
        env["RUST_NAMED_WRAPPER_BIN"] = _to_bash_path(rust_named_wrapper.resolve())
        if c_binary_path:
            env["C_BIN"] = _to_bash_path(Path(c_binary_path).resolve())
        if c_wrapper:
            env["C_WRAPPER_BIN"] = _to_bash_path(c_wrapper.resolve())
        if c_named_wrapper:
            env["C_NAMED_WRAPPER_BIN"] = _to_bash_path(c_named_wrapper.resolve())
        env.setdefault("LC_ALL", "C")

        self._env = TestEnvironment(
            wrapper_dir=self.wrapper_dir,
            rust_wrapper=rust_wrapper,
            c_wrapper=c_wrapper,
            rust_named_wrapper=rust_named_wrapper,
            c_named_wrapper=c_named_wrapper,
            base_env=env,
            rust_binary_path=rust_binary_path,
            c_binary_path=c_binary_path,
        )

    def restage_rust_binary(self, rust_binary_path: str) -> None:
        """rebuild 后调用，只更新 Rust wrapper；C wrapper 不变。"""
        if self._env is None:
            self.stage(rust_binary_path, None)
            return
        self.wrapper_dir.mkdir(parents=True, exist_ok=True)
        _copy_replace(Path(rust_binary_path), self._env.rust_wrapper)
        _chmod_executable(self._env.rust_wrapper)
        _copy_replace(Path(rust_binary_path), self._env.rust_named_wrapper)
        _chmod_executable(self._env.rust_named_wrapper)
        self._env.rust_binary_path = rust_binary_path
        self._env.base_env["RUST_BIN"] = _to_bash_path(Path(rust_binary_path).resolve())

    # ------------------------------------------------------------- running

    def run_single(
        self,
        script_path: Path,
        *,
        capture_trace: bool = False,
    ) -> TestCaseResult:
        if self._env is None:
            raise RuntimeError("TestRunner 未 stage，无法运行测试")

        run_dir = self.test_dir / f".run_{script_path.stem}"
        if run_dir.exists():
            shutil.rmtree(run_dir, ignore_errors=True)
        run_dir.mkdir(exist_ok=True)
        self._run_dirs.append(run_dir)
        _copy_fixtures_into(script_path.parent, run_dir)
        self._stage_wrappers_into_run_dir(run_dir)
        bash_env = self._write_bash_env(run_dir)

        started = time.time()
        timed_out = False
        try:
            env = self._env.base_env.copy()
            if bash_env:
                env["BASH_ENV"] = _to_bash_path(bash_env.resolve())
            proc = _run_bash_with_timeout(
                ["bash", "-lc", self._bash_invocation(script_path, run_dir, trace=False)],
                cwd=str(run_dir),
                env=env,
                timeout=self.timeout_seconds,
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
            stderr = f"测试超时 (> {self.timeout_seconds}s)"
            exit_code = -1
            passed = False
            timed_out = True

        result = TestCaseResult(
            name=script_path.name,
            script_path=str(script_path),
            passed=passed,
            exit_code=exit_code,
            stdout=stdout[-TEST_OUTPUT_TAIL_CHARS:],
            stderr=stderr[-TEST_OUTPUT_TAIL_CHARS:],
            duration_seconds=round(time.time() - started, 2),
        )
        if timed_out:
            result.trace = f"<测试超时，已终止进程组；跳过 bash -x 复跑，避免二次卡住>"
        elif not passed and capture_trace:
            result.trace = self._capture_trace(script_path, run_dir)
        return result

    def run_all(self, scripts: List[Path]) -> TestRunSummary:
        results: List[TestCaseResult] = []
        passed = 0
        for script in scripts:
            r = self.run_single(script, capture_trace=False)
            results.append(r)
            if r.passed:
                passed += 1
                print(f"  ✓ {r.name} ({r.duration_seconds}s)")
            else:
                print(f"  ✗ {r.name} (exit={r.exit_code})")
                excerpt = (r.stderr.strip() or r.stdout.strip()).splitlines()
                if excerpt:
                    print(f"    ↳ {excerpt[-1][:240]}")
        return TestRunSummary(
            total=len(results), passed=passed, failed=len(results) - passed, results=results
        )

    def capture_trace_for(self, script_path: Path) -> str:
        """按需捕获单个测试的 bash -x trace。"""
        if self._env is None:
            return ""
        run_dir = self.test_dir / f".run_{script_path.stem}"
        if not run_dir.exists():
            run_dir.mkdir()
            _copy_fixtures_into(script_path.parent, run_dir)
            self._stage_wrappers_into_run_dir(run_dir)
            self._write_bash_env(run_dir)
            self._run_dirs.append(run_dir)
        return self._capture_trace(script_path, run_dir)

    def _capture_trace(self, script_path: Path, run_dir: Path) -> str:
        try:
            env = self._env.base_env.copy() if self._env else os.environ.copy()
            bash_env = run_dir / ".cgr_bash_env"
            if bash_env.is_file():
                env["BASH_ENV"] = _to_bash_path(bash_env.resolve())
            proc = _run_bash_with_timeout(
                ["bash", "-lc", self._bash_invocation(script_path, run_dir, trace=True)],
                cwd=str(run_dir),
                env=env,
                timeout=self.timeout_seconds,
            )
            trace = proc.stderr.decode("utf-8", errors="ignore")
        except subprocess.TimeoutExpired:
            return f"<trace 捕获超时 (> {self.timeout_seconds}s)，已终止进程组>"
        except Exception as exc:  # noqa: BLE001
            return f"<trace 捕获失败：{exc}>"
        if len(trace) > TRACE_TAIL_CHARS:
            trace = trace[-TRACE_TAIL_CHARS:]
        return trace

    def _bash_invocation(self, script_path: Path, run_dir: Path, *, trace: bool) -> str:
        if self._env is None:
            return f"bash {shlex.quote(_to_bash_path(script_path.resolve()))}"

        exports = [
            f"export RUST_BIN={shlex.quote(self._env.base_env['RUST_BIN'])}",
            f"export CGR_WRAPPER_DIR={shlex.quote(self._env.base_env['CGR_WRAPPER_DIR'])}",
            f"export RUST_WRAPPER_BIN={shlex.quote(self._env.base_env['RUST_WRAPPER_BIN'])}",
            f"export RUST_NAMED_WRAPPER_BIN={shlex.quote(self._env.base_env['RUST_NAMED_WRAPPER_BIN'])}",
            "export srcdir=.",
            f"export abs_srcdir={shlex.quote(_to_bash_path(run_dir.resolve()))}",
            "export builddir=.",
            f"export abs_builddir={shlex.quote(_to_bash_path(run_dir.resolve()))}",
            "export top_srcdir=.",
            f"export abs_top_srcdir={shlex.quote(_to_bash_path(run_dir.resolve()))}",
            "export LC_ALL=C",
        ]
        bash_env = run_dir / ".cgr_bash_env"
        if bash_env.is_file():
            exports.append(f"export BASH_ENV={shlex.quote(_to_bash_path(bash_env.resolve()))}")
        if self._env.base_env.get("C_BIN"):
            exports.append(f"export C_BIN={shlex.quote(self._env.base_env['C_BIN'])}")
        if self._env.base_env.get("C_WRAPPER_BIN"):
            exports.append(
                f"export C_WRAPPER_BIN={shlex.quote(self._env.base_env['C_WRAPPER_BIN'])}"
            )
        flag = "-x " if trace else ""
        script = shlex.quote(_to_bash_path(script_path.resolve()))
        return "; ".join(exports) + f"; bash {flag}{script}"

    def _write_bash_env(self, run_dir: Path) -> Optional[Path]:
        """Write a BASH_ENV prelude that maps project commands to wrappers.

        For original C test scripts, `<bin>` is the program under test. Here it
        must call Rust, while the optional C reference is exposed as `<bin>-c`.
        """
        if self._env is None:
            return None

        defs = [
            "# Auto-generated by RustTestAgent.",
            "# Sourced by bash through BASH_ENV for each test script.",
        ]
        rust_name = f"{self.bin_name}-rust"
        if _safe_bash_function_name(rust_name):
            defs.append(_bash_function_definition(rust_name, "RUST_BIN"))

        if _safe_bash_function_name(self.bin_name):
            defs.append(_bash_function_definition(self.bin_name, "RUST_BIN"))

        c_name = f"{self.bin_name}-c"
        if self._env.base_env.get("C_BIN") and _safe_bash_function_name(c_name):
            defs.append(_bash_function_definition(c_name, "C_BIN"))

        path = run_dir / ".cgr_bash_env"
        try:
            path.write_text("\n".join(defs) + "\n", encoding="utf-8", newline="\n")
        except OSError:
            return None
        return path

    def _stage_wrappers_into_run_dir(self, run_dir: Path) -> None:
        if self._env is None:
            return
        rust_target = run_dir / self._env.rust_named_wrapper.name
        rust_plain = run_dir / self.bin_name
        for target in (rust_target, rust_plain):
            _copy_replace(self._env.rust_named_wrapper, target)
            _chmod_executable(target)
        if self._env.rust_wrapper.name != self._env.rust_named_wrapper.name:
            target = run_dir / self._env.rust_wrapper.name
            _copy_replace(self._env.rust_wrapper, target)
            _chmod_executable(target)
        if self._env.c_named_wrapper:
            target = run_dir / self._env.c_named_wrapper.name
            _copy_replace(self._env.c_named_wrapper, target)
            _chmod_executable(target)

    # ------------------------------------------------------------- cleanup

    def cleanup(self) -> None:
        if self.wrapper_dir.exists():
            shutil.rmtree(self.wrapper_dir, ignore_errors=True)
        for run_dir in self._run_dirs:
            if run_dir.exists():
                shutil.rmtree(run_dir, ignore_errors=True)
        self._run_dirs.clear()


# --------------------------------------------------------------- helpers


def _copy_replace(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        try:
            dst.unlink()
        except OSError:
            pass
    shutil.copy2(src, dst)


def _run_bash_with_timeout(
    args: List[str],
    *,
    cwd: str,
    env: Dict[str, str],
    timeout: int,
) -> subprocess.CompletedProcess:
    """Run bash and kill the whole process group on timeout."""
    popen_kwargs = {
        "cwd": cwd,
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(args, **popen_kwargs)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)
    except subprocess.TimeoutExpired as exc:
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                pass
            try:
                proc.kill()
            except OSError:
                pass
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                try:
                    proc.kill()
                except OSError:
                    pass
        stdout, stderr = proc.communicate()
        exc.output = stdout
        exc.stderr = stderr
        raise exc


def _chmod_executable(path: Path) -> None:
    try:
        os.chmod(path, 0o755)
    except OSError:
        pass


_BASH_FUNCTION_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


def _safe_bash_function_name(name: str) -> bool:
    return bool(_BASH_FUNCTION_NAME_RE.fullmatch(name))


def _bash_function_definition(name: str, command_env_var: str) -> str:
    """Build a bash function definition for the BASH_ENV prelude."""
    return f'function {name} {{ "${{{command_env_var}}}" "$@"; }}'


def _to_bash_path(path: Path) -> str:
    """Return a path that bash on Windows/MSYS and POSIX shells can execute."""
    resolved = str(path)
    if os.name != "nt":
        return resolved

    try:
        proc = subprocess.run(
            ["cygpath", "-u", resolved],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )
        if proc.returncode == 0:
            converted = proc.stdout.decode("utf-8", errors="ignore").strip()
            if converted:
                return converted
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    normalized = resolved.replace("\\", "/")
    if len(normalized) >= 2 and normalized[1] == ":":
        drive = normalized[0].lower()
        tail = normalized[2:]
        wsl_path = f"/mnt/{drive}{tail}"
        try:
            proc = subprocess.run(
                ["bash", "-lc", f"test -e {shlex.quote(wsl_path)}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
            )
            if proc.returncode == 0:
                return wsl_path
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
        return f"/{drive}{tail}"
    return normalized


def _copy_fixtures_into(src_dir: Path, run_dir: Path) -> None:
    """把测试目录里非 sh 的辅助文件拷到 run_dir。"""
    try:
        entries = os.listdir(src_dir)
    except OSError:
        return
    for name in entries:
        if (
            name.startswith(".")
            or name.endswith(".sh")
            or name.endswith(".sh.orig")
            or name.endswith(".llm_raw.txt")
            or name.endswith(".invalid")
        ):
            continue
        full = src_dir / name
        if not full.is_file():
            continue
        try:
            shutil.copy2(full, run_dir / name)
        except OSError:
            pass

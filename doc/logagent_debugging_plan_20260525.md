# LogAgent 调试插桩实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在 Linux 测试环境里，为 C / Rust 对照测试增加可切换的运行时插桩与调试采集能力，让 test agent 能自动收集 backtrace、stack frames、locals、trace 和断点命中信息，并把压缩后的上下文喂给 LLM 用于修复与差异追踪。

**架构：** 先增加一个独立的 `LogAgent`，负责定义日志 schema、压缩运行时证据、组织动态插桩请求。随后在 `TestRunner` 中接入两类后端：默认的 LLDB 命令文件采集后端，以及可选的 DAP 后端。`RustTestAgent` 负责在失败重试时选择是否启用插桩、把采集到的日志写入每次运行目录，并把精简后的证据块注入修复 prompt。

**技术栈：** Python 3.11、`subprocess`、JSON、LLDB 命令文件、可选 `lldb-dap` / CodeLLDB、现有 `rtest` 测试编排链路。

---

## 文件边界

- 创建：`src/agent/rtest/log_agent.py`
  - 负责运行时证据模型、日志压缩、插桩请求生成、日志目录布局。
- 创建：`src/agent/rtest/debug_backends.py`
  - 负责 LLDB 命令文件生成、LLDB 执行、DAP 会话封装、统一结果解析。
- 修改：`src/agent/rtest/test_runner.py`
  - 负责在单个测试运行中挂接插桩请求、写入日志目录、回收 LLDB / DAP 输出。
- 修改：`src/agent/rtest/rust_test_agent.py`
  - 负责在失败用例和回归重跑时启用 LogAgent，并把 runtime evidence 传入修复循环。
- 修改：`src/agent/rtest/repair_prompt.py`
  - 负责把压缩后的运行时证据块拼进 LLM 修复提示词。
- 创建：`src/tests/test_log_agent.py`
  - 负责验证日志 schema、压缩逻辑、插桩请求格式。
- 创建：`src/tests/test_debug_backends.py`
  - 负责验证 LLDB / DAP 请求生成和输出解析。
- 创建：`src/tests/test_rust_test_agent_logging.py`
  - 负责验证修复循环会把 runtime evidence 注入 prompt，并把日志写入运行目录。

---

### 任务 1：定义运行时证据模型和 LogAgent 核心

**文件：**
- 创建：`src/agent/rtest/log_agent.py`
- 创建：`src/tests/test_log_agent.py`

- [ ] **步骤 1：编写失败的测试**

```python
def test_compress_runtime_bundle_prefers_structured_fields():
    bundle = RuntimeEvidenceBundle(
        case_name="case01",
        error="panic: index out of bounds",
        stderr="thread 'main' panicked\nstack backtrace:\n0: foo::run\n1: main\n",
        frames=[
            {"function": "foo::run", "file": "src/foo.rs", "line": 42},
            {"function": "main", "file": "src/main.rs", "line": 12},
        ],
        locals={"idx": 9, "len": 4},
        trace_lines=["enter foo::run", "exit foo::run"],
    )
    summary = LogAgent.compress(bundle, max_chars=1200)

    assert summary["error"] == "panic: index out of bounds"
    assert summary["frames"][0]["file"] == "src/foo.rs"
    assert summary["locals"]["idx"] == 9
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python src/tests/test_log_agent.py`

预期：失败，提示 `RuntimeEvidenceBundle` / `LogAgent` 未定义。

- [ ] **步骤 3：编写最少实现代码**

```python
@dataclass
class RuntimeEvidenceBundle:
    case_name: str
    error: str = ""
    stderr: str = ""
    frames: list[dict[str, object]] = field(default_factory=list)
    locals: dict[str, object] = field(default_factory=dict)
    trace_lines: list[str] = field(default_factory=list)


class LogAgent:
    @staticmethod
    def compress(bundle: RuntimeEvidenceBundle, max_chars: int = 4000) -> dict[str, object]:
        return {
            "case_name": bundle.case_name,
            "error": bundle.error.strip(),
            "frames": bundle.frames[:8],
            "locals": bundle.locals,
            "trace": bundle.trace_lines[:40],
        }
```

- [ ] **步骤 4：运行测试验证通过**

运行：`python src/tests/test_log_agent.py`

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add src/agent/rtest/log_agent.py src/tests/test_log_agent.py
git commit -m "feat: add runtime log agent core"
```

### 任务 2：实现 LLDB 命令文件后端

**文件：**
- 创建：`src/agent/rtest/debug_backends.py`
- 创建：`src/tests/test_debug_backends.py`

- [ ] **步骤 1：编写失败的测试**

```python
def test_lldb_script_contains_break_and_inspection_commands():
    request = InstrumentationRequest(
        breakpoints=[BreakpointSpec(file="src/main.rs", line=35)],
        collect_stack=True,
        collect_locals=True,
        watch_expressions=["x", "vec.len()"],
    )

    script = build_lldb_script(request)

    assert "breakpoint set --file src/main.rs --line 35" in script
    assert "frame variable" in script
    assert "bt" in script
    assert "watch set expression -- x" in script
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python src/tests/test_debug_backends.py`

预期：失败，提示 `InstrumentationRequest` / `build_lldb_script` 未定义。

- [ ] **步骤 3：编写最少实现代码**

```python
@dataclass
class BreakpointSpec:
    file: str
    line: int


@dataclass
class InstrumentationRequest:
    breakpoints: list[BreakpointSpec] = field(default_factory=list)
    collect_stack: bool = True
    collect_locals: bool = True
    watch_expressions: list[str] = field(default_factory=list)


def build_lldb_script(request: InstrumentationRequest) -> str:
    lines = ["settings set stop-disassembly-count 0"]
    for bp in request.breakpoints:
        lines.append(f"breakpoint set --file {bp.file} --line {bp.line}")
    lines.append("run")
    if request.collect_locals:
        lines.append("frame variable")
    if request.collect_stack:
        lines.append("bt")
    for expr in request.watch_expressions:
        lines.append(f"watch set expression -- {expr}")
    lines.append("continue")
    lines.append("quit")
    return "\n".join(lines) + "\n"
```

- [ ] **步骤 4：运行测试验证通过**

运行：`python src/tests/test_debug_backends.py`

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add src/agent/rtest/debug_backends.py src/tests/test_debug_backends.py
git commit -m "feat: add lldb instrumentation backend"
```

### 任务 3：把 DAP 作为第二个调试后端接进同一套请求模型

**文件：**
- 修改：`src/agent/rtest/debug_backends.py`
- 创建：`src/tests/test_debug_backends.py`

- [ ] **步骤 1：编写失败的测试**

```python
def test_dap_launch_payload_reuses_same_instrumentation_request():
    request = InstrumentationRequest(
        breakpoints=[BreakpointSpec(file="src/main.rs", line=35)],
        collect_stack=True,
        collect_locals=True,
    )

    payload = build_dap_launch_payload(program="/tmp/app", request=request)

    assert payload["program"] == "/tmp/app"
    assert payload["breakpoints"][0]["line"] == 35
    assert payload["collectLocals"] is True
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python src/tests/test_debug_backends.py`

预期：失败，提示 `build_dap_launch_payload` 未定义。

- [ ] **步骤 3：编写最少实现代码**

```python
def build_dap_launch_payload(program: str, request: InstrumentationRequest) -> dict[str, object]:
    return {
        "name": "cgr-runtime-debug",
        "type": "lldb-dap",
        "request": "launch",
        "program": program,
        "breakpoints": [{"file": bp.file, "line": bp.line} for bp in request.breakpoints],
        "collectStack": request.collect_stack,
        "collectLocals": request.collect_locals,
        "watchExpressions": list(request.watch_expressions),
    }
```

- [ ] **步骤 4：运行测试验证通过**

运行：`python src/tests/test_debug_backends.py`

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add src/agent/rtest/debug_backends.py src/tests/test_debug_backends.py
git commit -m "feat: add dap instrumentation payloads"
```

### 任务 4：让 TestRunner 在单次测试里写入日志并执行插桩

**文件：**
- 修改：`src/agent/rtest/test_runner.py`
- 修改：`src/agent/rtest/log_agent.py`
- 创建：`src/tests/test_rust_test_agent_logging.py`

- [ ] **步骤 1：编写失败的测试**

```python
def test_write_case_bundle_creates_runtime_json(tmp_path):
    log_dir = tmp_path / ".cgr_logs"
    bundle = {
        "case_name": "case.sh",
        "exit_code": 1,
        "error": "panic: unwrap on None",
    }

    path = LogAgent.write_case_bundle(log_dir, bundle)

    assert path.name == "runtime.json"
    assert path.exists()
    assert path.read_text(encoding="utf-8").strip().startswith("{")
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python src/tests/test_rust_test_agent_logging.py`

预期：失败，提示 `run_single` 还没有写入 `.cgr_logs/`。

- [ ] **步骤 3：编写最少实现代码**

```python
class LogAgent:
    @staticmethod
    def write_case_bundle(log_dir: Path, bundle: dict[str, object]) -> Path:
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "runtime.json"
        path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
```

- [ ] **步骤 4：运行测试验证通过**

运行：`python src/tests/test_rust_test_agent_logging.py`

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add src/agent/rtest/test_runner.py src/agent/rtest/log_agent.py src/tests/test_rust_test_agent_logging.py
git commit -m "feat: persist runtime logs for test runs"
```

### 任务 5：把 runtime evidence 注入修复 prompt，并接入 RustTestAgent 的重试循环

**文件：**
- 修改：`src/agent/rtest/rust_test_agent.py`
- 修改：`src/agent/rtest/repair_prompt.py`
- 创建：`src/tests/test_rust_test_agent_logging.py`

- [ ] **步骤 1：编写失败的测试**

```python
def test_repair_prompt_includes_runtime_evidence():
    failing_case = TestCaseResult(
        name="case.sh",
        script_path="/tmp/case.sh",
        passed=False,
        exit_code=1,
        stdout="",
        stderr="panic: unwrap on None",
    )
    prompt = build_repair_prompt(
        failing_case=failing_case,
        script_content="echo hi",
        project_structure="",
        rust_overview="",
        material=MaterialBudget(),
        history_summary="",
        source_records_index="",
        attempt=1,
        max_attempts=3,
        last_build_error="",
        flags=[],
        keywords=[],
        expected_outputs=[],
        regression_warning="",
        focused_failure="",
        test_artifact_index="",
        runtime_evidence={"error": "panic: unwrap on None", "frames": [{"file": "src/main.rs", "line": 42}]},
    )

    assert "[Runtime evidence]" in prompt
    assert "src/main.rs:42" in prompt
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python src/tests/test_rust_test_agent_logging.py`

预期：失败，提示 prompt 里还没有 runtime evidence 区块。

- [ ] **步骤 3：编写最少实现代码**

```python
def _build_runtime_evidence_block(runtime_evidence: dict[str, object] | None) -> str:
    if not runtime_evidence:
        return ""
    return "\n[Runtime evidence]\n" + json.dumps(runtime_evidence, ensure_ascii=False, indent=2)
```

- [ ] **步骤 4：运行测试验证通过**

运行：`python src/tests/test_rust_test_agent_logging.py`

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add src/agent/rtest/rust_test_agent.py src/agent/rtest/repair_prompt.py src/tests/test_rust_test_agent_logging.py
git commit -m "feat: feed runtime evidence into repair prompts"
```

---

## 约束

1. 只面向 Linux 测试环境，不保留 Windows 分支。
2. LLDB 是默认深度调试后端，DAP 是同一请求模型下的第二实现。
3. 动态插桩必须通过结构化请求驱动，不能散落成临时字符串拼接。
4. 日志必须可回放、可压缩、可供 LLM 直接读取，不能只保留终端原始输出。
5. 这一版不引入 `rr`，留给下一阶段。

## 验收标准

- 失败用例会在对应的 `.run_<case>/.cgr_logs/` 下留下结构化日志。
- LLM prompt 能拿到压缩后的 runtime evidence，而不是整段 debugger 输出。
- LLDB 和 DAP 共用同一套插桩请求模型。
- test agent 能在失败重试时按需启用插桩，而不是每次都强制开启。

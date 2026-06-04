# A4-4 LogAgent 与运行时证据调研

## 研究问题

本文调研 `LogAgent` 如何把 shell 测试运行结果转化为可进入 prompt 的结构化证据。它不是独立修复 agent，而是 rtest 修复循环中的运行证据管理层，负责压缩失败输出、保存 runtime bundle，并把最近的动态/静态 probe 结果合并到下一轮 prompt。

核心研究问题如下：

- stdout、stderr、trace、locals、frames 等异质信息如何统一为一个 JSON bundle？
- 证据如何被压缩到 prompt 可承受的大小？
- `runtime.json`、`debug_probe_*.json`、`static_probe_*.json` 如何被读回下一轮？
- LogAgent 关闭时，修复流程如何退化为普通 stdout/stderr/trace 诊断？

## 流程/数据流

运行证据写入流程如下：

```text
TestRunner.run_single()
    -> TestCaseResult
    -> LogAgent.bundle_from_result()
    -> RuntimeEvidenceBundle
    -> LogAgent.compress()
    -> .cgr_logs/runtime.json
```

运行证据读取流程如下：

```text
RustTestAgent._repair_one_round()
    -> enable_log_agent ? RuntimeProbeService.read_runtime_evidence()
    -> 读取 .cgr_logs/runtime.json
    -> 读取最近 debug_probe_*.json
    -> 读取最近 static_probe_*.json
    -> build_repair_prompt()
    -> [Runtime evidence] JSON block
```

LogAgent 的可选性也很关键：

```text
--use-log-agent = false
    -> 不写 runtime evidence prompt block
    -> 不暴露 debug_probe / static_probe_update schema
    -> LLM 返回 probe 请求也会被忽略

--use-log-agent = true
    -> 写 runtime.json
    -> prompt 暴露动态和静态 probe 能力
    -> 下一轮自动携带 probe 摘要
```

## 关键工程细节

- **证据结构标准化。** `RuntimeEvidenceBundle` 统一包含 case name、exit code、error、stdout、stderr、frames、locals、trace lines 和 metadata。
- **尾部优先压缩。** `compress()` 默认按 4000 字符预算裁剪，stdout 留四分之一，stderr 留二分之一，trace 留最后 40 行。失败原因往往在尾部，因此该策略优先保留末端证据。
- **locals 和 frames 限额。** frames 最多保留 8 层，locals 做映射级裁剪，避免调试器输出挤占源码上下文。
- **日志文件独立。** `runtime.json` 写在每个用例 `run_dir/.cgr_logs/`，与项目源码分离，便于在修复循环内保留运行证据。
- **probe 摘要合并。** `RuntimeProbeService.read_runtime_evidence()` 读取最近 4 个 `debug_probe_*.json` 和最新 `static_probe_*.json`，避免 prompt 被历史 probe 淹没。
- **首跑 trace 懒加载影响。** 首次 `run_all()` 不捕获 trace，因此第一次 runtime evidence 可能只有 stdout/stderr。修复阶段重跑失败用例并 `capture_trace=True` 后，runtime evidence 才会携带 trace。
- **异常容忍。** 读取 `runtime.json` 或 probe JSON 失败时回退为空 dict，不阻断修复循环。
- **prompt 条件化。** 只有 LogAgent 开启时，`repair_prompt.py` 才写入 `[Runtime evidence]`、active static probes 和 probe JSON schema。
- **非中文化输出保留。** 日志 JSON 使用 `ensure_ascii=False`，可以保留原始错误文本中的非 ASCII 内容，避免信息损失。

## 可引用代码证据

- `src/agent/rtest/log_agent.py:14`：`RuntimeEvidenceBundle` 定义运行证据字段。
- `src/agent/rtest/log_agent.py:34`：`LogAgent` 定位为运行证据压缩和归一化 helper。
- `src/agent/rtest/log_agent.py:38`：`bundle_from_result()` 从 `TestCaseResult` 构造 bundle。
- `src/agent/rtest/log_agent.py:55`：`compress()` 将 bundle 裁剪为 JSON 摘要。
- `src/agent/rtest/log_agent.py:71`：`write_case_bundle()` 固定写入 `runtime.json`。
- `src/agent/rtest/log_agent.py:75`：`write_named_bundle()` 是 runtime 和 probe JSON 的统一写文件入口。
- `src/agent/rtest/test_runner.py:279`：`TestRunner.write_runtime_log()` 将测试结果写成 LogAgent bundle。
- `src/agent/rtest/test_runner.py:227`：失败且要求 trace 时才捕获 `bash -x`。
- `src/agent/rtest/test_runner.py:240`：首次 `run_all()` 调用 `run_single(capture_trace=False)`。
- `src/agent/rtest/rust_test_agent.py:150`：主流程打印 LogAgent 开关状态。
- `src/agent/rtest/rust_test_agent.py:1173`：修复轮根据 `enable_log_agent` 读取 runtime evidence。
- `src/agent/rtest/runtime_probe.py:39`：`read_runtime_evidence()` 从 run dir 读取证据。
- `src/agent/rtest/runtime_probe.py:41`：读取 `.cgr_logs/runtime.json`。
- `src/agent/rtest/runtime_probe.py:56`：收集 `debug_probe_*.json`。
- `src/agent/rtest/runtime_probe.py:75`：收集 `static_probe_*.json`。
- `src/agent/rtest/repair_prompt.py:791`：`_build_runtime_evidence_block()` 生成 `[Runtime evidence]` prompt 块。
- `src/agent/rtest/repair_prompt.py:800`：`_build_instrumentation_context()` 只在开启时写入 static probes 上下文。
- `src/agent/rtest/repair_prompt.py:824`：`_build_instrumentation_json_schema()` 条件化暴露 probe schema。
- `src/agent/rtest/rust_test_agent.py:2579`：CLI 暴露 `--use-log-agent`。
- `src/agent/rtest/rust_test_agent.py:2620`：CLI 将 `--use-log-agent` 传入 `RustTestAgent(enable_log_agent=...)`。

## 实验钩子

- **证据大小统计。** 记录 runtime evidence 原始 stdout/stderr/trace 长度与压缩后 JSON 字符数。
- **LogAgent 消融。** 比较 `--use-log-agent` 开关对修复成功率、平均轮数、probe 请求频次的影响。
- **trace 可用性。** 区分首次失败 runtime evidence 无 trace、修复重跑后有 trace 两种情况，观察对定位效果的影响。
- **JSON 容错。** 人为破坏 `runtime.json` 或 probe JSON，验证系统是否回退为空证据而不中断。
- **历史 probe 截断。** 调整最近 4 个 debug probe 的读取窗口，观察 prompt 长度与修复收益的平衡。
- **字段消融。** 分别去掉 stderr tail、trace、locals、frames，观察模型诊断准确率变化。

## 局限与反例

- LogAgent 默认保留尾部信息，若关键错误只出现在输出开头，可能被裁剪掉。
- 首次全量测试为了性能不抓 trace，第一次 prompt 可能需要额外懒加载 trace 才足够定位。
- `runtime.json` 是单用例运行目录内的证据，如果 run dir 被 cleanup 删除，后续无法恢复。
- locals 和 frames 的质量依赖动态 probe 后端；普通 `runtime.json` 不会自动包含调试器局部变量。
- 证据压缩关注 prompt 可读性，不适合做完整审计日志；完整 stdout/stderr 需要从运行目录或外部日志另行保存。

## 可写入论文位置

- **方法章节：运行证据结构化。** 说明如何把黑盒 shell 失败转化为 LLM 可消费的 JSON evidence。
- **工程优化章节：prompt 预算控制。** 介绍 tail clipping、frame/locals 限额和 probe 历史窗口。
- **实验章节：LogAgent 消融。** 将是否启用运行时证据作为重要消融变量，报告修复轮数和成功率变化。

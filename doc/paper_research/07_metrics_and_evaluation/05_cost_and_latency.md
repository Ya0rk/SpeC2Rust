# 成本与延迟指标

## 研究问题

本文件回答「系统为了获得翻译成功付出了多少成本」的问题。C 到 Rust 闭环翻译的成本来自 LLM 请求、prompt token、模型延迟、`cargo check/build`、C build、shell 测试、trace 捕获、snapshot 和 probe。论文需要同时报告端到端耗时和阶段级开销，否则无法解释 repair/test 成功率是否值得。

核心研究问题包括：

- 如何从现有日志统计 LLM 请求数、token 和单轮耗时？
- 如何估计编译修复和功能测试修复的非 LLM 开销？
- 预算上限如何影响成功率、耗时和失败类型？
- 哪些指标适合主文，哪些适合作为附录或工程分析？

## 指标定义或数据流

成本数据流如下：

```text
Model.generate()
    -> translation_metrics.increment_llm_requests()
    -> RoundLogger.write_round(duration, token_usage, call_stack)

main.py finally
    -> translation_metrics.save_to(<rust_project>/translation_metrics.json)

RustRepairAgent
    -> repair_journal.jsonl:
       llm_repair / post_check / round_timeout / iteration_result

RustTestAgent + TestRunner
    -> rtest log:
       per-case duration / timeout / repair attempt / rebuild / regression
    -> optional .cgr_logs/runtime.json / debug_probe_*.json / static_probe_*.json
```

建议定义以下成本指标：

| 指标 | 定义 | 数据源 |
| --- | --- | --- |
| `elapsed_seconds` | 端到端 wall-clock 秒数，整数 | `translation_metrics.json` |
| `llm_request_count` | 通过统一 `Model.generate()` 的请求次数 | `translation_metrics.json` |
| `round_duration_seconds` | 单次 LLM 调用耗时 | round log `Duration` |
| `request_tokens` / `reply_tokens` | 单轮 prompt 和 completion token | round log token 字段 |
| `total_tokens` | 所有 round token 求和 | round logs |
| `repair_llm_rounds` | `llm_repair` stage 次数 | `repair_journal.jsonl` |
| `cargo_check_count` | `post_check` 次数加初始 check | `repair_journal.jsonl` |
| `round_timeout_count` | repair 轮内 timeout 次数 | `repair_journal.jsonl` |
| `test_case_duration_seconds` | 单用例 shell 测试耗时 | `TestCaseResult.duration_seconds`、rtest 日志 |
| `test_timeout_count` | 超时用例次数 | rtest 日志、timeout artifacts |
| `probe_count` | debug/static probe 请求和执行次数 | rtest 日志、`.cgr_logs/` |
| `success_per_100_requests` | `100 * successful_projects / total_llm_request_count` | 聚合结果 |
| `case_lift_per_request` | 新增通过测试用例数 / LLM 请求数 | 聚合结果 |

本地历史样例显示成本跨度较大，仅可作为日志可用性说明，不应作为最终论文结果。例如 `output_deepseek/cat/cat-rust/translation_metrics.json` 记录约 9351 秒和 355 次 LLM 请求；`output_deepseek/shc/shc-rust/translation_metrics.json` 记录约 2126 秒和 51 次 LLM 请求。

## 关键工程细节

- **LLM 请求数由 wrapper 统一计数。** `Model.generate()` 在调用底层模型前递增计数，因此失败或异常调用也会进入 `llm_request_count`。
- **宏观指标很粗。** `TranslationMetrics` 只记录开始、结束、整数秒耗时和总请求数，不区分 agent、阶段、token 或失败重试。
- **round log 可做阶段拆分。** 每个 round log 记录 objective、model、backend、duration、token、finish reason、request options 和 call stack，可按 objective 前缀或调用栈聚合到 Spec、Rust 生成、repair 和 rtest。
- **token 可能是实际值，也可能是估计值。** `RoundLogger` 优先使用后端 `last_usage`，缺失时用启发式 token 估算。论文中应标注 actual 与 estimated。
- **非 LLM 开销需要间接估计。** 当前 repair journal 没有每次 `cargo check` 的 duration 字段，rtest 日志有 per-case duration。编译和 build 时间可通过外部批处理复跑或增强日志补齐。
- **默认预算影响尾延迟。** `agent.sh` 默认 repair/test 各 64 轮；rtest 默认 prompt budget 为 256000 字符。大预算能提高修复机会，也会放大失败项目的耗时。
- **测试超时是成本上限。** `TestRunner` 默认按 timeout 杀进程组，超时用例会消耗接近完整 timeout，并额外捕获短 trace。
- **probe 成本不可忽略。** 动态 probe 依赖 LLDB，静态 probe 会复制项目副本、插桩、重新 build 并运行 binary，对大型项目开销明显。

## 可引用代码证据

| 论点 | 代码证据 |
| --- | --- |
| `TranslationMetrics` 用 monotonic time 记录整数秒耗时 | `src/utils/translation_metrics.py:23-64` |
| `translation_metrics.json` 写入目标路径 | `src/utils/translation_metrics.py:67-73` |
| 主流程结束时保存并打印耗时和 LLM 请求数 | `src/agent/main.py:785-793` |
| `Model.generate()` 递增请求数并记录调用耗时 | `src/llm/model.py:28-46` |
| `Model` 把后端 `last_usage` 传给 round logger | `src/llm/model.py:88-100` |
| `RoundLogger` 支持 `CGR_ROUND_LOG_DIR`、`CGR_ROUND_LOG_PROJECT` 和 `CGR_ROUND_LOG_RUN` | `src/utils/round_logger.py:31-40` |
| `RoundLogger` token 估算和 token 字段输出 | `src/utils/round_logger.py:110-159` |
| `RoundLogger` 记录单轮 duration | `src/utils/round_logger.py:237-252` |
| 默认 repair/test 预算和 rtest prompt budget | `scripts/agent.sh:167-169` |
| rtest 旁路暴露 max repair iterations 和 prompt budget | `scripts/rtest_agent.sh:172-173` |
| `RustRepairAgent` 命令 timeout：`cargo check`、`cargo test`、release build | `src/agent/rust_repair_agent.py:170-222` |
| repair 轮内 timeout 记录 | `src/agent/rust_repair_agent.py:3903-3919` |
| rtest 单用例 duration 记录 | `src/agent/rtest/test_runner.py:160-231` |
| rtest timeout 进程组终止 | `src/agent/rtest/test_runner.py:574-622` |
| rtest 超时 trace 使用较短 timeout | `src/agent/rtest/test_runner.py:222-226`、`src/agent/rtest/test_runner.py:534-535` |

## 实验钩子

- **阶段级 token 聚合。** 按 round log objective 前缀统计 Spec、ContextualRust、RustRepair、RustTest 的请求数、request tokens、reply tokens 和 duration。
- **预算曲线。** 对 repair/test 预算运行 0/8/16/32/64，绘制成功率、平均耗时、P95 耗时和 LLM 请求数。
- **失败项目尾延迟。** 单独报告未成功项目的平均请求数和耗时，判断是否需要早停策略。
- **编译开销补齐。** 在批处理脚本中包装 `cargo check`、`cargo build --release` 和 C build，写入 duration，补足当前 journal 缺口。
- **rtest 开销拆分。** 统计首次 `run_all()`、每次 rebuild、每个 case `run_single()`、回归检查和 trace 捕获耗时。
- **LogAgent/probe 成本。** 开启和关闭 LogAgent，比较 probe 次数、测试修复轮数、总耗时和成功率。
- **效率指标。** 报告每 100 次 LLM 请求的成功项目数、每新增通过用例的请求数、每清除一个编译错误前沿的请求数。
- **质量成本关系。** 对通过项目比较 `unsafe_rate`、裸指针计数和 LLM 请求数，观察高质量 Rust 是否需要更多修复成本。

## 局限与反例

- `translation_metrics.json` 不含阶段拆分，也不含 token 总量。严格成本需要 round logs。
- round log token 不是总能拿到模型真实 usage；估算值不能用于精确计费。
- 模型价格随时间和供应商变化，论文若报告货币成本，应在实验当天引用官方价格并记录日期。
- 有些底层调用如果绕过 `Model.generate()`，不会进入统一 LLM 请求计数。
- shell log 和 rtest log 是文本格式，解析 per-case 和 stage duration 容易受输出格式变化影响。
- 当前 repair journal 没有记录每个 cargo 命令的耗时，只能统计次数和 timeout，或通过复跑补测。
- 历史输出样例的成本受模型、网络、后端限速和代码版本影响，不能直接横向比较。

## 可写入论文位置

- **实验设置：成本口径。** 定义 wall-clock、LLM 请求数、token、编译次数、测试次数和 timeout。
- **结果章节：成本收益。** 报告成功率随请求数和预算增长的曲线。
- **系统分析：可观测性。** 说明 round logs 和 translation metrics 如何支持阶段级成本审计。
- **威胁与局限：计费与复现。** 说明 token 估算、模型价格变化和非 LLM 工具开销的限制。

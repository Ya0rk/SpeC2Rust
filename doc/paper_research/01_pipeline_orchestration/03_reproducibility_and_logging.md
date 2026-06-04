# A0-03：可复现性与日志调研

日期：2026-06-04  
责任范围：`src/utils/round_logger.py`、`src/utils/translation_metrics.py`、`src/llm/model.py`、`src/config/config.py`，关联入口脚本日志

## 研究问题

本文件回答论文中「如何证明一次翻译运行可以被审计、复盘和统计」的问题。可复现性不只来自固定命令，还来自每一层日志：入口日志记录端到端 stdout/stderr，round log 记录每轮 LLM 请求与响应，translation metrics 记录总耗时和请求轮数，下游修复 agent 还会产生更细粒度过程日志。

核心研究问题包括：

- 一次运行有哪些持久化产物可以支撑论文实验复盘？
- LLM 调用是否在统一底层被记录，而不是依赖每个 agent 手动打点？
- 当前指标能否支持成本分析、消融统计和失败归因？
- 日志体系有哪些隐私、结构化程度和 run manifest 方面的不足？

## 流程 / 数据流

### 日志层次

```text
入口 shell
  -> log/agent-<project>-<timestamp>.log
  -> log/rtest-<project-or-custom>-<timestamp>.log

main.py
  -> translation_metrics.start()
  -> 所有 Model.generate 调用增加 LLM 请求计数
  -> finally: translation_metrics.finish()
  -> <rust_project_path>/translation_metrics.json

Model.generate
  -> capture call stack
  -> self.llm.get_response(prompt)
  -> finally: RoundLogger.log_round(...)

RoundLogger
  -> log/round_logs/<run-name>/<round>-<objective>.md
  -> 每个 Markdown 包含 timestamp、objective、model、backend、duration、token、call stack、request、reply、error
```

这一设计的关键点是：LLM 请求日志在 `Model.generate` 最底层统一记录。只要 agent 使用 `Model` 包装器，就能进入统一计数和 round log 体系，而不用每个 agent 自己实现日志格式。

### 配置数据流

```text
local_config.json
  -> Config(config_path=...)
  -> model_name / api_model / api_base_url / api_max_tokens / retry / stream
  -> round_log_enabled / round_log_dir / round_log_project_name
  -> Model(config)
  -> CustomApiGen 或 OpenAiGen 或 QwenLocalGen
  -> RoundLogger(base_dir=config.round_log_dir, project_name=config.round_log_project_name)
```

`main.py` 会把 `config.round_log_project_name` 设置为 C 项目目录名或 Rust 项目名，保证 round log 目录名包含项目语义。`RoundLogger` 也支持环境变量覆盖：`CGR_ROUND_LOG_DIR`、`CGR_ROUND_LOG_PROJECT` 和 `CGR_ROUND_LOG_RUN`。

### 指标数据流

```text
translation_metrics.start()
  -> 记录 wall clock start 和 monotonic start

Model.generate(...)
  -> translation_metrics.increment_llm_requests()

translation_metrics.finish()
  -> 记录 wall clock end 和 monotonic end

translation_metrics.save_to(...)
  -> JSON:
     started_at
     ended_at
     elapsed_seconds
     llm_request_count
```

目前全局指标足以支撑「总耗时」和「总 LLM 请求轮数」统计；若论文需要阶段级成本，需要从 round logs、agent 专属日志或后续新增结构化事件中补充。

## 关键工程细节

- **统一底层计数。** `Model.generate` 每次调用先执行 `translation_metrics.increment_llm_requests()`，再调用具体后端。无论使用 Qwen、本地兼容 API 还是 OpenAI 包装器，计数路径一致。
- **失败也会写 round log。** `Model.generate` 在 `finally` 中调用 `_safe_log_round`。如果后端抛异常，日志中会保留 error 字段；如果没有返回，reply 为空。
- **调用栈可审计。** `Model._capture_generate_stack` 把调用栈转换为相对仓库路径、行号、函数名和当前代码上下文，round log 中可以定位是哪一个 agent、哪一个函数发起请求。
- **round log 可配置但默认开启。** `Config.round_log_enabled` 默认是 `True`；`RoundLogger` 默认写入 `log/round_logs`，也支持 `round_log_dir` 或 `CGR_ROUND_LOG_DIR`。
- **run name 自动包含时间和项目名。** 未显式指定 run name 时，`RoundLogger` 用模块加载时的时间戳和项目名生成目录，便于按项目归档。
- **token 使用优先真实值，缺失时估算。** `RoundLogger` 如果拿到后端 `last_usage`，会写 prompt、completion、total token；否则用中文字符数和 ASCII token 正则做估算。
- **请求选项进入日志。** 当后端提供 `request_options`，round log 会记录 `api_model`、`stream`、`max_tokens`、thinking 和 payload keys 等字段，方便解释不同 API 配置下的行为差异。
- **流式诊断可落盘。** round log 支持记录 stream finish reasons、事件数、内容 chunk 数、reasoning chunk 数和可见内容为空等诊断信息。
- **指标线程安全。** `TranslationMetrics` 使用 lock 保护 start、finish、increment 和 snapshot；`RoundLogger` 使用全局 lock 与全局 counter 保证同一进程内轮次编号单调递增。
- **入口日志保留完整 stdout/stderr。** `agent.sh` 和 `rtest_agent.sh` 使用 `2>&1 | tee`，保留控制台输出、banner、阶段进度和异常信息。

## 可引用代码证据

| 论点 | 代码证据 |
| --- | --- |
| `Config` 默认启用 round log，并加载 API、重试、stream 和 round log 配置 | `src/config/config.py:1-98` |
| `Model` 初始化时创建 `RoundLogger` | `src/llm/model.py:16-25` |
| 每次 LLM 请求统一增加计数 | `src/llm/model.py:27-35` |
| LLM 异常也进入 `_safe_log_round` | `src/llm/model.py:36-47` |
| request label 可作为 objective | `src/llm/model.py:49-67` |
| 调用栈记录相对路径、行号、函数和代码上下文 | `src/llm/model.py:69-86` |
| round log 写入 model、backend、call stack、error、duration 和 token usage | `src/llm/model.py:88-103` |
| 模型后端选择和 API 参数传递 | `src/llm/model.py:105-135` |
| `RoundLogger` 默认目录与环境变量覆盖 | `src/utils/round_logger.py:24-43` |
| round log 文件名清洗，兼容中文项目名 | `src/utils/round_logger.py:45-48` |
| token 估算与真实 usage 合并 | `src/utils/round_logger.py:109-165` |
| stream diagnostics 与 request options 写入 | `src/utils/round_logger.py:167-208` |
| Markdown payload 包含元信息、调用栈、错误、请求和回复 | `src/utils/round_logger.py:226-285` |
| 全局递增轮次并写入单轮 Markdown | `src/utils/round_logger.py:298-333` |
| `TranslationMetrics` 记录起止时间和 LLM 请求数 | `src/utils/translation_metrics.py:9-65` |
| `translation_metrics.json` 持久化 | `src/utils/translation_metrics.py:67-76` |
| `main.py` 在 finally 中保存指标 | `src/agent/main.py:781-796` |
| 主入口写 `log/agent-*.log` | `scripts/agent.sh:51-52`、`scripts/agent.sh:216-217` |
| RustTestAgent 旁路写 `log/rtest-*.log` | `scripts/rtest_agent.sh:69-71`、`scripts/rtest_agent.sh:216-217` |

## 实验钩子

| 实验问题 | 控制方式 | 观测方式 |
| --- | --- | --- |
| 每个配置的总成本是多少？ | 固定项目和模型，改变 flags | `translation_metrics.json` 中的 `elapsed_seconds`、`llm_request_count` |
| 不同模型或 API 参数是否影响生成失败？ | 修改 `local_config.json` 中 `model_name`、`api_model`、`api_max_tokens`、`api_stream` | round logs 的 backend、request options、finish reason、error |
| prompt budget 是否影响测试修复？ | `--rust-test-agent-prompt-budget-chars` 或 `CGR_RUST_TEST_PROMPT_BUDGET_CHARS` | round logs 的 request tokens、rtest 日志的通过率 |
| LogAgent 是否增加证据质量但提高成本？ | `--use-log-agent` 或 `CGR_USE_LOG_AGENT=1` | round log 数量、LLM 请求数、功能测试修复结果 |
| 错误分批是否降低单轮 prompt 体积？ | `--use-error-organizer-agent --error-batch-size N` | round logs 的 request tokens、编译修复轮数 |
| API 流式输出是否稳定？ | `api_stream=true/false` | stream diagnostics、finish reasons、空内容诊断 |
| round log 目录是否可按实验 run 隔离？ | 设置 `CGR_ROUND_LOG_DIR`、`CGR_ROUND_LOG_RUN` | 指定目录下的单轮 Markdown |

建议后续新增一个结构化 run manifest，至少包含：

| 字段 | 来源 |
| --- | --- |
| git commit | `git rev-parse HEAD` |
| 命令行和环境变量 | 入口脚本 |
| `local_config.json` 摘要 | `Config` |
| 数据集路径和 hash | `datasets/<project>` |
| 输出路径和是否清理旧产物 | 入口脚本 |
| round log 目录 | `RoundLogger.run_dir` |
| 主日志路径 | `agent.sh` 或 `rtest_agent.sh` |
| 关键阶段成功状态 | `main.py`、修复 agent、测试 agent |

## 局限与反例

- **总指标太粗。** `translation_metrics.json` 只有总耗时和总请求数，不能直接回答「SpecAgent 用了多少请求」「RustRepairAgent 用了多少 token」「RustTestAgent 花了多少时间」。
- **round logs 是 Markdown，不是结构化事件表。** 它们便于人工审查 prompt 和回复，但批量统计需要额外解析。
- **入口日志和 round log 的 run ID 没有强绑定。** `agent.sh` 的日志时间戳来自 shell 启动时，`RoundLogger` 的默认时间戳来自 Python 模块加载时。两者通常接近，但不是同一个显式 run ID。
- **没有自动记录 git commit 和配置快照。** 论文复现实验仍需外部记录代码版本、`local_config.json`、环境变量和数据集版本。
- **日志可能包含敏感信息或专有代码。** round logs 会保存完整 request 和 reply，如果 prompt 中含源码、API 错误或业务数据，归档和公开时需要脱敏。
- **`Config` 的布尔转换对字符串不安全。** JSON 布尔值 `false` 可以正确转成 `False`，但如果配置文件错误写成字符串 `"false"`，`bool("false")` 会得到 `True`。实验配置应使用 JSON 原生布尔值。
- **round log 写失败不会中断主流程。** `_safe_log_round` 捕获日志异常并只打印错误，这保证翻译不中断，但也意味着日志缺失可能不被测试自动发现。
- **同一输出目录多次运行可能覆盖指标。** `translation_metrics.json` 写到 Rust 项目目录，多次运行同一输出目录会覆盖旧指标；主日志和 round logs 有时间戳，指标文件没有历史版本。
- **直接入口的日志粒度不一致。** `agent.sh`、`rtest_agent.sh` 和 `run_repair.sh` 对 Python 环境、`PYTHONPATH` 和日志命名的处理不完全一致，跨入口比较时要记录入口类型。

## 可写入论文位置

- **实验设置：可复现性声明。** 描述主日志、round logs、`translation_metrics.json` 和下游修复日志构成的审计链。
- **方法章节：LLM 调用审计。** 强调底层 `Model.generate` 统一记录请求、响应、耗时、token 和调用栈，而不是依赖 agent 自己打点。
- **实验结果：成本指标。** 目前可以报告总耗时和 LLM 请求轮数；阶段级 token 成本需要基于 round logs 解析或新增结构化统计。
- **消融实验：证据增强成本。** 用 LogAgent、ErrorOrganizerAgent、prompt budget 和 API stream 作为变量，结合 round logs 解释成本和成功率变化。
- **威胁有效性：复现限制。** 写明当前缺少 run manifest、配置快照、数据集 hash 和阶段级指标，作为后续工程优化方向。

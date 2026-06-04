# LLM 轮次日志与翻译指标

## 研究问题

本节研究项目如何记录 LLM 调用、请求标签、token 使用、调用栈和整体运行指标。论文需要的不只是最终成功率，还需要回答：

- 每个阶段消耗了多少轮 LLM 请求？
- 哪些请求因为上下文不足触发 `<CGR_READ>` 或 continuation？
- 出错时能否回溯到具体 agent、函数和 prompt？
- 不同 agent 变体的耗时和请求轮数是否可比较？

## 流程 / 数据流

日志分为三层：

1. `scripts/agent.sh` 使用 `tee` 把整次终端输出写入 `log/agent-<project>-<timestamp>.log`。
2. `Model.generate()` 每次调用底层模型时递增 `translation_metrics`，并在启用 round log 时写入一份 Markdown。
3. `main.py` 在流程开始调用 `translation_metrics.start()`，在 `finally` 中保存 `translation_metrics.json`。

Round log 的 request label 来自各 agent，例如 `ContextualRustAgent 代码生成 src/foo.rs [round 1]`、`ContextualRustAgent 项目结构设计 [round 2]`、`测试修复 case #attempt`。这使单轮 prompt 能归属到具体阶段。

## 关键工程细节

`RoundLogger` 是一个独立于 agent 的日志器，由 `Model` 包装层统一调用。它写出的 Markdown 包括：

- timestamp、objective、model、backend、duration。
- request / reply token，优先使用后端返回的 `last_usage`，缺失时做估算。
- finish reason、stream diagnostics、request options。
- 调用栈，包括文件、行号、函数名和代码上下文。
- request 和 reply 原文。
- reasoning content，如果后端把 reasoning 写入 token usage。

日志目录支持环境变量：

- `CGR_ROUND_LOG_DIR`：覆盖默认 `log/round_logs`。
- `CGR_ROUND_LOG_PROJECT`：参与 run name。
- `CGR_ROUND_LOG_RUN`：显式指定 run name。

全局轮次计数使用 `threading.Lock` 和 `itertools.count`，可避免并发写日志时文件名冲突。token 估算对 CJK 字符单独计数，再用正则切 ASCII token，是一个可解释但非 tokenizer 级别的近似。

`TranslationMetrics` 当前只保存宏观指标：

- `started_at`
- `ended_at`
- `elapsed_seconds`
- `llm_request_count`

它的优势是稳定、低成本；不足是缺少按阶段拆分、token 总量、失败重试和 read-request 统计。

## 可引用代码证据

- `scripts/agent.sh:52`：构造整次运行的 shell log 文件名。
- `scripts/agent.sh:216-223`：Python 主流程输出通过 `tee` 写入 shell log。
- `src/llm/model.py:16-28`：`Model` 初始化 `RoundLogger`，每次 `generate()` 递增 LLM 请求计数。
- `src/llm/model.py:40-47`：每次模型请求结束后写 round log。
- `src/llm/model.py:49-67`：request label 作为 objective，否则使用首条 prompt 文本推断 objective。
- `src/llm/model.py:69-101`：捕获调用栈，并写入模型名、后端名、耗时和 token usage。
- `src/utils/round_logger.py:16-43`：日志目录、run name 和环境变量策略。
- `src/utils/round_logger.py:110-165`：token 使用记录与估算逻辑。
- `src/utils/round_logger.py:226-285`：Markdown payload 结构。
- `src/utils/round_logger.py:298-333`：用全局 counter 生成 `000001-objective.md` 文件。
- `src/utils/translation_metrics.py:23-44`：开始、结束和 LLM 请求计数。
- `src/utils/translation_metrics.py:46-73`：生成并保存 `translation_metrics.json`。
- `src/agent/main.py:432`：主流程开始记录翻译指标。
- `src/agent/main.py:782-795`：主流程结束时保存并打印耗时与 LLM 请求轮数。

## 实验钩子

- **按阶段聚合 round log：** 根据 objective 前缀统计 Spec 生成、Rust 生成、repair、test repair 的请求轮数。
- **token 分布：** 从 round log 中提取 Request Tokens / Reply Tokens，比较 `RustAgent` 与 `ContextualRustAgent` 的 prompt 压缩效果。
- **continuation 统计：** objective 中包含 `[continuation N]` 或 `[round N]` 的请求可用于统计长文件截断率。
- **read-request 效果：** 查找 `<CGR_READ>` round 后下一轮是否成功生成，评估按需材料机制。
- **失败回溯：** 使用 call stack 聚合哪个 agent 函数触发最多错误或重试。

## 局限与反例

- `translation_metrics.json` 只记录总请求数，不区分 agent、阶段、成功 / 失败、prompt / completion token。
- token 估算不是模型 tokenizer，不能作为严格成本指标。
- Round log 默认记录完整 prompt 和 reply，可能包含大量源码或敏感项目内容，论文实验需要脱敏或只保留统计。
- 如果某些底层模型调用绕过 `Model.generate()`，则不会进入统一指标计数。
- request label 依赖 agent 主动设置；未设置时 objective 由 prompt 首行推断，可读性不稳定。

## 可写入论文位置

建议放入「实验设置」和「可观测性」小节。论文中可以把 round log 作为分析上下文预算、LLM 调用成本和失败诊断的基础设施，而不是核心算法贡献。


# A8-03：输出契约与解析器调研

日期：2026-06-04
责任范围：`src/agent/spec_agent.py`、`src/agent/alternatives/contextual_rust_agent.py`、`src/agent/rust_repair_agent.py`、`src/agent/rtest/*.py`、`src/llm/custom_api.py`、`src/utils/round_logger.py`

## 研究问题

本文件研究系统如何把 LLM 输出从自由文本约束为可解析、可审计、可反馈的协议。C 到 Rust 翻译流程中，LLM 输出既可能是 Markdown 文档、Rust 文件、读取请求、诊断计划、结构化编辑，也可能是测试修复动作或 runtime probe 请求。不同阶段的输出契约越明确，下游程序越能区分「需要更多证据」「可以执行编辑」「协议失败但流程继续」。

核心研究问题包括：

- 长 Markdown 和长代码输出如何用 `<CGR_DONE>` 处理截断和续写？
- Rust 生成阶段如何用 `<CGR_READ>` 把「上下文不足」表达成机器可物化的请求？
- RustRepairAgent 如何把编译修复拆成「诊断 JSON」和「编辑 JSON」两阶段？
- RustTestAgent 如何解析功能修复 JSON，并在解析失败时给模型可操作反馈？
- 当前哪些 contract 是强校验，哪些只是 prompt 约束或空实现？

## 流程 / 数据流

### SpecAgent Markdown contract

```text
LLM markdown response
  -> 可选 <CGR_DONE>
  -> _extract_done_marker()
  -> 未完成则继续追加 continuation prompt
  -> _postprocess_generated_markdown()
  -> 写入 c_docs
```

`SpecAgent` 要求模型只输出 Markdown body 和可选 `<CGR_DONE>`，不能输出解释。若模型未给完成标记，会在最多 4 轮内续写。

### ContextualRustAgent generation contract

```text
LLM file response
  -> 可能返回 <CGR_READ>...</CGR_READ>
  -> _parse_read_requests()
  -> _materialize_read_requests()
  -> rust_read_materials_followup()
  -> 再次生成代码
  -> _extract_done_marker()
  -> _extract_generated_content()
  -> 未完成则 continuation
  -> lint / repair / force-write decision
```

`<CGR_READ>` 的 payload 支持 JSON、Python literal 和行式 `kind: query`。读取种类包括 spec、source、rust、registry 和 plan。代码生成同样依赖 `<CGR_DONE>` 判断是否需要续写。

### RustRepairAgent two-phase JSON contract

```text
cargo check errors
  -> _build_diagnosis_prompt()
  -> LLM JSON diagnosis plan
       target_files
       read_requests
       search_requests
       edit_strategy
  -> _materialize_read_requests() / _materialize_search_requests()
  -> _build_edit_prompt()
  -> LLM JSON structured edits
       edits
       more_read_requests
       search_requests
       complete
       updated_summary
  -> _apply_structured_edits_with_audit()
  -> cargo check
```

这个 contract 的关键是先诊断后编辑。诊断阶段不允许直接输出代码；编辑阶段只能使用允许的 edit modes，例如 `replace_range`、`insert_after`、`copy_range_after`、`copy_c_string_array_after`、`create_file` 和 `create_dir`。

### RustTestAgent repair JSON contract

```text
test failure prompt
  -> LLM JSON response
       cgr_read
       rust_read_requests
       test_artifact_read
       edits
       material_keep
       history_control
       debug_probe / static_probe_update
       complete
       updated_summary
  -> RepairAdapter.extract_json_payload()
  -> RepairResponseContract.parse_failure() on parse error
  -> validate_payload()
  -> material / probe / edit executor
```

RTest 复用 `RustRepairAgent` 的 JSON 提取和结构化编辑执行能力，通过 `RepairAdapter` 封装下划线方法，减少直接耦合。解析失败不会让修复流程中止，而是把协议失败摘要写入 `history_summary`，要求下一轮返回 raw JSON object。

## 关键工程细节

- **`<CGR_DONE>` 解决长输出截断。** `SpecAgent` 和 `ContextualRustAgent` 都不把一次 LLM 响应视作必然完整，而是显式要求完成标记。
- **读取请求是输出协议的一部分。** `ContextualRustAgent` 允许模型在生成前用 `<CGR_READ>` 请求更多材料，而不是在低上下文下猜测。
- **JSON 提取有容错。** `RustRepairAgent._extract_json_payload()` 先剥离代码块，再尝试整段 JSON、对象子串和数组子串，降低 Markdown fence 或额外文本导致的失败。
- **诊断 JSON 失败时不 fallback patch。** 如果诊断计划不可解析，RustRepairAgent 返回一个只读目标文件的 fallback plan，明确「do not perform fallback patching」。
- **结构化编辑 JSON 有默认字段补齐。** RustRepairAgent 对成功解析的 dict 补齐 `edits`、`more_read_requests`、`search_requests`、`complete` 和 `updated_summary`，下游 executor 可统一处理。
- **RTest 协议失败是可恢复状态。** `RepairResponseContract.parse_failure()` 会读取 `finish_reason` 和 stream diagnostics；如果是 length 截断，会明确要求不要继续原文本，而是重新返回完整 JSON。
- **Probe schema 只在 LogAgent 开启时暴露。** RTest prompt 动态加入 `debug_probe` 和 `static_probe_update` schema，并要求 probe 是 evidence-gathering round。
- **API 层也记录 request contract。** `CustomApiGen` 把 `stream_options`、`thinking`、`max_tokens`、sanitized surrogate 数量等写入 `last_usage.request_options`，最终进入 round log。

## 可引用代码证据

| 论点 | 代码证据 |
| --- | --- |
| SpecAgent 用 `<CGR_DONE>` 解析完成标记 | `src/agent/spec_agent.py:97-103` |
| SpecAgent 长 Markdown 续写要求和最大轮数 | `src/agent/spec_agent.py:188-238` |
| ContextualRustAgent 解析 `<CGR_READ>` 并按需 follow-up | `src/agent/alternatives/contextual_rust_agent.py:1191-1216` |
| `<CGR_READ>` parser 支持 JSON、literal 和行式语法 | `src/agent/alternatives/contextual_rust_agent.py:1218-1255` |
| ContextualRustAgent 文件输出用 `<CGR_DONE>` continuation | `src/agent/alternatives/contextual_rust_agent.py:1975-2031` |
| ContextualRustAgent 写入前 lint、repair、force-write decision | `src/agent/alternatives/contextual_rust_agent.py:2069-2108` |
| RustRepairAgent JSON 提取器容忍代码块和额外文本 | `src/agent/rust_repair_agent.py:1489-1531` |
| RustRepairAgent 诊断 prompt 明确「Do not output code yet」 | `src/agent/rust_repair_agent.py:1535-1685` |
| 诊断 JSON 解析失败时只读取错误文件，不生成 fallback edits | `src/agent/rust_repair_agent.py:1689-1725` |
| 修复工具协议列出 read、search 和 edit mode | `src/agent/rust_repair_agent.py:2077-2137` |
| edit prompt 要求 JSON only、真实行号和允许 edit modes | `src/agent/rust_repair_agent.py:2173-2491` |
| structured edit 解析成功后补齐默认字段，失败后返回空 edits | `src/agent/rust_repair_agent.py:2495-2525` |
| apply structured edits 生成 audit records | `src/agent/rust_repair_agent.py:3276-3505` |
| RTest 通过 `RepairAdapter` 封装 JSON 提取和结构化编辑执行 | `src/agent/rtest/repair_adapter.py:1-12`、`src/agent/rtest/repair_adapter.py:19-46` |
| RTest 对 LLM JSON 解析失败做 contract feedback 并继续下一轮 | `src/agent/rtest/rust_test_agent.py:1224-1239` |
| RTest 调用 `validate_payload()`，但当前实现为空校验 | `src/agent/rtest/rust_test_agent.py:1241-1248`、`src/agent/rtest/response_contract.py:25-27` |
| RTest parse failure 反馈包含 finish reason、stream diagnostics 和 raw tail | `src/agent/rtest/response_contract.py:30-72` |
| RTest prompt schema 包含 `cgr_read`、`rust_read_requests`、`test_artifact_read`、`edits`、`material_keep` 和 `history_control` | `src/agent/rtest/repair_prompt.py:666-712` |
| RTest prompt 要求 JSON 可解析且不要 markdown fence | `src/agent/rtest/repair_prompt.py:777-782` |
| RTest prompt 暴露 debug / static probe JSON schema | `src/agent/rtest/repair_prompt.py:824-846` |
| Probe 是 evidence-gathering round，不能和 edits 混用 | `src/agent/rtest/repair_prompt.py:849-859` |
| Custom API payload、stream options 和 request metadata | `src/llm/custom_api.py:277-290`、`src/llm/custom_api.py:322-343` |
| Custom API invalid surrogate 清洗和 JSON 序列化 | `src/llm/custom_api.py:292-352` |
| RoundLogger 记录 token usage、stream diagnostics、request options 和 call stack | `src/utils/round_logger.py:121-165`、`src/utils/round_logger.py:226-285` |

## 实验钩子

| 实验变量 | 控制方式 | 可观察指标 |
| --- | --- | --- |
| `<CGR_DONE>` continuation | 降低 `api_max_tokens` 或构造长文件 | continuation 轮数、截断失败率、代码完整性 |
| `<CGR_READ>` 协议 | 禁用 read loop 或限制 read rounds | 生成阶段错误猜测、read request 次数、prompt 长度 |
| two-phase repair | 合并诊断和编辑为单次自由 patch 对照 | 结构化编辑失败率、破坏性编辑率、修复轮数 |
| JSON parser 容错 | 给模型允许 / 禁止 markdown fence | parse failure 次数、协议反馈后恢复率 |
| RTest schema 强度 | 实现 `validate_payload()` 与当前 no-op 对比 | 非法字段、错类型字段、无 action round 次数 |
| copy edit modes | 禁用 `copy_range_after` / `copy_c_string_array_after` | 长 JSON 解析失败率、转义错误、数组 / 模板修复成功率 |
| stream vs non-stream | `api_stream=true/false` | visible content empty、finish_reason、round log diagnostics |

## 局限与反例

- **RTest schema 校验尚未完整实现。** `RepairResponseContract.validate_payload()` 当前直接返回 `None`，所以 RTest 主要处理 parse failure，而不严格验证字段类型、edit shape 或 action 互斥。
- **JSON 提取器可能误抓子串。** `_extract_json_payload()` 用贪婪正则抓对象 / 数组子串，若回复中包含多个 JSON-like 片段，可能解析到错误对象。
- **`<CGR_DONE>` 依赖模型遵守协议。** 模型忘记 marker 会触发续写；模型过早给 marker 会提前停止。
- **`<CGR_READ>` 不是外部工具调用标准。** 它是自定义文本协议，解析和错误恢复能力弱于严格 function calling。
- **prompt 约束不等于执行层强校验。** 例如 prompt 写了 probe 与 edits 互斥，RTest 执行层也做了互斥；但并非所有字段都有类似双层保护。
- **Round log 记录了协议失败证据，但不会自动修复协议。** 它适合分析和回溯，真正恢复仍依赖下一轮 prompt feedback。

## 可写入论文位置

- **方法章节：Action Protocols for LLM Repair。** 描述 `<CGR_READ>`、`<CGR_DONE>`、诊断 JSON、编辑 JSON 和 probe JSON 的统一思想。
- **系统设计章节：Parsers and Recovery。** 说明解析失败不等于任务失败，而是被转化为下一轮 feedback。
- **工程优化章节：Structured Edits。** 强调真实行号、允许 edit modes、copy modes 和 audit records 如何降低自由文本 patch 风险。
- **实验章节：协议消融。** 比较自由文本修复、单阶段 JSON、两阶段 JSON、copy edit modes 和 schema 强校验的效果。

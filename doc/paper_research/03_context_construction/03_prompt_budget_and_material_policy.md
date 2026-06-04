# Prompt 预算与材料注入策略

## 研究问题

本节研究系统如何在长文档、长源码和多轮测试修复之间控制 prompt 预算。C 到 Rust 项目翻译的上下文有两个高压点：

- Rust 初始生成阶段：Spec 文档、C 源码、已生成 Rust 文件会持续增长。
- RustTestAgent 修复阶段：失败用例、C 源码、Rust 文件、测试产物和历史摘要会同时进入 prompt。

工程目标是：优先保留直接解释当前目标文件或当前失败用例的材料，允许模型按需读取缺失证据，并通过预算淘汰机制避免上下文膨胀。

## 流程 / 数据流

默认脚本通过 `--rust-test-agent-prompt-budget-chars "${CGR_RUST_TEST_PROMPT_BUDGET_CHARS:-256000}"` 给测试修复阶段设置材料预算。Rust 生成阶段没有统一 token budget 管理器，而是分散在文档裁剪、Spec section 选择、C 源码内联和 `<CGR_READ>` 材料化中。

生成阶段的数据流如下：

1. `RustAgent` / `StableRustAgent` 加载文档时先按文档类型裁剪。
2. `SpecJsonAgent` 可选地把 Markdown 压缩为 `spec_context.json`，并对单文档做 6000 字符截断。
3. `ContextualRustAgent` 构造文件 prompt 时只注入当前文件相关的 spec context、source context 和 registry summary。
4. 如果模型发现信息不足，通过 `<CGR_READ>` 请求 spec、source、rust、registry 或 plan，每轮材料有总预算和单请求预算。
5. C 源码上下文分为「少量关键函数全文内联」和「其余函数索引」，索引函数必须通过 `<CGR_READ>` 补齐后才能实现。

测试修复阶段的数据流如下：

1. RustTestAgent 首轮根据失败用例推断 flag / keyword，主动注入相关 C 函数和 Rust 文件。
2. 模型可返回 `cgr_read`、`rust_read_requests`、`test_artifact_read` 请求更多材料。
3. `material_policy.py` 判断行范围是否升级为 whole file，或把越界行号 clamp 到实际文件尾部。
4. `MaterialBudget` 维护 C 源码、Rust 文件和测试产物的 LRU 材料表。
5. prompt 中明确展示当前材料表、预算压力、最近请求结果和 `material_keep` 约定。

## 关键工程细节

Rust 生成期的材料策略包括：

- `ContextualRustAgent._materialize_read_requests()` 总预算默认 40000 字符，单请求预算 12000 字符。
- `_build_targeted_source_context()` 最多全文内联 10 个关键 C 函数，且单函数不超过 80 行。
- 长函数、测试入口和不够相关的函数只进入 C Source Index，引导模型用 `<CGR_READ>` 补充。
- `_generate_file_with_continuation()` 用 `<CGR_DONE>` 判断是否完整，未完成时最多续写 4 轮。
- `SpecJsonAgent` 的 JSON 压缩失败时会回退到启发式 JSON，保证结构稳定。

RustTestAgent 修复期的材料策略更显式：

- 小文件阈值为 80 KB；若小文件已存在相关材料，或请求行数大于等于 300 行，行范围请求升级为 whole file。
- 行号超出 EOF 时不拒绝，而是返回最后一行，给模型一个明确的文件尾 anchor。
- `FileSlice` 记录请求范围、实际范围和总行数，可反馈给模型「requested vs. actual」。
- `MaterialBudget` 三类材料共享字符预算，通过 LRU 淘汰旧材料；当前新增材料受 protected 保护，避免刚请求就被淘汰。
- prompt 中的 `material_keep` 是优先级提示，不是硬删除命令，这避免模型误以为未列材料马上消失。

## 可引用代码证据

- `scripts/agent.sh:169`：默认 RustTestAgent prompt budget 为 256000 字符，可通过环境变量覆盖。
- `src/agent/rust_agent.py:60-84`：按文档类型裁剪输入文档。
- `src/agent/alternatives/stable_rust_agent.py:50-61`：Stable 路径按文档类型设置 10000 到 20000 字符裁剪上限。
- `src/agent/spec_json_agent.py:115-195`：压缩 Spec 文档时每个文档截取前 6000 字符，并要求输出严格 JSON。
- `src/agent/spec_json_agent.py:230-293`：模型压缩失败时构建稳定 fallback JSON。
- `src/agent/alternatives/contextual_rust_agent.py:1256-1281`：`<CGR_READ>` 材料化的总预算和单请求预算。
- `src/agent/alternatives/contextual_rust_agent.py:1859-1948`：C 源码上下文分为关键函数全文内联和函数索引。
- `src/agent/alternatives/contextual_rust_agent.py:1975-2031`：用 `<CGR_DONE>` 和 continuation 控制长文件输出。
- `src/agent/rtest/material_policy.py:16-61`：小文件 whole-file 升级策略。
- `src/agent/rtest/material_policy.py:64-120`：行范围归一化和 `FileSlice` 读取。
- `src/agent/rtest/repair_prompt.py:50-66`：`MaterialBudget` 维护 C、Rust、测试产物三类材料和 LRU。
- `src/agent/rtest/repair_prompt.py:360-430`：预算超限时淘汰旧材料并生成预算压力摘要。
- `src/agent/rtest/repair_prompt.py:624-790`：prompt 中展示预算状态、材料表、请求反馈和读取 / 编辑规则。
- `src/agent/rtest/rust_test_agent.py:1494-1682`：吸收 C / Rust 材料请求、升级 small file、去重重叠行范围并反馈实际范围。

## 实验钩子

- **预算曲线：** 把 RustTestAgent prompt budget 设置为 64K、128K、256K、512K，比较修复成功率、平均迭代次数、材料淘汰次数。
- **whole-file 升级消融：** 禁用 `should_upgrade_line_range_to_whole_file()`，观察重复请求和修复轮数是否增加。
- **内联 C 函数上限：** 调整 `MAX_INLINE` 和 `MAX_INLINE_LINES`，统计首次生成缺失实现、错误猜测和 `<CGR_READ>` 数量。
- **材料去重收益：** 记录 `already_available` 和 uncovered range 数量，评估重复请求抑制效果。
- **JSON 压缩对比：** SpecJsonAgent 成功压缩、fallback JSON、原始 Markdown 三组比较 prompt 长度和生成质量。

## 局限与反例

- 字符预算不是 token 预算，对中文、代码符号和不同 tokenizer 的估计并不精确。
- Rust 生成阶段的预算机制分散在多个函数中，没有统一 material manifest，也没有全局可视化。
- `SpecJsonAgent` 单文档截取开头 6000 字符，若关键行为在长文档后半段会被丢失。
- whole-file 升级适合小文件，但对包含大量无关模板的小文件可能引入噪声。
- 行范围 clamp 到 EOF 能给模型反馈，但也可能让错误行号请求看起来「成功」，需要结合 `range_changed` 提示判断。

## 可写入论文位置

建议放入「工程优化」或「Context Management」小节。可强调这是一个从「一次性大上下文」转向「材料表 + 按需读取 + 预算淘汰」的实用系统设计。


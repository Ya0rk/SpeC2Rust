# A8-02：上下文去重与材料预算调研

日期：2026-06-04
责任范围：`src/agent/spec_agent.py`、`src/agent/alternatives/contextual_rust_agent.py`、`src/agent/rust_repair_agent.py`、`src/agent/rtest/*.py`、`doc/c_docs_rust_agent_optimization_20260420.md`

## 研究问题

本文件研究系统如何从「把所有文档和源码都塞进 prompt」转向「唯一事实源、目标文件相关上下文、按需读取、材料表和去重」。C 到 Rust 翻译的上下文膨胀来自三类重复：C 文档重复描述同一接口，Rust 生成阶段重复拼接已生成文件，测试修复阶段重复请求同一源码 / 运行产物。

核心研究问题包括：

- 系统如何把 C 项目的迁移边界压缩为 `translation_contract.json`，减少 Markdown 间重复和越界规划？
- `ContextualRustAgent` 如何避免把全部文档、全部 C 源码和全部已生成 Rust 文件注入每个文件 prompt？
- RustRepairAgent 和 RustTestAgent 如何识别已经提供过的材料，避免重复读取同一文件或同一行范围？
- 字符预算、LRU 淘汰和 whole-file 升级会带来哪些收益和反例？

## 流程 / 数据流

### 文档层：从重复 Markdown 到 contract-first

```text
SpecAgent.analyze_and_generate_spec()
  -> parser.analyze_directory()
  -> ModuleSplitter
  -> repo_manifest / subsystem / interfaces / behaviors
  -> _generate_translation_contract()
  -> specs/<module>/{spec,plan,tasks}
  -> _lint_generated_docs()
```

`doc/c_docs_rust_agent_optimization_20260420.md` 记录了早期问题：样例 C 文档在接口、测试要求和工程结构上多次复述，导致下游 Rust 生成受到重复事实和越界计划影响。当前 `SpecAgent` 已经把其中一部分收敛为机器可读 `translation_contract.json`：项目类型、文件角色、函数角色、类型事实、允许生成文件、依赖策略和 forbidden features。

### 生成层：目标文件上下文

```text
ContextualRustAgent.generate_code()
  -> 构建 SpecDocumentIndex / ContextualSpecAgent
  -> 加载 translation_contract
  -> 推导 contextual_plan
  -> 对每个 PlannedFile：
       spec context = rust_context_for_planned_file()
       source context = 少量关键 C 函数全文 + C Source Index
       registry summary = 依赖文件符号 + 全局类型索引
       LLM 可用 <CGR_READ> 补充 spec/source/rust/registry/plan
       写入后更新 registry 和 API contract
```

生成阶段的关键设计是「已生成文件用 registry 表达，而不是全文拼接」。当前目标文件只看与 `depends_on`、`owns`、`source_files`、`source_functions` 相关的材料；长函数、测试入口和低相关函数只进入索引，模型必须通过 `<CGR_READ>` 取详情。

### 编译修复层：材料 key 去重

```text
RustRepairAgent._run_single_iteration()
  -> diagnosis read/search materials
  -> 每轮 structured response 可返回 more_read_requests / search_requests
  -> 物化后用 (kind, path, mode, start_line, end_line) 去重
  -> search 用 (kind, query, path, mode, start_line, end_line) 去重
  -> edits 后刷新已读 Rust 文件材料
```

RustRepairAgent 没有全局 LRU material budget，但轮内会把新增 read/search material 与已有 key 比对，避免把相同材料重复追加到下一轮 prompt。

### 功能测试层：材料表、范围差集和预算淘汰

```text
RustTestAgent._repair_failing_case()
  -> seed C source / Rust files / test artifacts
  -> MaterialBudget(budget_chars)
  -> LLM response:
       cgr_read
       rust_read_requests
       test_artifact_read
       material_keep
  -> _absorb_material_requests()
       small file + repeated range -> whole_file
       line range clamp
       whole file 覆盖 line range
       uncovered_rust_ranges() 只补缺口
  -> MaterialBudget LRU 超预算淘汰
```

RTest 的去重最强：C record、Rust 文件和测试产物共享一个材料预算；whole-file 材料会覆盖同文件行范围；新 whole-file 会移除旧 line ranges；重复材料只 `touch` LRU，不重复进入 prompt。

## 关键工程细节

- **优化备忘录明确了根因。** 早期文档指出 `avl-tree` 样例只有少量 C 文件，但 `c_docs` 中多份文档重复搬运函数、接口、行为和测试要求，并诱导 Rust 端生成无证据高级功能。
- **`translation_contract.json` 是唯一事实源候选。** `SpecAgent` 把 `allowed_rust_files`、`dependency_policy`、`forbidden_without_evidence`、函数角色和类型事实写入 JSON，给 Rust 生成阶段提供最高优先级边界。
- **接口文档不再假装未知事实存在。** `SpecAgent` 的接口文档模板强调只保留当前 source-analysis 观察到的事实；未观察到的 header、宏、错误码和配置不会被添加或假设。
- **目标文件只内联少量 C 源码。** `ContextualRustAgent` 最多内联 10 个关键 C 函数，单函数不超过 80 行；其余函数进入可读索引。
- **已生成 Rust 文件压缩为 registry。** registry 记录模块、类型、函数、常量、字段、方法和引用，用于减少全文重复和避免重复定义。
- **`<CGR_READ>` 有总预算和单请求预算。** 生成阶段材料化总预算默认 40000 字符，单请求默认 12000 字符，支持 spec、source、rust、registry 和 plan。
- **RTest 材料表以 range-aware key 去重。** Rust line range 会合并已有区间，只补 uncovered sub-ranges；whole-file 请求会覆盖行范围，避免同一文件多片段无限增长。
- **测试产物不做简单路径去重。** RTest 对显式 artifact request 会重新读取，因为运行产物可能随每次 edit / build / rerun 改变。
- **`material_keep` 是提示，不是硬删除。** RTest 曾因硬剪枝导致模型基于过期片段修复；当前只把它作为优先级提示，真正淘汰交给 LRU。

## 可引用代码证据

| 论点 | 代码证据 |
| --- | --- |
| 优化备忘录指出 C 文档重复和 Rust 越界生成问题 | `doc/c_docs_rust_agent_optimization_20260420.md:91-119`、`doc/c_docs_rust_agent_optimization_20260420.md:150-179` |
| 优化备忘录提出 `translation_contract.json` 作为机器可读范围边界 | `doc/c_docs_rust_agent_optimization_20260420.md:199-272` |
| `SpecAgent` 生成 contract，包含文件角色、函数角色、边界和 forbidden features | `src/agent/spec_agent.py:1089-1207` |
| `SpecAgent` 写出 `translation_contract.json` | `src/agent/spec_agent.py:1209-1226` |
| `SpecAgent` 文档 lint 检查越界阶段、依赖、发布、FFI 和 contract 外文件 | `src/agent/spec_agent.py:1228-1292` |
| 源文件角色分类区分 source / header / test / example / support | `src/agent/spec_agent.py:854-875` |
| 函数角色分类区分 public API、internal helper、example 和 test | `src/agent/spec_agent.py:1046-1062` |
| allowed Rust files 由 C source stem 和项目类型推导 | `src/agent/spec_agent.py:1064-1088` |
| 接口文档强调只记录直接观察到的事实 | `src/agent/spec_agent.py:552-567`、`src/agent/spec_agent.py:656-665` |
| `ContextualRustAgent` 类注释说明用索引、snippets、registry 和 `<CGR_READ>` 替代全文上下文 | `src/agent/alternatives/contextual_rust_agent.py:935-944` |
| static project context 明确不展开所有文档，按需 `<CGR_READ>` | `src/agent/alternatives/contextual_rust_agent.py:1155-1167` |
| `<CGR_READ>` 解析支持 JSON、Python literal 和 `kind: query` 行格式 | `src/agent/alternatives/contextual_rust_agent.py:1191-1255` |
| `<CGR_READ>` 材料化有 40000 / 12000 字符预算 | `src/agent/alternatives/contextual_rust_agent.py:1256-1281` |
| registry summary 只保留依赖文件符号，并追加全局类型索引 | `src/agent/alternatives/contextual_rust_agent.py:1812-1857` |
| C source context 分为关键函数内联和可读取索引 | `src/agent/alternatives/contextual_rust_agent.py:1859-1948` |
| 写入 `.rs` 后更新 registry 和 API contract | `src/agent/alternatives/contextual_rust_agent.py:2108-2111`、`src/agent/alternatives/contextual_rust_agent.py:2631-2651` |
| RustRepairAgent 对新 read material 做 key 去重 | `src/agent/rust_repair_agent.py:3994-4015` |
| RustRepairAgent 对 search material 做 key 去重 | `src/agent/rust_repair_agent.py:4054-4079` |
| RTest prompt budget 默认 256000 字符 | `src/agent/rtest/constants.py:39-45` |
| `MaterialBudget` 维护 C、Rust、test artifact 三类材料和 LRU | `src/agent/rtest/repair_prompt.py:50-67` |
| C record 重复时只 move-to-end / touch | `src/agent/rtest/repair_prompt.py:81-96` |
| Rust ranges 合并和 uncovered 计算 | `src/agent/rtest/repair_prompt.py:154-206` |
| whole-file Rust 材料会移除同文件 line ranges | `src/agent/rtest/repair_prompt.py:208-235` |
| RTest 小文件 / 多片段请求升级为 whole-file | `src/agent/rtest/material_policy.py:46-61` |
| RTest 行范围越界时 clamp 到实际文件范围 | `src/agent/rtest/material_policy.py:64-120` |
| RTest 吸收 C / Rust 请求时识别重复和 uncovered ranges | `src/agent/rtest/rust_test_agent.py:1494-1691` |
| 测试产物显式请求会重新读取，避免 stale artifact | `src/agent/rtest/rust_test_agent.py:1693-1768` |
| `material_keep` 只作为提示，不做硬剪枝 | `src/agent/rtest/rust_test_agent.py:1263-1269` |
| 预算超限时按 LRU 淘汰并反馈预算压力 | `src/agent/rtest/repair_prompt.py:359-430` |

## 实验钩子

| 实验变量 | 控制方式 | 可观察指标 |
| --- | --- | --- |
| contract-first vs Markdown-only | 固定数据集，对比有无 `translation_contract.json` 输入 | 生成文件数、contract 外文件数、越界依赖数 |
| registry summary 消融 | 禁用目标 registry summary 或全局类型索引 | 重复定义、未解析引用、编译错误数 |
| C source inline 上限 | 调整 `MAX_INLINE` / `MAX_INLINE_LINES` | `<CGR_READ>` 次数、生成阶段 token、初始编译错误 |
| RTest prompt budget | 64K / 128K / 256K / 512K chars | 修复成功率、材料淘汰次数、平均修复轮数 |
| whole-file 升级 | 禁用 `should_upgrade_line_range_to_whole_file()` | 重复行范围请求次数、prompt 长度、修复轮数 |
| range diff 去重 | 禁用 `uncovered_rust_ranges()` | 重复材料字符数、LLM 请求轮数 |
| `material_keep` 语义 | 硬剪枝 vs 当前 LRU 提示 | stale context 失败数、build-error 上下文丢失数 |

## 局限与反例

- **contract 是启发式唯一事实源。** `allowed_rust_files` 来自项目类型和 C 文件 stem，无法证明一定是最佳 Rust 模块划分。
- **Markdown 仍会生成。** `spec.md`、`plan.md`、`tasks.md` 仍可能包含重复或越界内容；当前 lint 只报告，不阻断。
- **字符预算不是 token 预算。** 代码符号、中文和不同 tokenizer 的差异会让 256000 chars 只能作为近似。
- **按需读取依赖召回。** 如果 C function index、spec query 或 registry summary 没有召回关键事实，模型可能在低上下文下错误猜测。
- **whole-file 升级可能引入噪声。** 小文件 whole-file 对重复请求有利，但如果小文件包含大量模板或条件编译，可能稀释当前失败上下文。
- **RustRepairAgent 没有全局 material budget。** 它做了轮内 key 去重，但长修复会话仍可能积累较多材料。
- **测试产物重新读取会增加 prompt 波动。** 这避免 stale artifact，但也让同一请求在不同轮得到不同内容，不适合简单 memoization。

## 可写入论文位置

- **方法章节：Demand-Driven Context Construction。** 描述 contract-first、targeted source context、registry summary 和 `<CGR_READ>` 的组合。
- **工程优化章节：Context Deduplication。** 把材料表、range-aware 去重、whole-file 升级和 LRU 预算作为降低 prompt 膨胀的实现细节。
- **实验章节：上下文消融。** 对比全文文档注入、contract-first、registry、range 去重和预算设置对成功率 / 成本的影响。
- **局限章节：召回和预算风险。** 说明去重不是无损压缩，漏召回和错误 contract 都可能伤害生成质量。

# 符号注册表与按需读取协议

## 研究问题

本节研究 `ContextualRustAgent` 如何在逐文件生成时维护已生成 Rust API 的轻量符号表，并通过 `<CGR_READ>` 协议让模型按需读取缺失证据。目标是降低三类错误：

- 重复定义同一个 struct / enum / free function。
- 引用未生成或未规划的模块。
- 跨文件访问私有字段、私有方法或猜测不存在的成员。

## 流程 / 数据流

符号表和读取协议在同一个闭环中工作：

1. 每个 `.rs` 文件写入后，`registry.update_file()` 从源码中提取模块、类型、函数、常量、字段、方法和可见性。
2. 下一个文件生成前，`_build_targeted_registry_summary()` 根据当前文件依赖注入相关符号；同时附带全局类型索引防止重复定义。
3. 如果模型认为缺少 spec、source、已生成 Rust 文件、registry 或 plan，可输出 `<CGR_READ>`。
4. `_parse_read_requests()` 解析 JSON、Python literal 或行格式请求。
5. `_materialize_read_requests()` 根据 kind 路由到 spec index、C source reader、generated Rust reader、registry summary 或 plan summary。
6. 生成结果写入前，registry 再执行 duplicate findings 和 reference findings。

## 关键工程细节

`RustProjectRegistry` 不是完整 Rust parser，而是一个 guardrail。它做了几件对项目翻译足够实用的事情：

- 先去掉注释，减少注释里的代码片段造成误识别。
- 从 top-level 中移除 `impl` / `trait` block，避免把方法误判为 free function。
- 单独提取 `impl Type` 中的方法，记录为 `Type::method`。
- 提取 struct 字段，并记录字段可见性和字段类型。
- `pub` 视为 public，非 `pub` 视为 private。
- `summary()` 将文件路径、模块路径、类型、函数、常量、字段、方法和 references 格式化给模型。

引用检查包括：

- `crate::<module>` 必须已经生成，或至少属于计划文件；否则报 unplanned / planned-before-generated。
- 跨文件方法调用必须存在于 registry 中，且 public；参数数量要匹配已记录签名。
- 字段访问必须存在于对应类型字段表中，且跨文件访问 private 字段会被拦截。

读取协议支持多种 kind：

- `spec` / `doc`：按 query 从 spec context 中检索。
- `source` / `c`：按 C 函数名、文件名或 token overlap 读取 C source。
- `rust` / `generated` / `file`：读取已生成 Rust 文件。
- `registry` / `symbols`：返回符号表摘要。
- `plan` / `project_plan`：返回当前文件计划。

## 可引用代码证据

- `src/agent/alternatives/contextual_rust_agent.py:360-406`：`RustSymbolReference` 定义 path、kind、visibility、owner_type、params、return_type 和 signature。
- `src/agent/alternatives/contextual_rust_agent.py:409-426`：`RustFileSymbols` 汇总模块、类型、函数、常量、字段、方法和 references。
- `src/agent/alternatives/contextual_rust_agent.py:429-465`：`RustProjectRegistry` 更新文件并抽取符号。
- `src/agent/alternatives/contextual_rust_agent.py:467-500`：去注释并移除 nested `impl` / `trait` item block。
- `src/agent/alternatives/contextual_rust_agent.py:565-636`：提取 top-level mod、type、function 和 const / static。
- `src/agent/alternatives/contextual_rust_agent.py:638-701`：提取 struct field 和 impl method references。
- `src/agent/alternatives/contextual_rust_agent.py:819-897`：检查跨文件成员引用、字段访问和 unplanned module。
- `src/agent/alternatives/contextual_rust_agent.py:910-932`：生成 registry summary。
- `src/agent/alternatives/contextual_rust_agent.py:1191-1216`：多轮 `<CGR_READ>` 交互循环。
- `src/agent/alternatives/contextual_rust_agent.py:1218-1254`：解析 `<CGR_READ>` 请求。
- `src/agent/alternatives/contextual_rust_agent.py:1256-1281`：材料化读取请求并应用预算。
- `src/agent/alternatives/contextual_rust_agent.py:1297-1355`：按函数名、文件名、路径和 token overlap 读取 C source。
- `src/agent/alternatives/contextual_rust_agent.py:1812-1857`：构建面向当前文件的 registry summary。
- `src/agent/alternatives/contextual_rust_agent.py:2631-2651`：把 registry references 持久化到 `.cgr_api_contract.json`。

## 实验钩子

- **重复定义率：** 统计生成文件中 duplicate type / function / const findings，在 registry 开启与关闭时对比。
- **引用错误率：** 统计 cargo check 中 unresolved import、private field、private method 错误，与 registry reference findings 的重合率。
- **读取请求收益：** 统计 `<CGR_READ>` 请求类型分布，以及请求后文件生成是否减少空实现、猜测实现和遗漏 symbol。
- **成员签名检查：** 人工抽样参数数量 mismatch finding，判断是否真实发现 cross-file API 错误。
- **预算敏感性：** 调整 `_materialize_read_requests()` 的 `per_request_budget`，观察读取轮数和生成质量。

## 局限与反例

- registry 使用正则，不理解 Rust 宏、泛型 where clause、trait impl、嵌套模块和复杂 `pub(crate)` 可见性。
- `_strip_comments()` 不会去掉字符串字面量，字符串中的 `fn` / `struct` 理论上可能误导符号抽取。
- 参数数量检查不能判断类型兼容、生命周期或 trait bound。
- 变量类型推断只覆盖函数参数和少量 `let Some(...) = owner.field.as_ref()` 形态，复杂数据流会漏检。
- 读取协议依赖模型主动请求；如果模型在证据不足时直接猜测，仍需要 lint 和后续测试修复兜底。

## 可写入论文位置

建议放入「方法」章节的「Symbol Guardrail and Demand-driven Reads」小节。它可以与上下文索引合并成一个完整故事：索引负责找证据，registry 负责约束已生成接口，`<CGR_READ>` 负责补证据。


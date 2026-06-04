# Pointer / Macro 证据调研

## 研究问题

本节关注 C 项目理解链路中的专项风险证据：指针和宏。它们不是普通接口事实，而是 C 到 Rust 迁移中最容易导致所有权、生命周期、条件编译和语义展开错误的高风险点。

核心研究问题包括：

- 如何在低成本静态扫描下识别需要特别关注的指针模式和宏模式？
- 如何把这些发现按模块过滤，作为 `pointer.md` / `macro.md` 附加到 per-module spec 中？
- 这些专项证据能否降低 Rust 生成阶段的所有权错误、宏翻译错误和条件编译错误？
- 当前启发式扫描的误报、漏报来自哪里，如何设计实验量化？

## 流程 / 数据流

`scripts/agent.sh` 默认不启用专项分析，但支持：

```bash
CGR_USE_POINTER_AGENT=1
CGR_USE_MACRO_AGENT=1
```

启用后主流程为：

```text
SpecAgent._generate_optional_translation_guides()
  -> PointerAgent.collect_findings()
  -> MacroAgent.collect_findings()
  -> self.pointer_findings / self.macro_findings
  -> 每个 module 调用 _write_module_auxiliary_notes()
  -> specs/<module>/pointer.md
  -> specs/<module>/macro.md
  -> _generate_auxiliary_risk_summary()
  -> docs/rewrite-context/04_gaps_and_risks/001_pointer_macro_summary.md
```

模块过滤逻辑会把专项发现限制在当前模块文件和相关头文件范围内，避免每个模块都看到全项目指针和宏噪声。

## 关键工程细节

### PointerAgent：结构化分析 + 文本扫描

`PointerAgent.collect_findings()` 先尝试调用 `CCodeAnalyzer` 获取结构化项目分析，再遍历 `.c` / `.h` 做源码行扫描。

结构化分析负责补充：

- 函数中出现 `malloc`、`calloc`、`realloc` 的分配模式。
- 函数中出现 `free` 的释放模式。
- 函数源码中的函数指针。
- 结构体源码中出现 `*` 的指针字段。

文本扫描负责识别：

- 普通指针声明。
- 函数指针声明。
- 每条发现的文件、行号、类别、原始声明和 Rust 迁移建议。

指针分类包括：

- `c_string_borrowed`
- `c_string_owned`
- `void_pointer`
- `double_pointer`
- `borrowed_const_pointer`
- `heap_pointer`
- `node_or_alias_pointer`
- `function_pointer`
- `allocation_pattern`
- `deallocation_pattern`
- `struct_pointer_field`
- `generic_pointer`

这些类别直接对应 Rust 侧候选表达，例如 `&T`、`&mut T`、`Box<T>`、`Vec<T>`、`NonNull<T>`、`*mut c_void`、`fn(...)`、`Box<dyn Fn(...)>`。

### MacroAgent：宏分类和高价值筛选

`MacroAgent.collect_findings()` 遍历 `.c` / `.h`，识别：

- `#define` 宏。
- `#ifdef` / `#ifndef` / `#if` / `#elif` / `#else` / `#endif` 条件编译。
- include guard。
- 多行反斜杠续行宏。

宏分类包括：

- `constant_macro`
- `function_like_macro`
- `statement_macro`
- `bit_flag_macro`
- `conditional_macro`
- `conditional_block`
- `include_guard`
- `preprocessor_magic_macro`
- `generic_macro`

随后 `_score_finding()` 给每条宏发现打分，`_select_important_findings()` 按类别限额和重要性选出高价值条目。这样能避免大量常量宏淹没真正危险的函数式宏、语句宏和预处理技巧。

### 模块级附加文档

`SpecAgent._build_module_auxiliary_note()` 将专项发现压缩为：

- 条目总数。
- 类型分布。
- 最多 8 条关键条目。
- 每条包含 `kind`、`file:line`、`declaration` 和 `rust_hint`。

这是一种面向 prompt 预算的压缩：专项分析可以收集全量，但每个模块只消费最相关、最短的风险提示。

### 项目级风险汇总

如果启用任一专项分析，`_generate_auxiliary_risk_summary()` 会生成项目级风险摘要，强调：

- Pointer 重点：所有权恢复、节点 / 链式结构、双重指针、函数指针、显式分配与释放。
- Macro 重点：函数式宏、语句型宏、条件编译块、位标志宏、复杂预处理技巧。

这份摘要适合 Rust 生成或修复阶段在遇到所有权、宏替换、条件编译、回调接口错误时优先查看。

## 可引用代码证据

| 证据点 | 代码位置 | 可引用结论 |
| --- | --- | --- |
| PointerAgent 目标 | `src/agent/pointer_agent.py:14-22` | 指针专项 Agent 明确用于扫描指针声明和典型用法，并生成 Rust 指导。 |
| 指针正则 | `src/agent/pointer_agent.py:24-29` | 当前指针识别以声明正则和函数指针正则为核心。 |
| 指针采集流程 | `src/agent/pointer_agent.py:34-56` | 采集流程合并结构化分析和源码逐行扫描。 |
| 结构化指针发现 | `src/agent/pointer_agent.py:100-161` | 从函数体和结构体源码中识别分配、释放、函数指针和结构体指针字段。 |
| 文件级指针扫描 | `src/agent/pointer_agent.py:174-219` | 每条发现包含文件、行号、类别、声明和 Rust 提示。 |
| 指针分类规则 | `src/agent/pointer_agent.py:221-282` | 指针模式被映射到不同 Rust 候选表达。 |
| MacroAgent 目标和正则 | `src/agent/macro_agent.py:13-27` | 宏专项 Agent 识别 `#define` 和条件编译指令。 |
| 宏采集流程 | `src/agent/macro_agent.py:46-60` | 宏扫描覆盖 `.c` / `.h` 并做去重和统计。 |
| 多行宏和条件块 | `src/agent/macro_agent.py:92-176` | 宏扫描支持续行宏和条件编译块。 |
| include guard | `src/agent/macro_agent.py:179-212` | 头文件保护宏被识别为通常不迁移的模式。 |
| 宏分类规则 | `src/agent/macro_agent.py:214-293` | 宏被分类为常量、函数式、语句型、位标志、条件编译等。 |
| 宏重要性打分 | `src/agent/macro_agent.py:312-375` | 高价值宏按类别权重、参数、多行、预处理技巧等打分筛选。 |
| 模块级过滤 | `src/agent/spec_agent.py:2046-2060` | 专项发现只注入相关模块文件和头文件。 |
| pointer.md / macro.md | `src/agent/spec_agent.py:2062-2120` | 每个模块可生成压缩后的专项风险文档。 |
| 项目级风险摘要 | `src/agent/spec_agent.py:2122-2179` | 系统会汇总 pointer / macro 风险，供后续修复阶段使用。 |
| 专项分析接入点 | `src/agent/spec_agent.py:2257-2279` | `SpecAgent` 可选调用 `PointerAgent` 和 `MacroAgent` 并保存发现。 |

## 实验钩子

建议设计以下实验验证专项证据价值：

- **误报率实验**：抽样 `pointer_findings` 和 `macro_findings`，人工标注是否真实需要迁移关注。
- **字符串 / 注释反例测试**：构造包含 `char *`、`void *`、`#define` 文本的字符串和注释，统计误检数量。
- **迁移收益消融**：比较启用和不启用 `PointerAgent` / `MacroAgent` 时的 Rust 编译错误数、所有权错误数、修复轮数。
- **错误相关性分析**：将 Rust 编译错误按 `borrow checker`、类型不匹配、宏替换、条件编译分类，与模块 `pointer.md` / `macro.md` 类型分布关联。
- **高价值筛选实验**：改变 `MAX_MARKDOWN_FINDINGS`、`HIGH_VALUE_KIND_LIMITS` 和模块级 `max_items`，观察文档长度和修复收益。
- **宏分类准确率**：人工标注宏类别，与 `MacroAgent` 分类结果计算 precision / recall。

可采集指标：

- `pointer_total_findings`
- `pointer_findings_by_kind`
- `macro_total_findings`
- `macro_selected_findings`
- `macro_findings_by_kind`
- `module_pointer_note_chars`
- `module_macro_note_chars`
- `ownership_related_compile_errors`
- `macro_related_compile_errors`

## 局限与反例

### PointerAgent 局限

- **原始行扫描会误检字符串和行尾注释**：当前只跳过整行以 `//` 或 `/*` 开始的注释，不能屏蔽字符串字面量、块注释内部后续行和代码后的尾注释。
- **跨行声明识别弱**：复杂声明跨多行时，单行正则容易漏检。
- **限定符覆盖不完整**：`char const *`、`unsigned char *`、`volatile T *` 等可能分类不准。
- **函数指针 typedef 复杂**：`typedef int (*cmp_fn)(...)` 与函数参数中的回调声明需要更完整的声明解析。
- **所有权判断是启发式**：`malloc` 出现在同一文件并不一定说明某个指针变量拥有堆内存。
- **结构体指针字段过粗**：只要结构体源码包含 `*` 就记录 `struct_pointer_field`，不能区分字段数量、字段名和字段角色。

典型反例：

```c
const char *text = "not a declaration: int *p";
/* block comment:
 * void *fake_ptr;
 */
int value; // char *fake_tail_comment
```

这些内容可能暴露原始文本扫描的误报。

### MacroAgent 局限

- **不执行预处理器**：不能判断条件编译分支是否实际启用，也不能展开宏后的真实代码。
- **参数正则不支持复杂嵌套**：`(?P<args>\([^)]*\))` 对嵌套括号和特殊 token 不够稳健。
- **条件块没有成对建模**：`#if` 到 `#endif` 的范围没有形成结构化 span，只是逐行记录。
- **宏体分类依赖 token**：字符串或注释中出现 `sizeof`、`##`、`;` 可能影响分类。
- **include guard 检测有格式假设**：只查看前若干显著行和文件尾部，非典型 guard 可能漏掉。

典型反例：

```c
#define CALL_WITH_DEFAULT(fn, x) fn((x), sizeof((x)))
#if defined(A) && (B + C > 3)
#define FLAG_VALUE (1 << (CONFIG_SHIFT + 1))
#endif
```

这类宏需要更完整的预处理语义和表达式解析才能稳定迁移。

## 可写入论文位置

建议放入论文的「Risk-aware Context Augmentation」或「Engineering Optimizations」章节，标题可为：

- `Pointer and Macro Risk Mining`
- `Risk-aware Auxiliary Notes for Rust Generation`

可强调的技术贡献：

- 把 C 到 Rust 迁移中最脆弱的指针和宏问题显式建模为模块级风险证据。
- 通过分类、Rust 候选表达和模块过滤，把专项静态发现压缩进 per-module prompt。
- 提供可消融的专项上下文开关，用实验验证所有权错误和宏翻译错误是否减少。


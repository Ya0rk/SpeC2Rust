# Tree-sitter 静态分析调研

## 研究问题

本节关注 C 项目理解链路的第一层：如何把原始 C 源码转换成可追溯、可复用、可被下游 Agent 消费的结构化事实。

核心研究问题包括：

- 如何用 tree-sitter 在不完整编译环境下提取函数、调用、结构体、全局变量和宏等迁移事实？
- 如何把源码位置、函数体、调用点和文件映射保留下来，使 Rust 生成阶段能够回溯到 C 证据？
- 静态分析结果在哪些地方足够可靠，在哪些地方必须交给后续文档约束、专项分析或人工复核？
- 相比直接把源码喂给 LLM，结构化 AST 事实能否降低上下文长度、幻觉和范围扩张？

## 流程 / 数据流

当前入口是 `SpecAgent.analyze_and_generate_spec()` 的步骤 2。流程如下：

```text
C 项目目录
  -> CCodeAnalyzer.analyze_directory()
  -> 遍历 .c / .h 文件
  -> tree-sitter parse
  -> 提取 functions / calls / globals / structs / macros / file_path_map
  -> 构建反向调用关系
  -> 写入 src/parse/res/<project>.json
  -> get_project_analysis() 返回内存态完整事实
  -> SpecAgent._build_dependency_graph()
  -> ModuleSplitter / rewrite-context / translation_contract.json
```

`CCodeAnalyzer` 的输出有两种消费方式：

- JSON 文件：`_save_results()` 只写函数列表，保持与历史 `slices.json` 类似的函数粒度格式。
- 内存对象：`get_project_analysis()` 返回函数、全局变量、结构体、宏、文件路径映射，用于 `SpecAgent` 后续构造依赖图、接口文档和迁移契约。

函数记录的关键字段包括：

- `func_defid`：`file:function` 形式的稳定标识。
- `span` / `pieces`：`file:start_line:start_col:end_line:end_col` 形式的位置证据。
- `source`：完整函数源码片段。
- `calls`：调用该函数的调用者列表，包含调用位置和调用行文本。
- `num_lines`：函数规模，用于模块拆分和 prompt 预算估计。

## 关键工程细节

### tree-sitter 版本兼容

`_load_c_language()` 兼容多种 `tree_sitter` / `tree_sitter_c` API：

- 先尝试 `tree_sitter_c.language()` 加 `Language(ptr)`。
- 再尝试 `Language(ptr, "c")`。
- 最后回退到仓库内置的 `c-language.so`。

这让同一份代码能在不同 Python 包版本下运行，减少实验环境差异对论文复现的影响。

### 源码事实保真

函数提取使用 AST 节点的 byte span 直接截取源码，位置使用 tree-sitter 的 `start_point` / `end_point` 转为 1-based 行列号。这样下游文档不需要重新猜测函数边界。

调用提取只在 `call_expression` 节点上进行，且只接受 `function` 字段为 `identifier` 的直接调用。调用证据保留了调用表达式位置和所在源码行，适合后续生成「该函数被谁调用」的解释。

### 反向调用图

函数内部先暂存 `callees`，随后 `_build_call_relationships()` 统一把它转为被调用函数的 `calls`。这种反向结构更适合下游回答「迁移这个函数时还要看哪些调用者」。

### 宏和内联函数的工程补丁

宏提取当前不是 AST 级 preprocessor 分析，而是按行扫描 `#define`，并支持反斜杠续行。这样实现成本低，能捕获常见常量宏和函数式宏，但不能理解条件展开后的真实语义。

头文件中的特殊内联函数另有 `_extract_inline_functions()` 兜底逻辑，针对 `IALLOC_INLINE`、`C_CTYPE_INLINE` 等模式做文本级提取。这是面向真实 C 项目的工程补丁，说明项目理解层不能只依赖标准 AST 节点。

### 与依赖图的关系

`SpecAgent._build_dependency_graph()` 会优先使用 `project_analysis` 中的函数调用和结构体源码，重建 `call_graph` 与 `struct_usage`。这使 tree-sitter 结果成为模块划分的上游事实源，而不是孤立的统计结果。

## 可引用代码证据

| 证据点 | 代码位置 | 可引用结论 |
| --- | --- | --- |
| C 语言解析器兼容加载 | `src/parse/c_ast.py:48-77` | 系统显式处理不同 tree-sitter 版本，保证静态分析环境可迁移。 |
| 遍历 `.c` / `.h` 并生成 JSON | `src/parse/c_ast.py:79-111` | 分析粒度覆盖源文件和头文件，输出可缓存的结构化结果。 |
| 函数事实字段 | `src/parse/c_ast.py:211-280` | 每个函数保留 ID、位置、源码、行数和调用信息。 |
| AST 调用点抽取 | `src/parse/c_ast.py:316-361` | 调用关系来自 `call_expression`，并保留调用行。 |
| 宏定义抽取 | `src/parse/c_ast.py:490-533` | 宏分析采用文本扫描和续行处理，属于轻量事实提取。 |
| 反向调用关系 | `src/parse/c_ast.py:628-664` | `calls` 表示「谁调用了当前函数」，便于迁移影响分析。 |
| 内存态项目分析结果 | `src/parse/c_ast.py:689-707` | 下游不只依赖 JSON，还消费完整的函数、结构体、宏和文件映射。 |
| 依赖图消费 AST 事实 | `src/agent/spec_agent.py:1464-1496` | `SpecAgent` 基于 `project_analysis` 重建调用图和结构体使用关系。 |

## 实验钩子

建议设计以下实验来验证该层贡献：

- **覆盖率实验**：对每个数据集记录 `.c` / `.h` 文件数、函数数、结构体数、宏数、调用边数，与 `ctags`、`clang` AST 或人工标注对比。
- **位置准确性实验**：随机抽样函数和宏，检查 `span` 是否能准确定位原始源码范围。
- **调用图精度实验**：抽样 `calls`，评估直接调用识别的 precision / recall，并单独统计函数指针、宏调用、成员函数式宏造成的漏检。
- **下游消融实验**：比较「直接源码上下文」和「tree-sitter 结构化事实」对 Rust 编译成功率、修复轮数、生成文件越界数的影响。
- **缓存收益实验**：记录静态分析耗时和输出大小，评估复用 `src/parse/res/<project>.json` 能否减少多轮实验成本。

可直接采集的指标：

- `functions_count`
- `structs_count`
- `macros_count`
- `call_edges_count`
- `avg_function_lines`
- `parse_failures`
- `span_validation_failures`

## 局限与反例

- **预处理不完整**：当前不会执行真实 C preprocessor，条件编译、宏展开、平台分支不会被语义化。
- **宏生成函数可能漏检**：通过宏拼出的函数名或声明不会自然出现在 tree-sitter 的函数定义节点里。
- **函数指针调用漏检**：`_extract_function_calls()` 只处理 `identifier(...)` 形式，`fp(...)` 可以捕获为名字但无法知道真实目标，`table[i].cb(...)` 等复杂调用会漏掉。
- **结构体命名不稳定**：匿名结构体、`typedef struct { ... } Alias;` 等场景可能需要额外恢复 alias，否则下游会看到 `anonymous`。
- **文本级宏扫描有噪声**：`#define` 提取不理解注释、字符串和预处理上下文，也不校验宏是否实际生效。
- **内联函数兜底有项目特化痕迹**：`IALLOC_INLINE`、`C_CTYPE_INLINE` 是针对特定代码风格的补丁，泛化能力需要实验验证。

典型反例：

```c
#define WRAP_CALL(fn, x) fn(x)
typedef int (*cmp_fn)(const void *, const void *);
static inline int (*factory(void))(int) { return impl; }
```

这些代码能暴露当前实现对宏调用、函数指针和复杂声明器的边界。

## 可写入论文位置

建议放入论文的「方法」或「系统设计」章节，标题可为：

- `Static Fact Extraction for C-to-Rust Migration`
- `Tree-sitter Based Program Understanding`

可强调的技术贡献：

- 在无完整编译数据库的条件下，用 tree-sitter 生成可追溯的 C 程序事实。
- 将函数、调用、结构体、宏和源码位置统一为下游 Agent 可消费的中间表示。
- 用结构化事实替代纯源码上下文，为模块切分、接口文档、迁移契约和 Rust 生成边界提供共同事实源。


# ModuleSplitter 模块切分调研

## 研究问题

本节关注 C 项目理解链路的第二层：如何把整个 C 工程切成适合 LLM 文档生成和 Rust 迁移的模块单元。

核心研究问题包括：

- 如何在不依赖完整构建系统的情况下，从目录、函数、调用关系和结构体使用中恢复模块边界？
- 如何同时满足两个目标：语义上尽量内聚，工程上控制 prompt 尺寸？
- 模块切分是否能降低文档重复、减少 Rust 生成范围扩张，并提高编译修复成功率？
- 哪些切分信号是有效的，哪些只是工程兜底？

## 流程 / 数据流

`ModuleSplitter` 不直接解析 C 代码，而是消费上游准备好的三类输入：

```text
project_info
  - c_files / h_files / other_files / build_system

project_analysis
  - functions / structs / macros / file_path_map

dependency_graph
  - include_graph / call_graph / struct_usage / file_symbols
```

公共入口是 `split()`，流程如下：

```text
project_info + project_analysis + dependency_graph
  -> schema 归一化
  -> 按 .c 文件目录识别 candidate_modules
  -> 绑定 functions / structs
  -> 计算 internal_calls / external_calls / cohesion_score
  -> 根据规模和内聚度判断 needs_split
  -> 对大模块执行 struct / prefix / file_local 三轮聚类
  -> 生成 module_units
  -> 对 module_units 继续生成 cluster_units
```

输出分两层：

- `module_units`：用于生成 `01_subsystems/*.md`、接口文档和每个模块的 `spec.md` / `plan.md` / `tasks.md`。
- `cluster_units`：更细粒度的函数簇，当前主要作为规模统计和潜在局部分析单元。

`SpecAgent._split_modules()` 明确把模块边界决策委托给 `ModuleSplitter`，使切分逻辑可以独立演进。

## 关键工程细节

### 输入 schema 归一化

`c_ast.py` 产出的函数字段是 `func_defid`、`span`、`num_lines` 等，而 `split.py` 内部使用 `name`、`file`、`start_line`、`end_line`、`line_count`。`_normalize_function_record()` 在入口统一补齐字段，避免后续每个算法分支兼容两套 schema。

这是重要的工程优化：它把历史格式兼容集中在入口，降低模块聚类代码的复杂度。

### 目录是第一层线索，不是最终答案

候选模块只从 `project_info["c_files"]` 出发，按目录分桶，再用目录 token 命中 `MODULE_CATEGORIES`。目录可以给出 `parser`、`io`、`config`、`memory` 等粗类别，但后续仍会用调用关系和结构体关系修正。

### 轻量内聚度

内聚度定义为：

```text
cohesion_score = internal_calls / (internal_calls + external_calls)
```

如果模块内没有项目内调用边，也没有跨模块调用边，当前实现把它视为 1.0，避免叶子模块被误判为低质量模块。

该指标不是严格架构度量，而是服务于「是否需要继续拆」的启发式信号。

### 阈值控制 prompt 规模

关键阈值包括：

- `MAX_MODULE_FILES = 10`
- `MAX_MODULE_FUNCTIONS = 60`
- `MAX_CLUSTER_FUNCTIONS = 15`
- `MAX_CLUSTER_LINES = 700`
- `MIN_STRUCT_CLUSTER_SIZE = 2`
- `MIN_PREFIX_CLUSTER_SIZE = 2`

这些阈值把切分目标从「找理论最优模块」转成「生成规模可控、语义可解释的迁移单元」。

### 三轮聚类策略

大模块拆分按固定优先级执行：

1. **共享结构体聚类**：多个函数围绕同一个 `struct` 工作时，优先放进同一簇。
2. **函数名前缀聚类**：例如 `parse_init`、`parse_run`、`parse_close` 可按 `parse_*` 聚合。
3. **文件局部性兜底**：剩余函数按文件和源码顺序切块，避免落入巨大 `misc`。

这个顺序体现了信号强弱：共享数据结构通常强于命名风格，命名风格强于单纯文件相邻。

### 确定性输出

模块、函数、结构体都按路径、行号、名字排序。稳定排序对论文实验很重要，因为它减少了非确定性文档差异，便于比较不同阈值或不同 Agent 配置的效果。

## 可引用代码证据

| 证据点 | 代码位置 | 可引用结论 |
| --- | --- | --- |
| 模块切分器职责说明 | `src/agent/split.py:9-31` | `ModuleSplitter` 明确分为候选模块、子模块、函数簇三层。 |
| 规模阈值 | `src/agent/split.py:33-39` | 切分算法内置 prompt 预算相关阈值。 |
| 目录类别词表 | `src/agent/split.py:40-52` | 初始模块类别来自目录 token 启发式。 |
| 函数记录归一化 | `src/agent/split.py:74-115` | 上游 AST schema 被统一转换为内部稳定字段。 |
| 内聚度计算 | `src/agent/split.py:190-228` | 内聚度由模块内外调用比值给出。 |
| 拆分原因生成 | `src/agent/split.py:260-277` | 是否拆分同时考虑文件数、函数数、内聚度和职责明确性。 |
| 大组切块 | `src/agent/split.py:302-336` | 函数数和行数共同控制 cluster 大小。 |
| 三轮信号聚类 | `src/agent/split.py:338-396` | 聚类顺序为结构体、函数名前缀、文件局部。 |
| 候选模块识别 | `src/agent/split.py:432-462` | 初始候选模块由 `.c` 文件目录生成。 |
| 语义收敛 | `src/agent/split.py:464-533` | 候选模块绑定函数、结构体并按内聚度决定是否拆分。 |
| SpecAgent 委托切分 | `src/agent/spec_agent.py:1510-1534` | 文档生成流程不内联模块边界算法，而是调用 `ModuleSplitter.split()`。 |

## 实验钩子

建议围绕「切分质量」和「下游收益」设计实验：

- **目录基线对比**：只按目录切分，与当前 `ModuleSplitter` 对比文档长度、模块函数数分布、Rust 编译成功率。
- **信号消融**：分别关闭结构体聚类、函数名前缀聚类、内聚度拆分，观察模块数量、平均 prompt 长度和修复轮数。
- **阈值敏感性**：扫描 `MAX_CLUSTER_FUNCTIONS` 和 `MAX_CLUSTER_LINES`，记录生成失败率、上下文长度和重复率。
- **人工一致性评估**：抽样模块，让人工标注「是否职责清晰」，与 `cohesion_score` 和 `split_reasons` 相关性对比。
- **稳定性实验**：同一项目重复运行，检查 `module_units` 顺序、名称和函数集合是否稳定。

建议新增机器可读日志：

```json
{
  "module_name": "...",
  "function_count": 0,
  "file_count": 0,
  "total_lines": 0,
  "cohesion_score": 0.0,
  "internal_calls": 0,
  "external_calls": 0,
  "split_reasons": []
}
```

这些字段可直接支持论文中的表格和消融图。

## 局限与反例

- **候选模块只看 `.c` 文件**：头文件逻辑密集的项目，尤其是 header-only 风格库，可能无法形成合理候选模块。
- **目录名质量影响初始分类**：如果项目目录命名为 `src/core/misc`，类别启发式很难恢复真实职责。
- **调用图质量决定上限**：`dependency_graph` 的调用关系来自 AST 和部分文本扫描，函数指针、宏调用、条件编译分支会影响内聚度。
- **函数同名风险**：调用图按函数名聚合，跨文件同名 `static` 函数可能混淆。
- **结构体使用信号偏弱**：当前主要从源码文本中匹配 `struct Name`，无法完整理解 typedef alias 或字段访问语义。
- **尾部路径匹配可能误伤**：`_match_file()` 支持 `endswith`，路径不规范且存在同名文件时可能把函数挂到错误模块。
- **二次聚类深度有限**：当前最多做有限递归，面对超大单文件或自动生成 C 文件时仍可能产生过大模块。

典型反例：

```text
src/common.c
src/common.h
src/platform_linux.c
src/platform_windows.c
src/generated_parser.c
```

这种项目里目录和文件名都很粗，真实模块边界可能依赖条件编译、生成器和平台宏，当前切分只能给出工程化近似。

## 可写入论文位置

建议放入论文的「C Program Understanding」或「Context Construction」章节，标题可为：

- `Hierarchical Module Decomposition`
- `Prompt-budget Aware C Module Splitting`

可强调的技术贡献：

- 用目录、调用图、结构体使用和规模阈值组合出低成本的层次化模块切分算法。
- 把模块切分目标与 LLM prompt 预算和文档生成粒度绑定，而不是追求传统编译器意义上的完备程序切片。
- 通过 `module_units` 和 `cluster_units` 提供可实验、可消融的中间表示。


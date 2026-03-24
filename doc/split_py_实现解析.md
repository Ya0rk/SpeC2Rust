# `src/agent/split.py` 实现解析

本文讲解 [`src/agent/split.py`](D:/Code/Personal/tcode_win/src/agent/split.py) 的实现逻辑、核心数据流、关键辅助函数，以及这份实现当前的设计取舍与局限。

## 1. 这个文件在整个项目里的作用

`ModuleSplitter` 的职责，是把一个 C 项目的静态分析结果进一步整理成两层结构：

1. `module_units`
2. `cluster_units`

它并不负责解析 C 代码本身，而是消费上游已经准备好的三类输入：

1. `project_info`
   主要包含 `c_files` 等基础文件信息。
2. `project_analysis`
   主要包含 `functions`、`structs` 等 AST/静态分析结果。
3. `dependency_graph`
   主要包含 `call_graph`、`struct_usage` 等依赖关系。

在调用链上，它由 [`src/agent/spec_agent.py`](D:/Code/Personal/tcode_win/src/agent/spec_agent.py) 创建并使用：

- `SpecAgent` 在 `16` 行导入 `ModuleSplitter`
- 在 `39` 行初始化 `self.module_splitter = ModuleSplitter()`
- 在 `252` 行调用 `self.module_splitter.split(...)`

也就是说，`split.py` 是 `SpecAgent` 文档生成流程中的“模块切分器”。它负责把“整个工程”拆成适合生成 spec 和 subsystem 文档的中间结构。

## 2. 整体思路：三阶段拆分

`split()` 是入口，定义在 [`src/agent/split.py:513`](D:/Code/Personal/tcode_win/src/agent/split.py#L513)。它的流程很清晰，分成三步：

1. 识别候选模块：`_identify_candidate_modules()`
2. 基于语义和规模收敛模块：`_refine_modules_with_semantics()`
3. 从模块中继续切出函数簇：`_identify_function_clusters()`

可以把它理解为：

```text
项目文件 -> 按目录先粗分 -> 用函数/结构体/调用关系修正 -> 必要时继续拆小 -> 生成最终函数簇
```

最终返回：

```python
(module_units, cluster_units)
```

其中：

- `module_units` 是较粗粒度的“子模块”
- `cluster_units` 是更细粒度的“函数簇”

## 3. 类级常量：这份算法的阈值控制器

类定义在 [`src/agent/split.py:9`](D:/Code/Personal/tcode_win/src/agent/split.py#L9)，前面几组常量决定了拆分尺度：

- `MAX_MODULE_FILES = 10`
  单个模块如果文件数超过 10，倾向继续拆分。
- `MAX_MODULE_FUNCTIONS = 60`
  单个模块函数数超过 60，倾向继续拆分。
- `MAX_CLUSTER_FUNCTIONS = 15`
  单个函数簇尽量不超过 15 个函数。
- `MAX_CLUSTER_LINES = 700`
  单个函数簇总行数尽量不超过 700。
- `MIN_STRUCT_CLUSTER_SIZE = 2`
  只有至少 2 个函数共享某个结构体，才值得按结构体聚类。
- `MIN_PREFIX_CLUSTER_SIZE = 2`
  只有至少 2 个函数拥有相同前缀，才值得按前缀聚类。

这些阈值的意义不是“绝对正确”，而是给后续文档生成一个可控粒度。换句话说，这个类追求的是“足够合理的工程切块”，不是形式化最优聚类。

## 4. 第一层辅助函数：做数据清洗和归一化

这一层函数主要是为了让后续分组逻辑稳定。

### 4.1 路径归一化

[`_normalize_path()`](D:/Code/Personal/tcode_win/src/agent/split.py#L46) 会把路径中的反斜杠转成 `/`，并去掉前缀 `./`。  
这样做的原因是：项目分析结果可能来自不同来源，如果路径格式不统一，后面的匹配和分组会很容易出错。

[`_tokenize_path()`](D:/Code/Personal/tcode_win/src/agent/split.py#L49) 会把目录路径拆成 token，分隔符包含：

- `/`
- `_`
- `.`
- `-`

比如：

```text
src/net-utils/parser.c -> ["src", "net", "utils", "parser", "c"]
```

这个 token 化结果会用于推断模块类别。

### 4.2 函数名和行号处理

[`_extract_function_prefix()`](D:/Code/Personal/tcode_win/src/agent/split.py#L56) 会把函数名前两个下划线片段作为前缀。  
例如：

- `http_parse_header` -> `http_parse`
- `list_init` -> `list_init`
- `main` -> `main`

这是后续“按函数名前缀聚类”的依据。

[`_safe_line_count()`](D:/Code/Personal/tcode_win/src/agent/split.py#L64) 用于安全计算函数行数：

1. 优先用 `end_line - start_line + 1`
2. 如果没有合法范围，则退回 `line_count`
3. 仍然不行就给默认值 `1`

这样即使上游分析数据不完整，也不至于让聚类过程崩掉。

[`_function_sort_key()`](D:/Code/Personal/tcode_win/src/agent/split.py#L71) 则统一函数排序规则：

1. 文件路径
2. 起始行
3. 函数名

这保证了每次输出顺序稳定，便于后续文档生成和调试。

## 5. 模块类别识别：先用目录名猜职责

[`MODULE_CATEGORIES`](D:/Code/Personal/tcode_win/src/agent/split.py#L31) 定义了一组“目录关键词 -> 模块类别”的映射，例如：

- `main`
- `config`
- `parser`
- `io`
- `protocol`
- `storage`
- `utils`

[`_resolve_module_category()`](D:/Code/Personal/tcode_win/src/agent/split.py#L77) 的判断逻辑分两轮：

1. 高置信度匹配  
   如果目录 token 中直接出现关键词，比如 `parser`、`config`，就返回对应类别和 `"high"`。
2. 中置信度匹配  
   如果 token 和关键词相等，或者 token 以前缀方式命中关键词，也返回对应类别，但置信度通常是 `"medium"`。

如果都匹配不上，就返回：

```python
("module", "medium")
```

本质上，这一步是“基于目录命名的启发式分类”，它不是语义分析，只是给后续模块命名和初始分组提供一个起点。

## 6. 第二层辅助函数：为收敛模块做准备

### 6.1 文件匹配与头文件收集

[`_match_file()`](D:/Code/Personal/tcode_win/src/agent/split.py#L91) 用来判断某个函数或结构体记录属于哪个模块文件集合。  
它既支持完全相等，也支持 `xxx/filename.c` 这种尾部匹配。这样可以兼容相对路径层级不完全一致的情况。

[`_collect_file_headers()`](D:/Code/Personal/tcode_win/src/agent/split.py#L97) 会从模块文件中筛出头文件，后面生成模块信息时会一并带上。

### 6.2 项目函数集合与内聚度计算

[`_build_project_function_set()`](D:/Code/Personal/tcode_win/src/agent/split.py#L100) 会构建项目中所有函数名集合，主要用于过滤掉对外部库函数的调用。

[`_compute_module_cohesion()`](D:/Code/Personal/tcode_win/src/agent/split.py#L103) 是这个文件里很关键的一段逻辑。它会统计：

- `internal_calls`
  模块内函数调用模块内函数的次数。
- `external_calls`
  模块内函数调用项目中其他模块函数的次数。

然后计算：

```python
cohesion_score = internal_calls / (internal_calls + external_calls + 1)
```

这里的 `+1` 是为了防止分母为 0。  
这个分数越高，说明模块内部自洽程度越高；越低，说明这个目录下的函数更像是“杂糅在一起”。

### 6.3 拆分原因生成

[`_make_split_reasons()`](D:/Code/Personal/tcode_win/src/agent/split.py#L136) 会根据几个条件给模块打上“需要拆分”的原因：

- 文件太多
- 函数太多
- 内聚度太低且文件数超过 3
- 类别无法明确识别且目录范围较大

这个函数本质上是在做一个布尔决策解释器。它不仅决定 `needs_split`，还顺手产出人类可读的原因文本，便于后续文档或调试输出。

## 7. 候选模块识别：按目录先粗分

[`_identify_candidate_modules()`](D:/Code/Personal/tcode_win/src/agent/split.py#L290) 对应第一阶段。

它的做法很直接：

1. 遍历 `project_info["c_files"]`
2. 对每个文件做路径归一化
3. 取 `os.path.dirname()` 作为目录名
4. 按目录把文件分组
5. 为每个目录推断类别和置信度
6. 生成候选模块字典

候选模块的典型结构类似：

```python
{
    "name": "parser_src_parser",
    "category": "parser",
    "directory": "src/parser",
    "files": [...],
    "confidence": "high",
    "headers": [...]
}
```

这里要注意一个设计点：  
第一阶段完全是“基于目录”的粗粒度切分，还没有真正使用函数调用关系和结构体关系。作者显然认为目录结构虽然不完美，但在工程项目中通常是个不错的初始线索。

## 8. 模块收敛：把“目录分组”变成“语义模块”

[`_refine_modules_with_semantics()`](D:/Code/Personal/tcode_win/src/agent/split.py#L320) 是整个文件的核心。

它做了三件事：

1. 把候选模块和真实函数/结构体绑定起来
2. 计算该模块的内聚度
3. 判断是否需要继续拆分

### 8.1 绑定函数和结构体

对每个候选模块，代码会：

- 从 `project_analysis["functions"]` 中筛出属于该模块文件的函数
- 从 `project_analysis["structs"]` 中筛出属于该模块文件的结构体

这样模块就从“只有文件列表”变成了“带函数、带结构体、带统计信息”的完整对象。

### 8.2 计算内聚度与拆分标记

接着用 `_compute_module_cohesion()` 计算：

- `internal_calls`
- `external_calls`
- `cohesion_score`

然后用 `_make_split_reasons()` 判断是否需要拆分，并生成：

- `needs_split`
- `split_reasons`
- `split_reason`

其中 `split_reason` 是把原因列表用中文分号拼成一个字符串，方便展示。

### 8.3 拆或不拆

这一阶段的最后一步：

- 如果 `needs_split == False`，直接把模块加入最终结果
- 如果 `needs_split == True`，调用 `_split_module_by_clusters()` 继续细分

所以 `_refine_modules_with_semantics()` 的作用不是“简单打标签”，而是负责把候选模块真正收敛成最终的 `module_units`。

## 9. 大模块拆分：三轮聚类策略

真正的细分逻辑在 [`_build_clusters_from_functions()`](D:/Code/Personal/tcode_win/src/agent/split.py#L212)。

它对一个函数列表做三轮聚类，而且是有优先级的。

### 9.1 第一轮：按共享结构体聚类

思路是：

1. 遍历每个函数
2. 查询 `struct_usage[func_name]`
3. 把使用同一个结构体的函数放进同一个桶

然后按“函数数从大到小”处理这些桶。只要一个结构体关联的可用函数数不少于 `MIN_STRUCT_CLUSTER_SIZE`，就把它切成一个或多个 cluster。

这一轮优先级最高，说明作者认为：

> 如果多个函数围绕同一个结构体工作，它们很可能属于同一职责簇。

这对 C 项目很合理，因为大量模块化设计就是围绕 `struct + operation functions` 展开的。

### 9.2 第二轮：按函数名前缀聚类

第一轮认领完成后，剩余函数再按 `_extract_function_prefix()` 提取的前缀分组。

例如：

- `json_parse_value`
- `json_parse_array`
- `json_parse_object`

都会落到 `json_parse` 组。

只有组大小不少于 `MIN_PREFIX_CLUSTER_SIZE` 才会成为正式 cluster。  
这一步适合处理那些没有明显共享结构体、但命名风格一致的函数族。

### 9.3 第三轮：按文件局部收尾

前两轮之后仍未认领的函数，会按文件分组，然后使用 `_chunk_large_group()` 按顺序切块。

这一步的意图很明确：

- 避免剩余函数全部掉进一个巨大的 `misc`
- 尽量保留“同文件、相邻函数”这个局部性

所以第三轮其实是一个兜底策略。

## 10. `_chunk_large_group()`：把过大的组再切小

[`_chunk_large_group()`](D:/Code/Personal/tcode_win/src/agent/split.py#L177) 是所有聚类过程都会反复用到的基础工具。

它按函数顺序遍历，并维护：

- `current`
  当前簇里的函数列表
- `current_lines`
  当前簇累计行数

只要满足以下任一条件，就会把当前簇落盘并开启新簇：

1. 当前簇函数数已经达到 `MAX_CLUSTER_FUNCTIONS`
2. 加上当前函数后，总行数会超过 `MAX_CLUSTER_LINES`

这一步很重要，因为前面的“共享结构体”或者“相同前缀”只是在表达语义相近，但并不保证输出大小适中。  
`_chunk_large_group()` 负责把“语义相近”转成“规模可控”。

## 11. 子模块实体化：把 cluster 变成正式模块对象

[`_materialize_cluster_module()`](D:/Code/Personal/tcode_win/src/agent/split.py#L261) 的作用，是把一个抽象 cluster 转成完整模块字典。

它会补齐：

- 模块名
- category
- files
- functions
- structs
- headers
- cluster_type
- cluster_key
- function_count
- total_lines
- parent_module
- confidence

模块名的生成方式是：

```python
f"{parent_module['name']}_{cluster['cluster_key']}_{index:02d}"
```

所以最终名字一般会带上父模块名、聚类键和序号。  
这保证了名字可追溯，也避免同名冲突。

## 12. `_split_module_by_clusters()`：对过大模块做递归式细分

[`_split_module_by_clusters()`](D:/Code/Personal/tcode_win/src/agent/split.py#L388) 会对“需要拆分”的模块执行聚类细分。

流程是：

1. 取出模块函数列表
2. 调 `_build_clusters_from_functions()` 得到原始 cluster
3. 用 `_materialize_cluster_module()` 把每个 cluster 变成子模块
4. 如果子模块仍然过大，再对该子模块函数重新聚类一次

这个“再聚类一次”的逻辑说明作者考虑了这样一种情况：

- 某个共享结构体簇或者前缀簇本身还是太大

所以它允许有限度的二次细分。

另外还有一个兜底：

- 如果最后一个子模块都没生成出来，就构造一个 `misc` 模块

这保证了函数不会在拆分流程里丢失。

## 13. 最终函数簇：从模块再切到可生成文档的最小单元

[`_identify_function_clusters()`](D:/Code/Personal/tcode_win/src/agent/split.py#L440) 是第三阶段，目标是生成最终的 `cluster_units`。

这里和前面的模块拆分有点像，但粒度更细，面向“后续文档或 spec 生成的工作单元”。

### 13.1 小模块直接作为一个函数簇

如果模块本身已经足够小：

- 函数数不超过 `MAX_CLUSTER_FUNCTIONS`
- 总行数不超过 `MAX_CLUSTER_LINES`

那么它直接被当成一个 cluster unit。

### 13.2 大模块继续在文件内切分

如果模块仍然偏大，就按文件分组。对每个文件：

1. 先按函数名前缀分组
2. 如果能形成有效分组，就按前缀切簇
3. 如果连前缀分组都做不起来，就退回按文件局部切块

这里和 `_build_clusters_from_functions()` 的差别在于：

- 这里没有再按结构体优先聚类
- 更强调“文件内局部性”

这说明作者把 `cluster_units` 看作更偏执行层的文档单元，而不是再次追求完整语义模块。

## 14. `split()` 入口方法：串起整个流程

[`split()`](D:/Code/Personal/tcode_win/src/agent/split.py#L513) 是公共接口。

它的内部顺序是固定的：

1. `_identify_candidate_modules()`
2. `_refine_modules_with_semantics()`
3. `_identify_function_clusters()`

最后返回：

```python
return module_units, cluster_units
```

因此，如果你从外部只关心“怎么用”，只需要准备好三份输入并调用这个方法即可。

## 15. 输出数据大致长什么样

### 15.1 `module_units`

每个模块大致包含：

```python
{
    "name": "...",
    "category": "...",
    "directory": "...",
    "files": [...],
    "functions": [...],
    "structs": [...],
    "headers": [...],
    "cohesion_score": 0.0,
    "internal_calls": 0,
    "external_calls": 0,
    "needs_split": False,
    "split_reasons": [...],
    "confidence": "high" | "medium"
}
```

如果它是从 cluster 拆出来的，还会额外带上：

- `cluster_type`
- `cluster_key`
- `function_count`
- `total_lines`
- `parent_module`

### 15.2 `cluster_units`

每个函数簇大致包含：

```python
{
    "module_name": "...",
    "cluster_type": "...",
    "cluster_key": "...",
    "functions": [...],
    "files": [...],
    "structs": [...],
    "headers": [...],
    "total_lines": 0
}
```

这类结构已经非常适合喂给下游 LLM 或文档模板了，因为它控制住了上下文规模。

## 16. 这份实现的优点

### 16.1 分层很清晰

不是一上来就做细粒度聚类，而是：

1. 目录粗分
2. 语义收敛
3. 函数簇细分

这种层次化设计比单次全局聚类更可解释，也更容易调参。

### 16.2 启发式足够工程化

它没有追求复杂图算法，而是组合了几种非常实用的信号：

- 目录名
- 调用关系
- 结构体共用关系
- 函数名前缀
- 文件局部性
- 函数规模

对“生成工程文档”这个目标来说，这比追求聚类理论最优更务实。

### 16.3 输出结构稳定

很多地方都做了排序和兜底处理，意味着：

- 结果更容易复现
- 更适合做批处理文档生成
- 更方便调试差异

## 17. 需要特别注意的实现细节和局限

### 17.1 候选模块只基于 `.c` 文件

`_identify_candidate_modules()` 使用的是 `project_info["c_files"]`。  
这意味着头文件不会主动形成候选模块，只会在后续通过文件集合筛选时作为附属信息被收集。

这在很多 C 项目里是合理的，但如果项目把大量逻辑放在头文件里，这个策略会漏掉一部分结构信息。

### 17.2 内聚度只是“调用次数比值”

`cohesion_score` 的定义比较朴素，只考虑：

- 模块内函数对项目内其他函数的调用

它没有考虑：

- include 关系
- 全局变量共享
- 结构体共享强度
- 调用方向和权重

所以这个分数更像“轻量启发式指标”，不能把它当成严格的软件架构度量。

### 17.3 路径尾部匹配可能误伤同名文件

`_match_file()` 支持 `endswith("/" + module_file)`。  
如果项目里有不同目录下同名文件，且路径信息不规范，理论上存在误匹配风险。

### 17.4 有未使用的方法

[`_pick_primary_struct_key()`](D:/Code/Personal/tcode_win/src/agent/split.py#L154) 当前没有被任何地方调用。  
这通常说明作者中途改过设计，或者预留了后续优化点，但还没有接上线。

### 17.5 有未使用的参数

[`_identify_function_clusters()`](D:/Code/Personal/tcode_win/src/agent/split.py#L440) 的 `project_path` 参数在函数体里没有使用。  
`split()` 里传入的也是空字符串 `''`。这说明这个参数目前只是历史遗留接口。

### 17.6 对上游依赖图质量比较敏感

这份文件自身并不构建 `call_graph` 和 `struct_usage`，而是依赖上游输入。  
如果上游 `dependency_graph` 本身不准确，这里的聚类质量会直接下降。

尤其是 `SpecAgent._build_dependency_graph()` 的实现偏启发式，不是完整语义分析，所以 `split.py` 的效果上限实际上由上游决定。

## 18. 一句话总结这份代码的设计哲学

这份 `split.py` 不是在做严格的程序切片，也不是在做学术意义上的最优社区发现。  
它做的是一套“面向文档生成和模块理解”的工程化启发式拆分流程：

1. 先相信目录结构
2. 再用调用关系和结构体关系修正
3. 最后把过大的结果切成 LLM 可消费的函数簇

所以它的核心目标不是理论最优，而是：

> 以足够低的成本，把一个 C 项目拆成结构还算合理、规模还算可控、便于后续生成 spec 文档的中间单元。

## 19. 如果你接下来要继续改这个文件，建议优先看哪里

如果你是要维护或增强这份实现，我建议优先关注这几个点：

1. `_compute_module_cohesion()`  
   这里决定了“目录分组是否需要拆”，影响最大。
2. `_build_clusters_from_functions()`  
   这里决定“大模块如何切小”，是聚类质量核心。
3. `_identify_function_clusters()`  
   这里决定最终给下游文档生成喂什么粒度的上下文。
4. 上游 `SpecAgent._build_dependency_graph()`  
   如果依赖图质量差，改 `split.py` 的收益会很有限。

## 20. 简化版流程图

```text
project_info / project_analysis / dependency_graph
                |
                v
      _identify_candidate_modules
                |
                v
    按目录得到 candidate_modules
                |
                v
     _refine_modules_with_semantics
                |
                |-- 绑定 functions / structs
                |-- 计算 cohesion_score
                |-- 判断 needs_split
                |
                +-- 不需要拆 -> final module
                |
                +-- 需要拆 -> _split_module_by_clusters
                                 |
                                 +-- 按 struct 聚类
                                 +-- 按 prefix 聚类
                                 +-- 按 file_local 收尾
                |
                v
            module_units
                |
                v
      _identify_function_clusters
                |
                v
           cluster_units
```

---

如果你需要，我下一步可以继续补一版“按函数逐个讲”的说明，也可以直接把 `split.py` 里的每个方法加上中文注释版逐段解释。

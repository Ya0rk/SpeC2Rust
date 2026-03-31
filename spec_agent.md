# SpecAgent 工作机制分析

## 0. 说明

这份分析完全基于代码阅读，不依赖你本地是否跑过实验。  
结论主要来自 [`src/agent/spec_agent.py`](/E:/Code/C2R-Auto/cGrcode/src/agent/spec_agent.py) 和入口 [`src/agent/main.py`](/E:/Code/C2R-Auto/cGrcode/src/agent/main.py)。

如果只用一句话概括：

**SpecAgent 不是一个“全都交给大模型写文档”的 agent，而是一个“先做静态分析和模块划分，再把部分结果交给大模型补成 spec-kit 文档”的混合式流程。**

也就是说，它内部同时存在两种机制：

1. 规则/程序分析驱动
2. LLM 文档生成驱动

---

## 1. SpecAgent 在总流程中的位置

当入口使用：

```bash
python src/agent/main.py --use-spec-agent ...
```

主链路变成：

`SpecAgent -> RustAgent -> CodeFixer -> TestFixer`

如果再加上：

```bash
--use-spec-json-agent
```

则变成：

`SpecAgent -> SpecJsonAgent -> RustAgent -> CodeFixer -> TestFixer`

如果再加：

```bash
--use-pointer-agent
--use-macro-agent
```

那么 `PointerAgent` / `MacroAgent` 会以“可选的辅助分析层”插入 `SpecAgent` 内部流程。

---

## 2. SpecAgent 的核心目标

`SpecAgent` 的目标不是直接生成 Rust 代码，而是为 Rust 迁移准备一个中间层文档体系。  
这个中间层主要服务两件事：

1. 给人看  
   让研究者或开发者理解 C 工程被拆成了哪些模块，各模块做什么，哪些接口和行为值得保留。

2. 给后续 agent 看  
   让 `RustAgent`、`SpecJsonAgent`、修复器等拿到比“原始 C 源码全文”更浓缩、更结构化的上下文。

因此它本质上是在做：

**工程分析 -> 模块化压缩 -> 结构化文档输出**

---

## 3. SpecAgent 的整体执行顺序

按照 `analyze_and_generate_spec(...)` 的实现，当前流程大致如下：

### 第 0 步：初始化 spec-kit 目录骨架

调用：

- `specify_init(output_dir, model_type="qwen", script="sh")`

作用：

- 创建 spec-kit 风格的项目目录骨架

这一段：

- 不调用大模型
- 只是初始化目录结构

### 第 1 步：收集项目基础信息

调用：

- `_collect_project_info(project_path)`

收集内容包括：

- `project_name`
- `c_files`
- `h_files`
- `other_files`
- `readme_content`
- `build_system`
- `build_files`
- `executables`
- `libraries`
- `entry_files`

这一段：

- 不调用大模型
- 纯文件系统扫描和轻量启发式识别

### 第 2 步：解析 C 代码结构并构建依赖图

主要调用：

- `self.parser.analyze_directory(...)`
- `self.parser.get_project_analysis()`
- `_build_dependency_graph(...)`

输出包括：

- `project_analysis`
- `dependency_graph`

其中 `project_analysis` 里包含：

- 函数
- 结构体
- 宏
- 全局变量
- 文件路径映射

`dependency_graph` 则补充：

- `include_graph`
- `call_graph`
- `struct_usage`
- `file_symbols`

这一段：

- 不调用大模型
- 主要是 parser + 规则分析

### 第 3 步：模块划分

调用：

- `_split_modules(...)`
- 内部委托给 `ModuleSplitter.split(...)`

输出：

- `module_units`
- `cluster_units`

这是 `SpecAgent` 的关键前置步骤之一，因为后面的多数文档都围绕“模块”展开，而不是围绕单个函数展开。

这一段：

- 不调用大模型
- 完全依赖 `ModuleSplitter`

### 第 4 步：生成仓库地图

调用：

- `_generate_repo_manifest(...)`
- 内部使用 `_build_repo_manifest_content(...)`

输出：

- `docs/rewrite-context/00_repo_manifest.md`

这一段：

- 不调用大模型
- 直接基于扫描结果拼出事实型文档

### 第 5 步：生成子系统文档

调用：

- `_generate_subsystem_docs(...)`
- 内部使用 `_generate_module_summary(...)`
- 最终走 `_build_module_summary_content(...)`

输出：

- `docs/rewrite-context/01_subsystems/*.md`

注意这里的“每个文件”其实不是“每个 C 文件”，而是“每个模块一个 `.md` 文件”。

这一段：

- 当前版本不调用大模型
- 是规则化、模板化拼装出的模块摘要

### 第 6 步：生成横切面文档

这一层会生成三类最重要的全局文档：

1. `02_interfaces`
2. `03_behaviors`
3. `constitution.md`

它们的机制并不完全相同，下面分开说。

### 第 7 步：为每个模块生成 spec-kit 文档

这一层输出到：

- `specs/<index>-<module>-rust-port/`

每个模块目录里会生成：

- `spec.md`
- `plan.md`
- `tasks.md`

如果开启了 pointer / macro 辅助分析，还会额外写：

- `pointer.md`
- `macro.md`

这一层是最典型的“模块目录级组织”。

---

## 4. SpecAgent 最终会生成哪些目录和文件

当前输出结构可以理解为三层粒度。

### 4.1 项目级单文件

这类文件描述整个项目，只生成一个：

- `docs/rewrite-context/00_repo_manifest.md`
- `.specify/memory/constitution.md`

可选但当前主流程默认不生成：

- `docs/rewrite-context/04_gaps_and_risks.md`

### 4.2 项目级目录 + 模块级单文件

这类目录本身是项目级的，但目录内部是“每个模块一个 `.md`”：

- `docs/rewrite-context/01_subsystems/`
- `docs/rewrite-context/02_interfaces/`

其中：

- `01_subsystems/<module>.md`
- `02_interfaces/<index>_<module>.md`

另外 `02_interfaces/` 下还有一个项目级索引：

- `001_public_interfaces.md`

### 4.3 项目级目录 + 全局汇总文件

这一类目前主要是行为文档：

- `docs/rewrite-context/03_behaviors/001_behavior_specification.md`

如果模块很多，还会先生成批次摘要：

- `docs/rewrite-context/03_behaviors/batches/*.md`

然后再合成为最终总文档。

### 4.4 模块级目录

这一类是 spec-kit 目录：

- `specs/<index>-<module>-rust-port/`

每个模块一个目录，目录内再放多份文档：

- `spec.md`
- `plan.md`
- `tasks.md`
- 可选 `pointer.md`
- 可选 `macro.md`

这就是你看到“有时按文件夹组织，有时按文件组织”的根本原因。

---

## 5. 哪些步骤调用了大模型，哪些没有

这是理解 `SpecAgent` 的关键。

## 5.1 不调用大模型的部分

下面这些步骤本质上是规则分析或模板拼装：

1. `specify_init(...)`
2. `_collect_project_info(...)`
3. `self.parser.analyze_directory(...)`
4. `_build_dependency_graph(...)`
5. `_split_modules(...)`
6. `_generate_repo_manifest(...)`
7. `_generate_subsystem_docs(...)`
8. `_generate_module_summary(...)`
9. `_generate_interfaces_docs(...)`
10. `_generate_optional_translation_guides(...)` 中的 Pointer/Macro 结果收集
11. `_write_module_auxiliary_notes(...)`

也就是说，下面这些输出当前是“不依赖 LLM”的：

- `00_repo_manifest.md`
- `01_subsystems/*.md`
- `02_interfaces/*.md`
- `pointer.md`
- `macro.md`

### 为什么这几类不走 LLM

因为它们更偏“事实抽取”和“结构索引”。

这类文档如果交给 LLM 自由生成，容易出现：

- 幻觉头文件
- 幻觉接口
- 误判“空实现”
- 把“信息不足”说成“设计错误”

当前代码是在尽量避免这种问题。

## 5.2 调用了大模型的部分

当前 `SpecAgent` 中真正调用 `self.llm.generate(...)` 的地方主要有：

1. `03_behaviors`
   - 单轮行为文档生成
   - 或先批次摘要，再最终汇总

2. `constitution.md`
   - 项目级原则文档

3. `spec.md`
   - 每个模块一个

4. `plan.md`
   - 每个模块一个

5. `tasks.md`
   - 每个模块一个

6. 可选 `04_gaps_and_risks.md`
   - 当前主流程里默认注释掉了

因此，真正“强依赖 LLM”的输出主要是：

- `03_behaviors/`
- `.specify/memory/constitution.md`
- `specs/<module>/spec.md`
- `specs/<module>/plan.md`
- `specs/<module>/tasks.md`

---

## 6. 为什么有时按文件夹组织，有时按文件组织

这个现象不是混乱，而是当前设计故意做出的分层。

可以把 `SpecAgent` 的输出理解为三种不同目标。

### 6.1 项目总览类

例如：

- `00_repo_manifest.md`
- `constitution.md`

它们描述整个项目，所以天然是单文件。

### 6.2 横切索引类

例如：

- `01_subsystems/*.md`
- `02_interfaces/*.md`

这些文档是在“同一个视角下浏览所有模块”，所以最适合做成：

- 一个目录
- 每个模块一份文件

它们更像“从某个分析维度看项目”。

### 6.3 执行规格类

例如：

- `specs/<module>/spec.md`
- `plan.md`
- `tasks.md`

这类文档不只是描述，而是要作为后续迁移任务的执行单元，所以每个模块需要一个独立目录。

也就是说：

- `rewrite-context/` 更像“分析视图”
- `specs/<module>/` 更像“执行视图”

这是当前代码最重要的组织原则。

---

## 7. 各目录的真实职责

### 7.1 `docs/rewrite-context/00_repo_manifest.md`

职责：

- 给出仓库级地图
- 记录目录、文件、README、构建系统、入口文件

来源：

- 纯规则分析

用途：

- 帮后续 agent 建立项目全局感

### 7.2 `docs/rewrite-context/01_subsystems/`

职责：

- 描述模块摘要
- 告诉你每个模块的职责、文件边界、函数数量、结构体、调用关系等

来源：

- `ModuleSplitter` 结果 + 规则模板

用途：

- 帮助理解模块划分是否合理
- 为 behavior/spec 生成提供上游摘要

### 7.3 `docs/rewrite-context/02_interfaces/`

职责：

- 提取接口事实
- 补充头文件、函数、结构体、宏、全局变量等证据

来源：

- `project_analysis` + `dependency_graph`

用途：

- 给后续 Rust 迁移提供“接口面”的事实依据

### 7.4 `docs/rewrite-context/03_behaviors/`

职责：

- 把多个模块摘要进一步总结成行为规范

来源：

- 上游模块摘要 + LLM

用途：

- 给 `constitution` 和 `RustAgent` 提供行为层约束

### 7.5 `.specify/memory/constitution.md`

职责：

- 给整个项目制定迁移原则

来源：

- `project_info`
- `interfaces_doc`
- `behaviors_doc`
- LLM

用途：

- 提供顶层规则和治理约束

### 7.6 `specs/<module>/`

职责：

- 为每个模块提供具体迁移工作包

文件说明：

- `spec.md`
  - 说明该模块要迁移成什么
- `plan.md`
  - 说明如何迁移
- `tasks.md`
  - 说明拆成哪些任务
- `pointer.md`
  - 当前模块相关的指针迁移提示
- `macro.md`
  - 当前模块相关的宏迁移提示

来源：

- `spec/plan/tasks` 依赖 LLM
- `pointer/macro` 当前不依赖 LLM

---

## 8. SpecAgent 当前最重要的“中间单位”是什么

从代码看，`SpecAgent` 最核心的单位不是“单个 C 文件”，也不是“整个项目”，而是：

**module**

也就是 `module_units`。

这是理解所有输出组织方式的关键。

当前很多步骤都会先问一个问题：

> 这个项目应该被拆成哪些模块？

然后再围绕模块生成：

- 子系统摘要
- 接口事实
- 行为摘要
- spec
- plan
- tasks

所以你看到“有时按文件夹，有时按文件”，并不表示它同时在以“文件”为核心组织。  
更准确地说，它是在以“模块”为核心组织，只是不同文档层的承载方式不同：

- 有的用“每模块一个 `.md`”
- 有的用“每模块一个目录”

---

## 9. 当前代码里真正按“文件”处理的地方有哪些

严格来说，`SpecAgent` 里真正按文件处理的地方主要是事实收集，不是最终组织单位。

例如：

- 收集 `c_files` / `h_files`
- 从文件中提取 `include`
- 从文件映射中找头文件
- 从某个文件定位函数和结构体
- `PointerAgent` / `MacroAgent` 的条目天然带文件路径

但这些“文件级信息”最终多数会被提升为：

- 模块级摘要
- 接口索引
- 行为摘要
- 模块 spec 目录

所以它底层会大量依赖“文件级事实”，但对外展示更偏“模块级组织”。

---

## 10. 当前 SpecAgent 的优点

从代码角度看，它有几个明显优点。

### 10.1 不把所有东西都交给 LLM

很多最容易幻觉的部分都被固定成规则分析：

- 仓库地图
- 模块摘要
- 接口事实

这让输出更稳。

### 10.2 模块是中间层

不是直接“项目全文 -> Rust”，而是：

项目 -> 模块 -> 文档 -> 迁移任务

这更适合复杂工程。

### 10.3 文档层次清楚

它不是一个大杂烩文档，而是：

- 仓库地图
- 子系统
- 接口
- 行为
- constitution
- module spec-kit

这是比较适合研究和后续实验的结构。

---

## 11. 当前 SpecAgent 的局限

### 11.1 行为层仍然偏 LLM 摘要

`03_behaviors` 和 `constitution` 仍然主要依赖 LLM 总结。  
这意味着：

- 表达较灵活
- 但稳定性不如结构化 JSON

### 11.2 模块划分完全依赖 ModuleSplitter

`SpecAgent` 自身不判断模块边界。  
所以如果 `ModuleSplitter` 切得不好，后面的文档组织也会受影响。

### 11.3 文件级信息进入最终 spec 时仍有压缩损失

虽然底层收集了很多文件事实，但进入 `spec.md / plan.md / tasks.md` 时，还是会被 prompt 压缩。  
这对后续 Rust 迁移既是优点，也是信息损失来源。

### 11.4 当前输出混合了“人工可读”和“机器可消费”两种目标

它现在总体还是偏 markdown。  
如果后续更强调“给模型用”，那么 `SpecJsonAgent` 会越来越重要。

---

## 12. 一个简化结论

如果你现在想快速理解 `SpecAgent`，最值得记住的不是所有细节，而是这四句话：

1. `SpecAgent` 先做静态分析和模块划分，再生成文档。
2. 它不是全靠大模型，很多关键文档是规则拼装出来的。
3. 它的核心单位是“模块”，不是“单个文件”。
4. `rewrite-context` 更偏分析视图，`specs/<module>/` 更偏执行视图。

---

## 13. 可用于画图的图片提示词

### 图 1：SpecAgent 内部流程图

提示词：

> 绘制一个软件工程分析流程图，主题为 “How SpecAgent works”。流程包括：project scan, C parser, dependency graph, ModuleSplitter, repo manifest, subsystem docs, interface docs, behavior docs, constitution, per-module spec/plan/tasks。要求区分 rule-based steps 和 LLM-based steps，白底蓝灰配色，学术风格。

### 图 2：SpecAgent 输出目录结构图

提示词：

> 绘制一个目录树示意图，展示 SpecAgent 的输出结构：docs/rewrite-context/00_repo_manifest.md，01_subsystems/*.md，02_interfaces/*.md，03_behaviors/001_behavior_specification.md，.specify/memory/constitution.md，specs/<module>/spec.md, plan.md, tasks.md, optional pointer.md and macro.md。风格清晰简洁，适合研究报告。

### 图 3：项目级 / 模块级 / 文件级三层关系图

提示词：

> 绘制一个三层抽象图：顶层是 project-level documents，中层是 module-level organization，底层是 file-level facts。箭头从 file facts 指向 module summaries，再指向 project-level views and module spec directories。强调 SpecAgent is module-centric but built on file-level evidence。学术图风格，白底。


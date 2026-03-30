# src 总览

## 这是什么

`src` 是整个实验原型的主体，实现了一条面向 `C -> Rust` 迁移的 agent/harness 流水线。它的核心目标不是单纯“让模型直接翻译代码”，而是先把 C 工程压缩成多层文档和约束，再驱动模型生成 Rust，并借助编译器/测试输出来持续纠错。

主流程由 [`agent/main.py`](/E:/Code/C2R-Auto/cGrcode/src/agent/main.py) 串起：

1. 分析 C 项目，生成文档和 spec-kit 风格材料。
2. 根据文档生成 Rust 工程骨架和代码。
3. 用 `cargo fmt/check/build` 做编译修复。
4. 用 `cargo test` 做测试修复。

相比旧的 [`agent/c_doc_agent.py`](/E:/Code/C2R-Auto/cGrcode/src/agent/c_doc_agent.py)，当前更完整的方案是 [`agent/spec_agent.py`](/E:/Code/C2R-Auto/cGrcode/src/agent/spec_agent.py)：它把原始代码先切成模块和函数簇，再逐层压缩成 repo manifest、接口文档、行为文档、constitution、每模块 spec/plan/tasks。

## 目录分工

- [`agent`](/E:/Code/C2R-Auto/cGrcode/src/agent): 任务编排、C 文档生成、Rust 代码生成、编译/测试修复。
- [`config`](/E:/Code/C2R-Auto/cGrcode/src/config): 模型配置与 prompt 管理。
- [`llm`](/E:/Code/C2R-Auto/cGrcode/src/llm): LLM 适配层，当前主要对接本地 Qwen 服务。
- [`parse`](/E:/Code/C2R-Auto/cGrcode/src/parse): 基于 tree-sitter 的 C 静态分析与样例输出。
- [`utils`](/E:/Code/C2R-Auto/cGrcode/src/utils): shell、文档生成、颜色输出、specify 初始化等辅助模块。
- [`tests`](/E:/Code/C2R-Auto/cGrcode/src/tests): 目前只有 `SpecAgent` 的集成式试跑入口。
- [`.vscode`](/E:/Code/C2R-Auto/cGrcode/src/.vscode): 本地开发器配置。

## 核心设计思路

### 1. 先压缩，再生成

项目假设模型无法稳定消化整个 C 工程，因此先做一层“外部认知支架”：

- tree-sitter 抽取函数、结构体、宏、全局变量。
- 构建调用图、include 图、结构体使用关系。
- 按目录、调用关系、命名模式、共享结构体把大工程切成更小模块。
- 对模块再做接口/行为/迁移原则级别的文档压缩。

这样后续模型看到的不是全量源码，而是“被 harness 筛选过的上下文”。

### 2. harness 驱动的闭环纠错

Rust 生成不是一次完成，而是依次接受外部反馈：

- `cargo fmt` 暴露格式与基本语法问题。
- `cargo check` 暴露类型/借用/模块问题。
- `cargo build` 暴露链接与更完整的编译问题。
- `cargo test` 暴露语义行为偏差。

修复器把错误信息重新喂给模型，构成最小闭环。

### 3. 文档是中间产物，也是控制信号

该工程把“文档”当成控制模型行为的显式接口，而不只是说明材料：

- `repo manifest` 约束仓库边界。
- `interfaces` 约束可见 API 与数据结构。
- `behaviors` 压缩行为需求。
- `constitution` 给迁移设原则。
- 每模块 `spec / plan / tasks` 则把迁移拆成可执行单元。

## 工程里使用到的 trick / 技术分类

### A. 上下文压缩与认知脚手架

- `tree-sitter` 做结构化解析，而不是正则扫源码。
- 模块切分不是只看目录，还叠加调用图、结构体共用、命名前缀和行数阈值。
- 用“模块 -> 函数簇 -> 文件摘要 -> 模块摘要 -> 全局文档”的多层压缩，控制 prompt 长度。
- 在 [`agent/spec_agent.py`](/E:/Code/C2R-Auto/cGrcode/src/agent/spec_agent.py) 里显式设置多个字符上限，避免本地模型上下文爆掉。

### B. 约束式生成

- 用统一 prompt manager 管理不同 agent 的 system/user prompt。
- 要求输出特定标签，如 `<project_file>`、`<implementation_plan>`、`<new_files_to_generate>`，再由代码解析。
- 让模型先产出项目结构和实现计划，再逐文件生成代码，而不是直接一次生成全工程。
- `constitution/spec/plan/tasks` 把迁移目标外显成文本约束。

### C. harness 反馈回路

- 通过 `cargo fmt/check/build/test` 逐级暴露问题。
- 用错误消息反向定位待修文件。
- 让模型只修一个文件，缩小修改面。
- 把测试失败和编译失败拆成不同 fixer，分别使用不同 prompt。

### D. 面向弱模型的工程化补偿

- 通过外部分析器替模型完成结构抽取。
- 通过模块切分和 batch summary 代替“让模型自己总结全部仓库”。
- 通过固定模板和中文输出约束，降低自由生成带来的漂移。
- 通过 spec-kit 目录结构，把结果转换成后续 agent 可继续消费的工作流资产。

### E. 静态分析与关系建模

- `tree-sitter` 解析 AST。
- `networkx` 参与图结构输出。
- 记录函数调用、include 关系、结构体使用、全局变量和宏。
- 将路径规范化，兼容 Windows/Linux 路径差异。

## 当前状态与明显限制

- [`agent/spec_agent.py`](/E:/Code/C2R-Auto/cGrcode/src/agent/spec_agent.py) 是主力版本；[`agent/c_doc_agent.py`](/E:/Code/C2R-Auto/cGrcode/src/agent/c_doc_agent.py) 更像早期方案，粒度较粗。
- [`llm/openai/oai.py`](/E:/Code/C2R-Auto/cGrcode/src/llm/openai/oai.py) 基本未实现。
- [`utils/runtest.py`](/E:/Code/C2R-Auto/cGrcode/src/utils/runtest.py) 为空壳。
- [`utils/exception.py`](/E:/Code/C2R-Auto/cGrcode/src/utils/exception.py) 为空文件。
- [`agent/code_fixer_agent.py`](/E:/Code/C2R-Auto/cGrcode/src/agent/code_fixer_agent.py) 保留了多个 `input()`，说明它最初带有人工介入调试流程，不完全是无人值守 harness。
- [`llm/model.py`](/E:/Code/C2R-Auto/cGrcode/src/llm/model.py) 中模型名分支存在命名不一致，如 `qianwen14` 与 CLI 里的 `qwen14`。

## 阅读建议

建议按下面顺序读：

1. [`agent/main.py`](/E:/Code/C2R-Auto/cGrcode/src/agent/main.py)
2. [`agent/spec_agent.py`](/E:/Code/C2R-Auto/cGrcode/src/agent/spec_agent.py)
3. [`agent/split.py`](/E:/Code/C2R-Auto/cGrcode/src/agent/split.py)
4. [`parse/c_ast.py`](/E:/Code/C2R-Auto/cGrcode/src/parse/c_ast.py)
5. [`agent/rust_agent.py`](/E:/Code/C2R-Auto/cGrcode/src/agent/rust_agent.py)
6. [`agent/code_fixer_agent.py`](/E:/Code/C2R-Auto/cGrcode/src/agent/code_fixer_agent.py)
7. [`config/prompt.py`](/E:/Code/C2R-Auto/cGrcode/src/config/prompt.py)


研究分析
SpecAgent + ModuleSplitter + Fixer 的研究价值很高，核心不在“翻译模型多强”，而在“如何把弱模型包在一个外部认知与验证壳里”。

SpecAgent 的价值最大。它把原始 C 工程先转换成 repo manifest、接口事实、行为摘要、constitution、模块级 spec/plan/tasks。这相当于把“读代码”拆成“抽取事实 -> 压缩认知 -> 生成可执行约束”。这非常适合研究“模型不足时，harness 如何补偿”。

ModuleSplitter 是整个项目里最像论文贡献点的部分。它不是简单按目录切模块，而是混合了目录启发、调用关系、结构体共用、命名前缀、函数规模阈值。它的作用不是完美软件工程分层，而是控制 prompt 尺寸、提高上下文局部性、降低弱模型一次处理的复杂度。

Fixer 代表另一条关键思路：把编译器和测试器变成外部判别器。模型不必一次答对，而是通过fmt/check/build/test 逐层暴露错误，再用报错驱动局部修复。这是典型 harness-first，而不是 model-first。

可改进点

- SpecAgent 现在已经很强，但输出仍主要是自然语言文档。下一步更值得做的是把接口、行为、风险再结构化成机器可消费 JSON，而不是只给 markdown。
- ModuleSplitter 目前是启发式聚类，适合原型，但还缺少可解释评估指标。建议补三类评估：模块内调用密度、跨模块调用密度、文档长度/修复成功率相关性。
- Fixer 现在还是“报错定位到单文件后整文件重写”，修改面偏大。更强的版本应改成“先定位符号或函数，再最小补丁修复”。
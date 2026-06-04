# TLDR

本目录聚焦翻译前的 C 程序理解阶段：如何从源码中提取结构、模块、规格和高风险语义证据。

| 文件 | 讲什么 |
| --- | --- |
| [01_tree_sitter_static_analysis.md](01_tree_sitter_static_analysis.md) | 调研 tree-sitter 静态分析如何提取 C 函数、调用关系、结构体、宏、源码位置等事实，并说明兼容性补丁和局限。 |
| [02_module_splitter.md](02_module_splitter.md) | 分析 ModuleSplitter 如何根据目录、依赖、调用图和轻量内聚度把 C 项目切成可翻译模块，并控制 prompt 规模。 |
| [03_spec_agent_documents.md](03_spec_agent_documents.md) | 说明 SpecAgent 如何生成确定性文档、LLM 补充文档和 `translation_contract.json`，为后续生成提供机器可读边界。 |
| [04_pointer_macro_evidence.md](04_pointer_macro_evidence.md) | 总结 PointerAgent 和 MacroAgent 如何识别指针、数组、宏、条件编译等高风险迁移点，并生成项目级风险证据。 |

# TLDR

本目录汇总系统中的工程级优化与防护机制，适合写入论文的实现细节或威胁控制部分。

| 文件 | 讲什么 |
| --- | --- |
| [01_environment_isolation.md](01_environment_isolation.md) | 分析环境隔离和运行封装，包括脚本环境变量、工作目录、测试临时目录、API 配置和日志隔离。 |
| [02_context_deduplication.md](02_context_deduplication.md) | 总结上下文去重与材料预算策略，覆盖文档层、生成层、编译修复层和功能测试层的重复材料控制。 |
| [03_output_contracts_and_parsers.md](03_output_contracts_and_parsers.md) | 说明各 agent 的输出契约和解析器，包括 Markdown contract、生成 contract、修复 JSON contract 和协议异常处理。 |
| [04_safety_guards.md](04_safety_guards.md) | 汇总安全守卫与反作弊机制，例如路径边界、只读测试、防 stub、防硬编码 expected output、probe 与编辑互斥。 |

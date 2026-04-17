# StableRustAgent 说明

## 变更目的

旧的 `RustAgent` 在 `quadtree` 这类项目上反复出现结构性漂移：

- `lib.rs` 被 LLM 和修复器整文件自由改写
- `Cargo.toml` 与实际生成文件不一致
- 关闭测试时，源文件内联 `#[cfg(test)]` 仍持续引入噪声
- 修复器不断在核心边界文件上“修出新接口”

因此新增了一个更薄的默认生成器：`StableRustAgent`。

## 当前默认入口

`main.py` 默认已经切到：

- `agent.stable_rust_agent.StableRustAgent`

只有显式开启生长式生成时，才走：

- `GrowthRustAgent`

## 旧实现备份

旧文件已经备份为：

- `src/agent/rust_agent.py.bak`

## 新实现的核心策略

1. 让 LLM 负责：
   - 文件计划
   - 普通源码文件内容
   - README 文本

2. 本地强控高风险边界：
   - `Cargo.toml` 本地生成和依赖合并
   - `lib.rs` 本地重建
   - `generate_tests=false` 时剥掉源文件内联测试
   - 移除外层 fenced code
   - 清理 `thiserror` 包装残留
   - `tree.rs` 中 `root` 统一放宽为 `pub(crate)`

3. 继续支持：
   - `continue_mode`
   - `.cgr_generation_plan.json`

## 设计取向

`StableRustAgent` 不追求复杂提示和多阶段骨架流程，重点是：

- 文件边界稳定
- crate 入口稳定
- 本地处理易错包装问题
- 默认全量重建时不复用旧坏产物

## 2026-04-16 补充修正

- 文件计划 fallback 不再固定成 `quadtree` 风格的
  `point.rs / bounds.rs / node.rs / tree.rs / walk.rs`。
  现在会优先从输入文档里提取 `.c/.h` 文件名，推断出对应的 `src/*.rs`。
- `lib.rs` 本地重建不再硬编码 `Bounds / Point / QuadTree / WalkCallback`。
  现在统一采用：
  - `pub mod <module>;`
  - 只对真实存在且唯一的公开类型项做 `pub use`
- `code_fixer_agent.py` 中“本地重建最小 lib.rs”规则已同步为同样策略，
  不再把模块函数或不存在的类型强行提升到 crate 根。

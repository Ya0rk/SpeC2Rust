# parse 目录说明

## 职责

`parse` 负责把 C 项目从“原始源码”转换成结构化事实，是整个 harness 的起点。

## 文件说明

- [`c_ast.py`](/E:/Code/C2R-Auto/cGrcode/src/parse/c_ast.py): 基于 `tree-sitter` 的 C 静态分析器。
- [`res`](/E:/Code/C2R-Auto/cGrcode/src/parse/res): 解析输出样例与图文件。
- [`test_c_project`](/E:/Code/C2R-Auto/cGrcode/src/parse/test_c_project): 小型 C 示例工程，用于调试解析流程。

## `c_ast.py` 做了什么

[`c_ast.py`](/E:/Code/C2R-Auto/cGrcode/src/parse/c_ast.py) 是整个项目的“事实抽取器”，主要负责：

- 遍历 `.c` / `.h`
- 解析函数定义
- 提取全局变量
- 提取结构体
- 提取宏
- 建立函数调用关系
- 输出 JSON 结果

此外还会：

- 记录文件相对路径到绝对路径的映射
- 保存源码 span 信息
- 尝试额外处理 header 中的 inline function

## 这个目录在全工程中的位置

如果没有 `parse`，后续 `SpecAgent` 和 `RustAgent` 都只能直接读原始源码；而有了它，系统就能在模型之外完成第一轮结构化压缩。

这正是该项目区别于“纯 prompt 翻译”的基础。

## 当前注意点

- `c_ast.py` 在模块 import 阶段就调用 `Language.build_library(...)`，初始化成本较高。
- 解析器输出格式偏实验型，后续模块里有不少“字段归一化”逻辑专门兼容它。
- 这里不仅是 parser，也承担了一部分图构建与结果序列化职责。

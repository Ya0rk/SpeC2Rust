# utils 目录说明

## 职责

`utils` 放置一些基础辅助模块，服务于分析、文档生成、命令执行与外部工具接入。

## 文件说明

- [`cmd.py`](/E:/Code/C2R-Auto/cGrcode/src/utils/cmd.py): 统一 shell 命令执行。
- [`code_analyzer.py`](/E:/Code/C2R-Auto/cGrcode/src/utils/code_analyzer.py): 早期按模块整块喂给 LLM 的代码分析器。
- [`document_generator.py`](/E:/Code/C2R-Auto/cGrcode/src/utils/document_generator.py): 早期文档生成器，根据模块分析结果写模块文档和项目总文档。
- [`fmtpr.py`](/E:/Code/C2R-Auto/cGrcode/src/utils/fmtpr.py): 彩色终端输出。
- [`spec.py`](/E:/Code/C2R-Auto/cGrcode/src/utils/spec.py): 包装 `specify init`。
- [`runtest.py`](/E:/Code/C2R-Auto/cGrcode/src/utils/runtest.py): 占位。
- [`exception.py`](/E:/Code/C2R-Auto/cGrcode/src/utils/exception.py): 空文件。
- [`tree-sitter`](/E:/Code/C2R-Auto/cGrcode/src/utils/tree-sitter): parser 依赖产物目录。

## 在系统中的位置

这个目录里同时存在两类东西：

- 真正被主流程使用的基础工具，如 `cmd.py`、`spec.py`、`fmtpr.py`
- 早期方案遗留工具，如 `code_analyzer.py`、`document_generator.py`

因此这里既是“工具层”，也是项目演化历史的缩影。

## 关键文件解读

### `cmd.py`

提供统一的 `run(command)`：

- 成功返回 `None`
- 失败返回 stderr 文本

这种设计很简陋，但它让上层可以把“有无报错”当作布尔信号使用。

### `spec.py`

调用外部 `specify init` 命令，为输出目录初始化 spec-kit 风格结构。它把外部工作流资产接入到了当前项目中。

### `code_analyzer.py` / `document_generator.py`

这是旧一代分析链路：

- 直接按模块收集整块源码
- 用 LLM 做分析
- 用 LLM 合成文档

后来 `SpecAgent + ModuleSplitter + AST 结构化压缩` 实际上是对这条旧路径的增强版替代。

## 当前风险

- `cmd.py` 以 `None` 表示成功，不够直观。
- `runtest.py`、`exception.py` 尚未实现。
- 新旧方案并存，容易让接手者误以为所有工具都还在主路径中。

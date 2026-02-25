# C项目解析Agent使用说明

## 项目简介

本Agent用于解析C项目，分析各个模块的功能和设计意图，并生成详细的项目文档。Agent使用本地的Qwen LLM模型来分析代码和生成文档。

## 功能特性

- 解析C项目结构，识别文件、函数、结构体等
- 分析各个模块的功能和设计意图
- 生成详细的模块文档和项目总文档
- 支持多个C项目的分析
- 自动生成README文件

## 目录结构

```
agent/
├── main.py          # Agent核心代码
└── README.md        # 使用说明

utils/
├── c_parser.py      # C项目解析工具
├── code_analyzer.py # 代码分析工具
└── document_generator.py # 文档生成工具

config/
└── config.py        # 配置管理

llm/
├── models.py        # 模型异常定义
├── qianwen/         # 本地Qwen模型接口
└── openai/          # OpenAI模型接口

scripts/
└── qwen.sh          # Qwen模型启动脚本

datasets/            # 测试数据集
├── avl-tree/        # AVL树项目
└── md5-c/           # MD5哈希项目

doc/                 # 生成的文档
```

## 环境要求

- Python 3.8+
- 本地Qwen模型服务
- 必要的Python包：
  - requests

## 安装与使用

### 1. 启动Qwen模型服务

首先需要启动本地的Qwen模型服务：

```bash
# 启动7B模型
./scripts/qwen.sh 7

# 或者启动14B模型
./scripts/qwen.sh 14
```

### 2. 运行Agent

使用以下命令运行Agent来分析C项目：

```bash
python agent/main.py <项目路径> <输出目录>
```

例如：

```bash
# 分析avl-tree项目
python agent/main.py datasets/avl-tree doc/avl-tree

# 分析md5-c项目
python agent/main.py datasets/md5-c doc/md5-c
```

## 生成的文档结构

对于每个项目，Agent会生成以下文档：

- `project_overview.md`：项目总文档，包括项目概述、功能、架构等
- `root_module.md`：根模块文档，分析项目的核心功能
- `README.md`：项目简介和使用说明

## 配置选项

Agent的配置文件位于`config/config.py`，可以修改以下配置：

- `api_key`：Qwen模型服务的API密钥
- `model`：使用的模型名称

## 注意事项

1. 确保Qwen模型服务已经启动并且可以正常访问
2. 对于大型项目，分析和文档生成可能需要较长时间
3. 生成的文档质量取决于模型的能力和项目的复杂度
4. 在后序工作中，可以利用生成的文档进行Rust代码生成

## 示例

### 分析AVL树项目

```bash
python agent/main.py datasets/avl-tree doc/avl-tree
```

生成的文档会保存在`doc/avl-tree/avl-tree`目录中。

### 分析MD5项目

```bash
python agent/main.py datasets/md5-c doc/md5-c
```

生成的文档会保存在`doc/md5-c/md5-c`目录中。

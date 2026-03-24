### 目录介绍

```
agent: 智能体相关代码
build、parse、vender: 目录树解析相关代码
output: 智能体生成的文档和代码保存路径
doc: 一些文档
example: 暂时无用
llm: 与大模型相关的代码，目前调用的是本地启动的qianwen模型
script: 一些脚本，启动本地大模型脚本
utils: 一些工具代码
test: 测试
utils: 一些工具代码，比如c项目的目录树解析、文档生成等
requirement.txt: 项目依赖的python包，目前只是部分，后续会根据需要添加
```


### 运行环境

Python 3.12.12 使用的miniconda启动的虚拟环境

### 运行步骤

1. 启动本地qianwen模型
```
./script/qwen.sh
```
2. 运行agent/main.py
```
python agent/main.py [c项目目录] [输出文档目录]
```

e.g.
```
python agent/main.py datasets/avl-tree doc/avl-tree-ast
```

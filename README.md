### 目录介绍

agent: 智能体相关代码
build、parse、vender: 目录树解析相关代码，但是目前用的不是这个；用的是utils/c_parser.py
doc: 一些agent解析c项目生成的文档，可以在里面添加我们公共文档，比如api文档、设计文档等
example: 暂时无用
llm: 与大模型相关的代码，目前调用的是本地启动的qianwen模型
script: 一些脚本，启动本地大模型脚本
utils: 一些工具代码，比如c项目的目录树解析、大模型调用等
test: 测试代码生成，但是目前还没用
utils: 一些工具代码，比如c项目的目录树解析、文档生成等
requirement.txt: 项目依赖的python包，目前只是部分，后续会根据需要添加


### 运行环境

Python 3.12.12 使用的miniconda启动的虚拟环境

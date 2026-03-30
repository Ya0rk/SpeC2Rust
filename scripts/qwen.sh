#!/bin/bash

# 检查是否提供了模型大小参数
if [ $# -ne 1 ]; then
    echo "用法: $0 <模型大小>"
    echo "例如: $0 3 (使用3B模型) 或 $0 7 (使用7B模型)"
    exit 1
fi

# 检查参数是否为有效的模型大小
if [[ "$1" != "3" && "$1" != "7" && "$1" != "14" && "$1" != "32" ]]; then
    echo "错误: 仅支持 3、7、14和32 作为模型大小参数"
    exit 1
fi

export CUDA_VISIBLE_DEVICES=0,1,2,3
MODEL_NAME="Qwen2.5-Coder-${1}B-Instruct"

echo "启动模型: $MODEL_NAME"
vllm serve /data1/jfeng/models/$MODEL_NAME\
    --api-key tcode-12345\
    --max-model-len 32768\
    --tensor-parallel-size 4\
    --served-model-name $MODEL_NAME\
    --trust-remote-code\
    --dtype float16 \
    --port 8080 \
    --host 0.0.0.0
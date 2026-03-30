#!/bin/bash

# 检查是否提供了模型大小参数
if [ $# -ne 1 ]; then
    echo "用法: $0 <模型大小>"
    echo "例如: $0 14"
    exit 1
fi

# 设置使用的显卡为 2 和 3（因为 0 和 1 已经被占用）
export CUDA_VISIBLE_DEVICES=2,3

MODEL_NAME="Qwen2.5-Coder-${1}B-Instruct"

echo "启动模型: $MODEL_NAME"

# 修改点：
# 1. --tensor-parallel-size 改为 2 (对应两张显卡)
# 2. --gpu-memory-utilization 建议设为 0.9 或 0.95 确保 14B 模型有足够显存
# 3. V100 不支持 bfloat16，务必保留 --dtype float16

vllm serve /data1/jfeng/models/$MODEL_NAME \
    --api-key tcode-12345 \
    --max-model-len 32768 \
    --tensor-parallel-size 2 \
    --served-model-name $MODEL_NAME \
    --trust-remote-code \
    --dtype float16 \
    --port 8080 \
    --host 0.0.0.0
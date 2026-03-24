#!/bin/bash

# 根据 AVL Tree 项目文档生成 Rust 实现

# 设置路径
PROJECT_NAME="avl_tree_test"
OUTPUT_DIR="/data1/jfeng/tcode/test/avl_tree_test/rust"
DOC_DIR="/data1/jfeng/tcode/doc/avl-tree-32B"

# 文档路径
DOC_FILES=(
    "${DOC_DIR}/final_project_overview.md"
    "${DOC_DIR}/avl-tree"
)

# 模型大小（可选：7, 14, 32, 72）
MODEL_SIZE="7"

echo "=========================================="
echo "根据 AVL Tree 文档生成 Rust 实现"
echo "=========================================="
echo "项目名称：${PROJECT_NAME}"
echo "输出目录：${OUTPUT_DIR}"
echo "文档目录：${DOC_DIR}"
echo "模型大小：${MODEL_SIZE}B"
echo "=========================================="

# 运行 agent
python3 /data1/jfeng/tcode/agent/rust_doc_main.py \
    "${PROJECT_NAME}" \
    "${OUTPUT_DIR}" \
    "${DOC_FILES[@]}" \
    --model_size "${MODEL_SIZE}" \
    --verbose

echo "=========================================="
echo "完成！"
echo "=========================================="

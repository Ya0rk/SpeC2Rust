#!/bin/bash

if [ $# -ne 1 ]; then
    echo "用法：$0 <项目名称>"
    echo "例如：$0 avl-tree，会自动根据项目名称到 dataset 目录下匹配项目"
    exit 1
fi


# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# 参数
C_NAME=$1
DATASET=datasets/$C_NAME
OUTPUT_DIR=output/$C_NAME

# 创建日志目录
LOG_DIR=log
mkdir -p "$LOG_DIR"

# 生成日志文件名（年月日时分格式）
LOG_FILE="$LOG_DIR/$(date +%Y%m%d%H%M).log"

echo -e "${CYAN}========================================${NC}"
echo -e "${YELLOW}项目名称:${NC} ${BLUE}$C_NAME${NC}"
echo -e "${YELLOW}数据集路径:${NC} ${BLUE}$DATASET${NC}"
echo -e "${YELLOW}输出目录:${NC} ${BLUE}$OUTPUT_DIR${NC}"
echo -e "${YELLOW}日志文件:${NC} ${BLUE}$LOG_FILE${NC}"
echo -e "${CYAN}========================================${NC}"

echo -e "\n${GREEN}Rust 项目名称：${NC}${BLUE}$C_NAME-rust${NC}"

# 使用 tee 命令同时输出到终端和日志文件，使用 python -u 确保输出不被缓冲
python -u ./src/agent/main.py \
    --model-name "qwen32" \
    --c_project_path "$DATASET" \
    --output_dir "$OUTPUT_DIR" \
    --rust-project-name "$C_NAME-rust" \
    2>&1 | tee -a "$LOG_FILE"

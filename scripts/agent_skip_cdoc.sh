#!/bin/bash

if [ $# -ne 1 ]; then
    echo "用法: $0 <项目名称>"
    echo "例如: $0 avl-tree，会自动根据项目名称到dataset目录下匹配项目"
    exit 1
fi


# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# 创建日志目录
LOG_DIR=log
mkdir -p "$LOG_DIR"

# 生成日志文件名（年月日时分格式）
LOG_FILE="$LOG_DIR/$(date +%Y%m%d%H%M_skip_cdoc).log"

# 参数
C_NAME=$1
DATASET=datasets/$C_NAME
OUTPUT_DIR=output/$C_NAME

echo -e "${CYAN}========================================${NC}"
echo -e "${YELLOW}项目名称:${NC} ${BLUE}$C_NAME${NC}"
echo -e "${YELLOW}数据集路径:${NC} ${BLUE}$DATASET${NC}"
echo -e "${YELLOW}输出目录:${NC} ${BLUE}$OUTPUT_DIR${NC}"
echo -e "${YELLOW}日志文件:${NC} ${BLUE}$LOG_FILE${NC}"
echo -e "${CYAN}========================================${NC}"

echo -e "\n${GREEN}Rust 项目名称：${NC}${BLUE}$C_NAME-rust${NC}"


python -u ./src/agent/main.py \
    --model-name "qwen32" \
    --c_project_path "$DATASET" \
    --output_dir "$OUTPUT_DIR" \
    --rust-project-name "$C_NAME-rust" \
    --skip-c-analysis
    2>&1 | tee -a "$LOG_FILE"
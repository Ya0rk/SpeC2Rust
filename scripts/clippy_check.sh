#!/usr/bin/env bash
# clippy_check.sh - 对 Rust 项目运行 Clippy + Rustfmt 检查，打印结果并输出 JSON 报告
#
# 用法:
#   ./clippy_check.sh <rust项目路径>
#
# 输出:
#   终端: 统计摘要 + 分类详情
#   文件:
#     <项目路径>/clippy_report.json
#     <项目路径>/clippy_output.log
#     <项目路径>/rustfmt_output.log

set -uo pipefail

usage() {
    echo "用法: $0 <rust项目路径>"
}

require_command() {
    local cmd="$1"
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "错误: 未找到命令: $cmd"
        exit 1
    fi
}

json_escape() {
    local s="${1-}"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    s="${s//$'\n'/\\n}"
    s="${s//$'\r'/\\r}"
    s="${s//$'\t'/\\t}"
    printf '%s' "$s"
}

trim_trailing_space() {
    local s="${1-}"
    s="${s%"${s##*[![:space:]]}"}"
    printf '%s' "$s"
}

relative_to_project() {
    local path="${1-}"
    if [ -z "$path" ]; then
        printf ''
        return
    fi

    case "$path" in
        "$PROJECT_DIR"/*) printf '%s' "${path#"$PROJECT_DIR"/}" ;;
        *) printf '%s' "$path" ;;
    esac
}

infer_category() {
    local code="${1-}"
    local msg="${2-}"

    if [ -n "$code" ]; then
        if [[ "$code" == *"::"* ]]; then
            printf '%s' "${code##*::}"
        else
            printf '%s' "$code"
        fi
        return
    fi

    if [[ "$msg" =~ never\ (used|read|constructed) ]]; then
        printf 'unused'
    elif [[ "$msg" =~ (unused|unnecessary) ]]; then
        printf 'unused'
    elif [[ "$msg" =~ deprecated ]]; then
        printf 'deprecated'
    elif [[ "$msg" =~ (should|could).*(be|use) ]]; then
        printf 'style'
    else
        printf 'other'
    fi
}

should_skip_clippy_line() {
    local line="${1-}"
    [[ "$line" == *".cargo/config"* && "$line" == *"deprecated in favor of `config.toml`"* ]]
}

if [ $# -ne 1 ]; then
    usage
    exit 1
fi

require_command cargo

if ! cargo clippy --version >/dev/null 2>&1; then
    echo "错误: 当前环境缺少 Clippy，请先运行: rustup component add clippy"
    exit 1
fi

if ! cargo fmt --version >/dev/null 2>&1; then
    echo "错误: 当前环境缺少 Rustfmt，请先运行: rustup component add rustfmt"
    exit 1
fi

PROJECT_DIR_INPUT="$1"

if [ ! -d "$PROJECT_DIR_INPUT" ]; then
    echo "错误: 目录不存在: $PROJECT_DIR_INPUT"
    exit 1
fi

PROJECT_DIR="$(cd "$PROJECT_DIR_INPUT" && pwd -P)"

if [ ! -f "$PROJECT_DIR/Cargo.toml" ]; then
    echo "错误: 未找到 Cargo.toml，不是有效的 Rust 项目: $PROJECT_DIR"
    exit 1
fi

PROJECT_NAME="$(basename "$PROJECT_DIR")"
REPORT_FILE="$PROJECT_DIR/clippy_report.json"
CLIPPY_LOG="$PROJECT_DIR/clippy_output.log"
RUSTFMT_LOG="$PROJECT_DIR/rustfmt_output.log"

echo "================================================================================"
echo "  Clippy + Rustfmt 风格检查"
echo "  项目: $PROJECT_NAME"
echo "  路径: $PROJECT_DIR"
echo "================================================================================"

echo ""
echo "[1/2] 运行 cargo clippy ..."
echo "----------------------------------------"

(
    cd "$PROJECT_DIR" &&
    cargo clippy --workspace --all-targets --message-format short
) >"$CLIPPY_LOG" 2>&1
clippy_exit_code=$?

declare -A categories=()
declare -A category_examples=()
declare -a category_order=()
declare -a clippy_warning_lines=()
declare -a clippy_error_lines=()

total_warnings=0
total_errors=0
clippy_details_json="["
first_detail=true
clippy_error_details_json="["
first_error_detail=true

while IFS= read -r line; do
    [ -z "$line" ] && continue
    if should_skip_clippy_line "$line"; then
        continue
    fi

    file_path=""
    line_no=0
    message_block=""
    severity=""

    if [[ "$line" =~ ^(.+):([0-9]+):([0-9]+):[[:space:]]+(warning|error):[[:space:]]+(.*)$ ]]; then
        file_path="${BASH_REMATCH[1]}"
        line_no="${BASH_REMATCH[2]}"
        severity="${BASH_REMATCH[4]}"
        message_block="${BASH_REMATCH[5]}"
    elif [[ "$line" =~ ^(warning|error):[[:space:]]+(.*)$ ]]; then
        severity="${BASH_REMATCH[1]}"
        message_block="${BASH_REMATCH[2]}"
    elif [[ "$line" =~ ^error(\[[A-Z0-9]+\])?:[[:space:]]+(.*)$ ]]; then
        severity="error"
        message_block="${BASH_REMATCH[2]}"
    elif [[ "$line" =~ ^warning(\[[A-Z0-9]+\])?:[[:space:]]+(.*)$ ]]; then
        severity="warning"
        message_block="${BASH_REMATCH[2]}"
    else
        continue
    fi

    code=""
    message="$message_block"
    if [[ "$message_block" =~ ^(.*)[[:space:]]\[([A-Za-z0-9_:-]+)\]$ ]]; then
        message="$(trim_trailing_space "${BASH_REMATCH[1]}")"
        code="${BASH_REMATCH[2]}"
    fi
    if [ "$severity" = "error" ] && [[ "$message" =~ ^could\ not\ compile\  ]]; then
        continue
    fi

    rel_file="$(relative_to_project "$file_path")"
    if [ -n "$rel_file" ]; then
        rendered_line="$rel_file:$line_no: $message"
    else
        rendered_line="$message"
    fi
    if [ -n "$code" ]; then
        rendered_line="$rendered_line [$code]"
    fi

    if [ "$severity" = "warning" ]; then
        total_warnings=$((total_warnings + 1))
        category="$(infer_category "$code" "$message")"
        categories["$category"]=$(( ${categories["$category"]:-0} + 1 ))

        seen=false
        for existing in "${category_order[@]}"; do
            if [ "$existing" = "$category" ]; then
                seen=true
                break
            fi
        done
        if ! $seen; then
            category_order+=("$category")
        fi

        example_count="${category_examples["${category}_count"]:-0}"
        if [ "$example_count" -lt 3 ]; then
            category_examples["${category}_${example_count}"]="$(printf '%.80s' "$message")"
            category_examples["${category}_count"]=$((example_count + 1))
        fi

        clippy_warning_lines+=("$rendered_line")

        if $first_detail; then
            first_detail=false
        else
            clippy_details_json+=","
        fi
        clippy_details_json+="{\"file\":\"$(json_escape "$rel_file")\",\"line\":$line_no,\"lint\":\"$(json_escape "$code")\",\"message\":\"$(json_escape "$message")\"}"
    elif [ "$severity" = "error" ]; then
        total_errors=$((total_errors + 1))
        clippy_error_lines+=("$rendered_line")

        if $first_error_detail; then
            first_error_detail=false
        else
            clippy_error_details_json+=","
        fi
        clippy_error_details_json+="{\"file\":\"$(json_escape "$rel_file")\",\"line\":$line_no,\"code\":\"$(json_escape "$code")\",\"message\":\"$(json_escape "$message")\"}"
    fi
done < "$CLIPPY_LOG"
clippy_details_json+="]"
clippy_error_details_json+="]"

echo ""
echo "[2/2] 运行 cargo fmt --all -- --check ..."
echo "----------------------------------------"

(
    cd "$PROJECT_DIR" &&
    cargo fmt --all -- --check
) >"$RUSTFMT_LOG" 2>&1
rustfmt_exit_code=$?

declare -A rustfmt_seen_files=()
declare -a rustfmt_file_order=()
rustfmt_details_json="["
first_fmt=true
rustfmt_issue_files=0

while IFS= read -r line; do
    [ -z "$line" ] && continue

    if [[ "$line" =~ ^Diff\ in\ (.+):([0-9]+):$ ]]; then
        diff_file="$(relative_to_project "${BASH_REMATCH[1]}")"
        diff_line="${BASH_REMATCH[2]}"

        if [ -z "${rustfmt_seen_files["$diff_file"]+x}" ]; then
            rustfmt_seen_files["$diff_file"]=1
            rustfmt_file_order+=("$diff_file")
            rustfmt_issue_files=$((rustfmt_issue_files + 1))
        fi

        if $first_fmt; then
            first_fmt=false
        else
            rustfmt_details_json+=","
        fi
        rustfmt_details_json+="{\"file\":\"$(json_escape "$diff_file")\",\"line\":$diff_line}"
    fi
done < "$RUSTFMT_LOG"
rustfmt_details_json+="]"

clippy_failed=0
if [ "$clippy_exit_code" -ne 0 ]; then
    clippy_failed=1
fi

rustfmt_failed=0
if [ "$rustfmt_exit_code" -ne 0 ] && [ "$rustfmt_issue_files" -eq 0 ]; then
    rustfmt_failed=1
fi

tool_errors=0
if [ "$clippy_failed" -eq 1 ] && [ "$total_errors" -eq 0 ]; then
    tool_errors=$((tool_errors + 1))
fi
if [ "$rustfmt_failed" -eq 1 ]; then
    tool_errors=$((tool_errors + 1))
fi
total_issues=$((total_warnings + total_errors + rustfmt_issue_files + tool_errors))

category_json="{"
first_cat=true
for category in "${category_order[@]}"; do
    if $first_cat; then
        first_cat=false
    else
        category_json+=","
    fi
    category_json+="\"$(json_escape "$category")\":${categories["$category"]}"
done
category_json+="}"

timestamp="$(date -Iseconds)"

cat > "$REPORT_FILE" <<JSONEOF
{
  "project": "$(json_escape "$PROJECT_NAME")",
  "path": "$(json_escape "$PROJECT_DIR")",
  "timestamp": "$(json_escape "$timestamp")",
  "summary": {
    "total_issues": $total_issues,
    "clippy_warnings": $total_warnings,
    "clippy_errors": $total_errors,
    "rustfmt_files_needing_format": $rustfmt_issue_files,
    "tool_errors": $tool_errors
  },
  "commands": {
    "clippy": "cargo clippy --workspace --all-targets --message-format short",
    "rustfmt": "cargo fmt --all -- --check"
  },
  "status": {
    "clippy_exit_code": $clippy_exit_code,
    "rustfmt_exit_code": $rustfmt_exit_code,
    "clippy_failed": $clippy_failed,
    "rustfmt_failed": $rustfmt_failed
  },
  "artifacts": {
    "clippy_log": "$(json_escape "$CLIPPY_LOG")",
    "rustfmt_log": "$(json_escape "$RUSTFMT_LOG")"
  },
  "clippy_by_category": $category_json,
  "clippy_details": $clippy_details_json,
  "clippy_error_details": $clippy_error_details_json,
  "rustfmt_details": $rustfmt_details_json
}
JSONEOF

echo ""
echo "================================================================================"
echo "  检查结果汇总"
echo "================================================================================"
echo "  Clippy 错误总数     : $total_errors"
echo "  Clippy 警告总数     : $total_warnings"
echo "  Rustfmt 待格式化文件: $rustfmt_issue_files"
echo "  工具执行错误数      : $tool_errors"
echo "  问题总计            : $total_issues"
echo ""

if [ "$total_errors" -gt 0 ]; then
    echo "  Clippy 错误详情 (前 30 条):"
    echo "  ----------------------------------------"
    max_error_show=30
    for ((i = 0; i < ${#clippy_error_lines[@]} && i < max_error_show; i++)); do
        echo "    ${clippy_error_lines[$i]}"
    done
    if [ "${#clippy_error_lines[@]}" -gt "$max_error_show" ]; then
        echo "    ... 还有 $((${#clippy_error_lines[@]} - max_error_show)) 条，详见 JSON 报告"
    fi
    echo ""
fi

if [ "$total_warnings" -gt 0 ]; then
    echo "  Clippy 分类统计:"
    for category in "${category_order[@]}"; do
        printf "    %-25s : %3d\n" "$category" "${categories["$category"]}"
    done
    echo ""
    echo "  Clippy 警告详情 (前 30 条):"
    echo "  ----------------------------------------"
    max_show=30
    for ((i = 0; i < ${#clippy_warning_lines[@]} && i < max_show; i++)); do
        echo "    ${clippy_warning_lines[$i]}"
    done
    if [ "${#clippy_warning_lines[@]}" -gt "$max_show" ]; then
        echo "    ... 还有 $((${#clippy_warning_lines[@]} - max_show)) 条，详见 JSON 报告"
    fi
fi

if [ "$rustfmt_issue_files" -gt 0 ]; then
    echo ""
    echo "  Rustfmt 需格式化的文件:"
    for diff_file in "${rustfmt_file_order[@]}"; do
        echo "    $diff_file"
    done
fi

if [ "$clippy_failed" -eq 1 ]; then
    echo ""
    echo "  Clippy 执行失败，通常表示项目存在编译错误或被 deny 的 lint。"
    echo "  详情日志: $CLIPPY_LOG"
fi

if [ "$rustfmt_failed" -eq 1 ]; then
    echo ""
    echo "  Rustfmt 执行失败，通常表示语法错误、宏展开问题或 rustfmt 组件异常。"
    echo "  详情日志: $RUSTFMT_LOG"
fi

if [ "$total_issues" -eq 0 ]; then
    echo "  ✓ 无任何问题。"
fi

echo ""
echo "================================================================================"
echo "  JSON 报告已保存: $REPORT_FILE"
echo "  Clippy 日志已保存: $CLIPPY_LOG"
echo "  Rustfmt 日志已保存: $RUSTFMT_LOG"
echo "================================================================================"

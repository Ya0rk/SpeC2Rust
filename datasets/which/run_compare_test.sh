#!/usr/bin/env bash

set -u

failed=0
case_count=0

ROOT_DIR="$(pwd)"
C_BIN="${C_BIN:-$ROOT_DIR/which}"
RUST_BIN="${RUST_BIN:-$ROOT_DIR/../../output/which/which-rust/target/debug/which-rust}"
RESULTS_DIR="${RESULTS_DIR:-$ROOT_DIR/compare_results}"
BIN_DIR="$RESULTS_DIR/.bins"

mkdir -p "$RESULTS_DIR" "$BIN_DIR/c" "$BIN_DIR/rust"

abs_path() {
    case "$1" in
        /*) printf '%s\n' "$1" ;;
        *) printf '%s/%s\n' "$ROOT_DIR" "$1" ;;
    esac
}

install_bin() {
    local src="$1"
    local dst="$2"

    if [[ ! -f "$src" ]]; then
        echo "Missing binary: $src" >&2
        return 1
    fi

    ln -sf "$src" "$dst" 2>/dev/null || cp -f "$src" "$dst"
    chmod +x "$dst" 2>/dev/null || true
}

normalize_file() {
    tr -d '\r' < "$1" \
        | sed 's/[[:space:]]*$//' \
        | sed 's|/bin/|/usr/bin/|g' \
        | sed "s|$BIN_DIR/c|<bin>|g" \
        | sed "s|$BIN_DIR/rust|<bin>|g"
}

run_one() {
    local bin_dir="$1"
    local output_file="$2"
    local path_override="$3"
    shift 3

    if [[ "$path_override" == "__DEFAULT_PATH__" ]]; then
        (cd "$bin_dir" && ./which "$@" > "$output_file" 2>&1)
    else
        (cd "$bin_dir" && PATH="$path_override" ./which "$@" > "$output_file" 2>&1)
    fi
}

run_case() {
    local test_num="$1"
    local description="$2"
    local path_override="$3"
    shift 3
    case_count=$((case_count + 1))

    local c_raw="$RESULTS_DIR/test${test_num}_c.raw"
    local rust_raw="$RESULTS_DIR/test${test_num}_rust.raw"
    local c_norm="$RESULTS_DIR/test${test_num}_c.norm"
    local rust_norm="$RESULTS_DIR/test${test_num}_rust.norm"
    local log_file="$RESULTS_DIR/test${test_num}.log"

    run_one "$BIN_DIR/c" "$c_raw" "$path_override" "$@"
    local c_code=$?
    run_one "$BIN_DIR/rust" "$rust_raw" "$path_override" "$@"
    local rust_code=$?

    normalize_file "$c_raw" > "$c_norm"
    normalize_file "$rust_raw" > "$rust_norm"

    {
        echo "Test #$test_num: $description"
        echo "Args: $*"
        if [[ "$path_override" != "__DEFAULT_PATH__" ]]; then
            echo "PATH: $path_override"
        fi
        echo "C exit code: $c_code"
        echo "Rust exit code: $rust_code"
        echo
        echo "[C output]"
        cat "$c_norm"
        echo
        echo "[Rust output]"
        cat "$rust_norm"
    } > "$log_file"

    if [[ "$c_code" == "$rust_code" ]] && cmp -s "$c_norm" "$rust_norm"; then
        echo "Test $test_num passed: $description"
        echo "Status: PASSED" >> "$log_file"
    else
        echo "Test $test_num failed: $description" >&2
        echo "Status: FAILED" >> "$log_file"
        failed=1
    fi
}

C_BIN="$(abs_path "$C_BIN")"
RUST_BIN="$(abs_path "$RUST_BIN")"

if ! install_bin "$C_BIN" "$BIN_DIR/c/which"; then
    exit 2
fi

if ! install_bin "$RUST_BIN" "$BIN_DIR/rust/which"; then
    echo "Build the Rust binary in the same OS environment first." >&2
    echo "For WSL/Linux: cd ../../output/which/which-rust && cargo build" >&2
    exit 2
fi

run_case 1 "basic lookup" "__DEFAULT_PATH__" ls
run_case 2 "non-existent command" "__DEFAULT_PATH__" non_existent_command
run_case 3 "multiple commands" "__DEFAULT_PATH__" ls cat grep
run_case 4 "all option" "__DEFAULT_PATH__" -a ls
run_case 5 "version option" "__DEFAULT_PATH__" --version
run_case 6 "help option" "__DEFAULT_PATH__" --help
run_case 7 "skip dot option" "__DEFAULT_PATH__" --skip-dot ls
run_case 8 "skip tilde option" "__DEFAULT_PATH__" --skip-tilde ls
run_case 9 "show dot option" "__DEFAULT_PATH__" --show-dot ls
run_case 10 "show tilde option" "__DEFAULT_PATH__" --show-tilde ls
run_case 11 "multiple non-existent commands" "__DEFAULT_PATH__" non_existent1 non_existent2
run_case 12 "skip functions option" "__DEFAULT_PATH__" --skip-functions ls
run_case 13 "restricted PATH" "/usr/local/bin:/usr/bin" ls
run_case 14 "empty command" "__DEFAULT_PATH__" ""
run_case 15 "multiple normal commands" "__DEFAULT_PATH__" cat ls
run_case 16 "double dash stops option parsing" "__DEFAULT_PATH__" -- ls
run_case 17 "all option duplicate search" "__DEFAULT_PATH__" -a ls
run_case 18 "invalid option" "__DEFAULT_PATH__" --invalid-option
run_case 19 "directory command" "__DEFAULT_PATH__" /usr/bin
run_case 20 "command with spaces" "__DEFAULT_PATH__" "program with spaces"

echo "Compared $case_count cases. failed=$failed"
echo "Logs: $RESULTS_DIR"
exit "$failed"

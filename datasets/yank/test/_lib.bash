#!/bin/bash
# Common helpers shared by every yank/test/*.sh script.
#
# Each test sources this file via:
#   . "$(dirname "$0")/_lib.sh"
#
# It sets up:
#   - an xsel mock that captures the clipboard write into ./yank_clipboard
#     (per-run-dir, so different cases never see each other's data)
#   - PATH so that ./xsel is preferred over any system one
#   - a require_expect helper that skips the test (exit 0 with SKIPPED log)
#     when the host has no expect / pty support, so the agent doesn't try to
#     "fix" missing test infrastructure.

set -u

setup_yank_env() {
    cat > xsel <<'XSEL_EOF'
#!/bin/bash
# Test mock: capture clipboard payload into the run dir.
cat > ./yank_clipboard
XSEL_EOF
    chmod +x xsel
    export PATH="$PWD:$PATH"
    rm -f ./yank_clipboard
}

# Skip-with-pass when expect is missing. Many CI hosts (Windows git-bash in
# particular) cannot run interactive expect tests; if we returned non-zero the
# RustTestAgent would loop the LLM trying to repair the binary, which is
# pointless. We log a clear SKIPPED line to stderr instead.
require_expect() {
    if ! command -v expect >/dev/null 2>&1; then
        echo "SKIPPED: 'expect' is not installed; cannot drive interactive yank tests." >&2
        exit 0
    fi
}

# Compare the captured clipboard against an expected value.
assert_clipboard() {
    local expected="$1"
    local actual
    actual="$(cat ./yank_clipboard 2>/dev/null || true)"
    if [ "$actual" = "$expected" ]; then
        return 0
    fi
    echo "Clipboard mismatch." >&2
    echo "  expected: '$expected'" >&2
    echo "  actual:   '$actual'" >&2
    return 1
}

# Verify that the clipboard file was never written (used for Ctrl-C / Ctrl-D
# tests where yank is expected to abort without yielding any selection).
assert_no_clipboard() {
    if [ ! -f ./yank_clipboard ]; then
        return 0
    fi
    echo "Expected no clipboard write, but ./yank_clipboard exists:" >&2
    cat ./yank_clipboard >&2 || true
    return 1
}

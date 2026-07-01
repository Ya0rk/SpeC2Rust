#!/bin/bash
# Interactive: Ctrl-C aborts yank without writing to the clipboard.

set -u
. "$(dirname "$0")/_lib.bash"
require_expect
setup_yank_env

expect <<'EXPECT_EOF' >/dev/null 2>&1
spawn bash -c "echo 'test data' | ./yank"
set timeout 5
expect -re ".*" {
    send "\003"
    expect eof
}
EXPECT_EOF

assert_no_clipboard

#!/bin/bash
# Interactive: 'G' should jump to the last field; pressing Enter
# captures "fourth".

set -u
. "$(dirname "$0")/_lib.bash"
require_expect
setup_yank_env

expect <<'EXPECT_EOF' >/dev/null 2>&1
spawn bash -c "echo 'first second third fourth' | ./yank"
set timeout 5
expect -re ".*" {
    send "G"
    sleep 0.2
    send "\r"
    expect eof
}
EXPECT_EOF

assert_clipboard "fourth"

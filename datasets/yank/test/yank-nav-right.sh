#!/bin/bash
# Interactive: pressing 'l' should advance one field to the right
# before yanking, so the second word is captured.

set -u
. "$(dirname "$0")/_lib.bash"
require_expect
setup_yank_env

expect <<'EXPECT_EOF' >/dev/null 2>&1
spawn bash -c "echo 'word1 word2 word3' | ./yank"
set timeout 5
expect -re ".*" {
    send "l"
    sleep 0.2
    send "\r"
    expect eof
}
EXPECT_EOF

assert_clipboard "word2"

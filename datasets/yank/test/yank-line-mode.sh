#!/bin/bash
# Interactive: -l puts yank in line mode; pressing Enter on the first
# line should yank "line1".

set -u
. "$(dirname "$0")/_lib.bash"
require_expect
setup_yank_env

expect <<'EXPECT_EOF' >/dev/null 2>&1
spawn bash -c "printf 'line1\nline2\nline3' | ./yank -l"
set timeout 5
expect -re ".*" { send "\r"; expect eof }
EXPECT_EOF

assert_clipboard "line1"

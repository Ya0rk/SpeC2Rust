#!/bin/bash
# Interactive: -d ":" splits "word1:word2:word3" on colons. Hitting
# Enter immediately yanks the first field.

set -u
. "$(dirname "$0")/_lib.bash"
require_expect
setup_yank_env

expect <<'EXPECT_EOF' >/dev/null 2>&1
spawn bash -c "echo 'word1:word2:word3' | ./yank -d ':'"
set timeout 5
expect -re ".*" { send "\r"; expect eof }
EXPECT_EOF

assert_clipboard "word1"

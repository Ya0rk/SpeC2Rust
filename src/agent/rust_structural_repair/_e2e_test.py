"""端到端测试：写入一个截断的 Rust 文件 → 调用 try_deterministic_repair → 验证文件被修复。"""

from __future__ import annotations

import os
import sys
import tempfile
import textwrap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rust_structural_repair import try_deterministic_repair
from rust_structural_repair.bracket_scanner import scan_brackets


def main() -> int:
    truncated_src = textwrap.dedent('''\
        pub struct Foo {
            x: i32,
        }

        impl Foo {
            pub fn new(x: i32) -> Self {
                Foo { x }
            }

            pub fn run(&self) {
                if self.x > 0 {
                    println!("positive: {}", self.x);
        ''')

    with tempfile.NamedTemporaryFile(mode="w", suffix=".rs", delete=False, encoding="utf-8") as f:
        f.write(truncated_src)
        tmp_path = f.name

    try:
        # 修复前：应不平衡
        with open(tmp_path, encoding="utf-8") as f:
            pre_content = f.read()
        pre = scan_brackets(pre_content)
        print(f"[pre] balanced={pre.is_balanced} unclosed={len(pre.unclosed_opens)}")
        assert not pre.is_balanced, "pre should be unbalanced"

        # 调用修复
        outcome = try_deterministic_repair(tmp_path)
        print(f"[repair] {outcome}")
        for d in outcome.details:
            print(f"  · {d}")
        assert outcome.changed, "repair should have applied"

        # 修复后：应平衡
        with open(tmp_path, encoding="utf-8") as f:
            post_content = f.read()
        post = scan_brackets(post_content)
        print(f"[post] balanced={post.is_balanced}")
        assert post.is_balanced, "post should be balanced"

        # 再次调用：应不修改（幂等）
        outcome2 = try_deterministic_repair(tmp_path)
        print(f"[repair-again] {outcome2}")
        assert not outcome2.changed, "second pass should be a no-op"

        print("\n=== fixed file content ===")
        print(post_content)
        print("=== end ===")
        return 0
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())

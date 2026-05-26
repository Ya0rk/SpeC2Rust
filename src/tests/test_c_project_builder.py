import os
import shutil
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.rtest.c_project_builder import CProjectBuilder


class CProjectBuilderTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parent / f"_tmp_c_builder_{uuid.uuid4().hex}"
        self.root.mkdir()

    def tearDown(self):
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)

    def write_makefile(self, text: str) -> None:
        (self.root / "Makefile").write_text(text, encoding="utf-8", newline="\n")

    def write_test_dir(self) -> None:
        (self.root / "test").mkdir(exist_ok=True)

    def test_validate_requires_makefile(self):
        self.write_test_dir()

        result = CProjectBuilder().validate(str(self.root), expected_bin_name="demo.exe")

        self.assertFalse(result.ok)
        self.assertIn("Makefile", result.error)

    def test_validate_requires_test_dir(self):
        self.write_makefile("all:\n\t@echo ok\n")

        result = CProjectBuilder().validate(str(self.root), expected_bin_name="demo.exe")

        self.assertFalse(result.ok)
        self.assertIn("test/", result.error)

    @unittest.skipIf(shutil.which("make") is None, "make is not available")
    def test_clean_failure_is_nonfatal_when_make_succeeds(self):
        self.write_test_dir()
        py = sys.executable.replace("\\", "/")
        self.write_makefile(
            "all:\n"
            f"\t\"{py}\" -c \"from pathlib import Path; Path('demo.exe').write_text('run')\"\n"
            f"\t\"{py}\" -c \"import os; os.chmod('demo.exe', 0o755)\"\n"
        )

        result = CProjectBuilder().clean_and_build(
            str(self.root), expected_bin_name="demo.exe"
        )

        self.assertTrue(result.ok, result.error)
        self.assertTrue(Path(result.binary_path).is_file())
        self.assertEqual(Path(result.binary_path).name, "demo.exe")

    @unittest.skipIf(shutil.which("make") is None, "make is not available")
    def test_build_failure_returns_error(self):
        self.write_test_dir()
        py = sys.executable.replace("\\", "/")
        self.write_makefile(f"all:\n\t\"{py}\" -c \"import sys; sys.exit(7)\"\n")

        result = CProjectBuilder().clean_and_build(
            str(self.root), expected_bin_name="demo.exe"
        )

        self.assertFalse(result.ok)
        self.assertIn("make", result.error)

    def test_ambiguous_binaries_require_expected_name(self):
        self.write_test_dir()
        self.write_makefile("all:\n\t@echo ok\n")
        for name in ("one", "two"):
            path = self.root / name
            path.write_text("run", encoding="utf-8")
            os.chmod(path, 0o755)

        result = CProjectBuilder().locate_binary(str(self.root))

        self.assertEqual(result, "")

    def test_single_root_binary_can_be_found_without_expected_name(self):
        self.write_test_dir()
        self.write_makefile("all:\n\t@echo ok\n")
        path = self.root / "tool"
        path.write_text("run", encoding="utf-8")
        os.chmod(path, 0o755)

        result = CProjectBuilder().locate_binary(str(self.root))

        if os.name == "nt":
            self.assertEqual(result, "")
        else:
            self.assertEqual(Path(result), path.resolve())


if __name__ == "__main__":
    unittest.main()

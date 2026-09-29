from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from chengda_paths import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR, ASSET_DIR


PARSER_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = DATA_DIR
CONVERTER = CODE_PROGRAM_DIR / "convert_chengda_sha_to_pdf.py"
INPUT_DIR = PROJECT_DIR / "SHA文件转换"


class ChengdaConverterCliTests(unittest.TestCase):
    def test_plan_lists_one_sha_and_one_physical_page(self) -> None:
        """成达入口必须在不生成产物时准确报告待转换页数。"""

        result = subprocess.run(
            [
                sys.executable,
                str(CONVERTER),
                "--input-dir",
                str(INPUT_DIR),
                "--plan-only",
            ],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SHA文件: 1", result.stdout)
        self.assertIn("物理页: 1", result.stdout)
        self.assertIn("00-42-0203-26-00EE00002-01 -> 第1页 (Sheet6)", result.stdout)


if __name__ == "__main__":
    unittest.main()

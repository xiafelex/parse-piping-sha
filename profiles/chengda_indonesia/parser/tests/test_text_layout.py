from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from chengda_paths import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR, ASSET_DIR


PARSER_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = DATA_DIR
SOURCE_SHA = PROJECT_DIR / "SHA文件转换" / "00-42-0203-26-00EE00002-01.sha"
sys.path.insert(0, str(PARSER_DIR))


def load_parser():
    spec = importlib.util.spec_from_file_location(
        "chengda_sha_to_svg_text", PARSER_DIR / "sha_to_svg_prototype.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载成达SHA解析器")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ChengdaTextLayoutTests(unittest.TestCase):
    def test_template_text_is_selected_by_structure_not_blue_star_ref_range(self) -> None:
        """成达低编号模板对象中的标题栏字段不能被蓝星编号范围过滤。"""

        parser = load_parser()
        streams = parser.read_sha_streams(SOURCE_SHA)

        records = parser.template_text_records(
            streams["Sheet221"], streams["PSMcluster0"], streams["StyleCluster"]
        )
        texts = {str(record["text"]) for record in records}

        self.assertTrue(
            {"DESIGN TEMP.", "P&ID NO.", "CAA-1 PROJECT", "Cilegon, Banten, Indonesia"}
            <= texts
        )

    def test_psm_bbox_is_converted_from_32768_grid_to_page_units(self) -> None:
        """成达PSM包围框必须与16800单位的Sheet文字锚点处于同一坐标系。"""

        parser = load_parser()
        streams = parser.read_sha_streams(SOURCE_SHA)

        actual = parser.psm_bbox(streams["PSMcluster0"], 0x17AB)

        self.assertIsNotNone(actual)
        assert actual is not None
        expected = (3680.6396484375, 4427.63671875, 3750.3662109375, 4491.2109375)
        for observed, wanted in zip(actual, expected):
            self.assertAlmostEqual(observed, wanted, places=6)

    def test_every_emitted_text_extent_has_positive_dimensions(self) -> None:
        """无PSM框的文字不能沿用上一条记录的right/top边界。"""

        parser = load_parser()
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            svg = temporary_path / "page.svg"
            manifest = temporary_path / "page.json"
            parser.render(SOURCE_SHA, svg, 1, False, manifest, None, "Sheet6")
            payload = json.loads(manifest.read_text(encoding="utf-8"))

        invalid = [
            entry
            for entry in payload["text"]
            if entry["psm_bbox_page_units"][2] <= entry["psm_bbox_page_units"][0]
            or entry["psm_bbox_page_units"][3] <= entry["psm_bbox_page_units"][1]
        ]
        self.assertEqual(invalid, [])


if __name__ == "__main__":
    unittest.main()

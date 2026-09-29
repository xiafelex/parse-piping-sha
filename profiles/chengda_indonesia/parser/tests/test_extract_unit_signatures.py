from pathlib import Path
from chengda_paths import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR, ASSET_DIR
import sys

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from extract_unit_signatures import crossing_signature_rgba


def test_table_rule_removed_without_cropping_crossing_stroke():
    source = Image.new("RGB", (100, 70), "white")
    for y in (0, 1, 50, 51):
        for x in range(100):
            source.putpixel((x, y), (0, 0, 0))
    for y in range(20, 56):
        for x in range(40, 44):
            source.putpixel((x, y), (0, 0, 0))
    output = crossing_signature_rgba(source, upscale=1)
    assert output.size == (4, 36)
    assert output.getchannel("A").getextrema() == (255, 255)


def test_signature_without_rules_retains_full_ink_bounds():
    source = Image.new("RGB", (100, 70), "white")
    for y in range(5, 65):
        source.putpixel((20, y), (0, 0, 0))
    output = crossing_signature_rgba(source, upscale=1)
    assert output.size == (1, 60)

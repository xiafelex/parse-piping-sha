from __future__ import annotations

from inject_sha_weld_callouts import (
    TextStyleModel,
    TextTemplate,
    pack_text_record,
    synthetic_weld_text_ref,
)
from sha_to_svg_prototype import text_records
from verify_sha_weld_callouts import appended_callout_records


def test_six_character_injected_weld_label_round_trips_without_truncation() -> None:
    template = TextTemplate(raw=bytes(66), style_ref=0x0585)
    style = TextStyleModel(
        offset_x=0.0,
        offset_y=0.0,
        font_height=88.0,
        char_width=52.0,
    )
    label = "F107A1"
    graphic_ref = synthetic_weld_text_ref(page=1, index=7, text_length=len(label))

    record = pack_text_record(
        template,
        style,
        graphic_ref,
        center_x=4000.0,
        center_y=5000.0,
        number=label,
    )

    assert len(record) == 68
    parsed = text_records(record)
    assert [item["text"] for item in parsed] == [label]


def test_five_character_injected_weld_label_keeps_legacy_record_shape() -> None:
    template = TextTemplate(raw=bytes(66), style_ref=0x0585)
    style = TextStyleModel(0.0, 0.0, 88.0, 52.0)
    label = "FS123"
    graphic_ref = synthetic_weld_text_ref(page=1, index=9, text_length=len(label))

    record = pack_text_record(template, style, graphic_ref, 4000.0, 5000.0, label)

    assert len(record) == 66
    assert [item["text"] for item in text_records(record)] == [label]


def test_verifier_splits_mixed_five_and_six_character_callouts() -> None:
    template = TextTemplate(raw=bytes(66), style_ref=0x0585)
    style = TextStyleModel(0.0, 0.0, 88.0, 52.0)
    fixed_lines = bytes(17 * 50)
    payload = b"".join(
        (
            fixed_lines,
            pack_text_record(
                template,
                style,
                synthetic_weld_text_ref(1, 1, 5),
                4000.0,
                5000.0,
                "FS123",
            ),
            fixed_lines,
            pack_text_record(
                template,
                style,
                synthetic_weld_text_ref(1, 2, 6),
                4200.0,
                5200.0,
                "F107A1",
            ),
        )
    )

    assert [record["label"] for record in appended_callout_records(payload)] == [
        "FS123",
        "F107A1",
    ]

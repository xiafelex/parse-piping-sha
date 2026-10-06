import struct
from sha_to_svg_prototype import style_format_metrics, is_chengda_feature_strip_value


def definition(ref, family='Arial Narrow-Z', height=.002):
    data = bytearray(56)
    struct.pack_into('<I', data, 0, ref)
    data[12:20] = b'\x1f\x01\x00\x00\x04\x08\x00\x00'
    struct.pack_into('<d', data, 28, height)
    return b'\x00' * 10 + b'\x01\x00' + data + family.encode('utf-16le') + b'\x00\x00'


def alias(ref, parent):
    data = bytearray(90)
    struct.pack_into('<I', data, 0, ref)
    data[14:20] = b'\x12\x00\xe0\x02\x00\x00'
    struct.pack_into('<I', data, 24, parent)
    return b'\x00' * 10 + b'\x01\x00' + data


def test_indirect_font_far_from_definition_and_chained_reference():
    data = definition(0x170) + b'\xff' * 300 + alias(0x180, 0x170) + alias(0x190, 0x180)
    metrics = style_format_metrics(data)
    assert metrics[0x180] == ('Arial Narrow-Z', .002)
    assert metrics[0x190] == ('Arial Narrow-Z', .002)


def test_cycles_and_missing_parents_do_not_guess_font():
    metrics = style_format_metrics(alias(0x180, 0x181) + alias(0x181, 0x180) + alias(0x190, 0x199))
    assert not metrics


def test_feature_font_requires_region_and_style_not_unit_id():
    assert is_chengda_feature_strip_value(0x1897, 0x15b, .2, .02, 'Arial Narrow-Z')
    assert not is_chengda_feature_strip_value(0x1897, 0x15b, .48, .02, 'Arial Narrow-Z')
    assert not is_chengda_feature_strip_value(8, 0x15b, .2, .02, 'Arial Narrow-Z')
    assert not is_chengda_feature_strip_value(0x1897, 0x15b, .2, .02, 'Courier New-Z')

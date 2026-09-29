"""Regression coverage for template-owned plotting without changing Sheet6."""
import sys
from pathlib import Path
from chengda_paths import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR, ASSET_DIR

import pytest
import copy
import math

PARSER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PARSER))
import sha_to_svg_prototype as api
import chengda_template_owned_page as owned

ROOT = DATA_DIR / 'SHA文件转换'
SAMPLE = ROOT / '400101' / '400101 SHA' / '40-42-0101-26-40EC40001-25.sha'


@pytest.fixture(scope='module')
def streams():
    if not SAMPLE.exists():
        pytest.skip('Local SHA fixture unavailable')
    return api.read_sha_streams(SAMPLE)


def test_storage_layout_detection(streams):
    assert owned.is_template_owned_page(streams, 'Sheet6', 'Sheet221')
    assert not owned.is_template_owned_page(streams, 'Sheet221', 'Sheet221')
    old = ROOT / '201600' / '201600 SHA' / '20-42-1600-26-HCL-8890-02.sha'
    if old.exists():
        assert not owned.is_template_owned_page(api.read_sha_streams(old), 'Sheet6', 'Sheet221')


def test_mapping_adapter_keeps_page_identity_and_restores_pipe_geometry(streams):
    from inject_sha_weld_callouts import sheet_streams
    physical = sheet_streams(streams, None)
    adapted = owned.mapping_sheet_streams(streams, None)
    assert [(s.page, s.stream_name) for s in adapted] == [(s.page, s.stream_name) for s in physical]
    assert len(adapted) == 1
    assert adapted[0].data == streams['Sheet221']
    assert len(api.line_segments(adapted[0].data)+api.template_line_segments(adapted[0].data)) > 100
    assert owned.mapping_sheet_streams(streams, {2}) == []
    old = ROOT / '201600' / '201600 SHA' / '20-42-1600-26-HCL-8890-02.sha'
    if old.exists():
        old_streams = api.read_sha_streams(old)
        assert owned.mapping_sheet_streams(old_streams, None) == sheet_streams(old_streams, None)


def test_projected_split_clearance_preserves_endpoint_and_engineering_coordinate(streams):
    endpoint = dict(weld_number='END', graphic_ref=1, page=1, sha_x=5000., sha_y=3000.)
    split = dict(weld_number='SPLIT', graphic_ref=2, page=1, sha_x=5005., sha_y=3000.,
                 construction_virtual_target=True,
                 construction_virtual_rule='construction-table-material-chain-terminal-pipe',
                 pipe_axis=[[5000., 3000.], [5600., 3000.]], pcf_endpoint=[1., 2., 3., 4.])
    payload = dict(welds=[endpoint, split])
    original = copy.deepcopy(payload)
    result = owned.space_template_owned_pipe_splits(payload, SAMPLE)
    assert payload == original
    assert result['welds'][0] == endpoint
    assert result['welds'][1]['pcf_endpoint'] == split['pcf_endpoint']
    assert math.dist((result['welds'][1]['sha_x'], result['welds'][1]['sha_y']), (5000, 3000)) == pytest.approx(58.8)
    # The split must not pass an already proven peer on the same pipe.
    crowded = dict(welds=[endpoint, split, dict(weld_number='PEER', graphic_ref=3,
                                              page=1, sha_x=5040., sha_y=3000.)])
    assert owned.space_template_owned_pipe_splits(crowded, SAMPLE) is crowded
    old = ROOT / '201600' / '201600 SHA' / '20-42-1600-26-HCL-8890-02.sha'
    if old.exists():
        assert owned.space_template_owned_pipe_splits(payload, old) is payload


def test_compass_widths_are_decoded_from_font_definitions(streams):
    style = streams['StyleCluster']
    assert owned.format_width_scale(style, 0x122) == 1
    assert owned.format_width_scale(style, 0x12c) == .75
    assert owned.format_width_scale(style, 0x132) == .65
    assert owned.format_width_scale(b'', 0x132) == 1
    # This variant also carries category-1 formats; the contractor value
    # shares Arial-Z but explicitly compresses its width to 66 percent.
    assert owned.format_width_scale(style, 0x166) == .66


def test_revision_label_uses_its_declared_text_height(streams):
    label = next(r for r in api.text_records(streams['Sheet6'])
                 if r['text'] == '0' and r['format_ref'] == 0x8e)
    definition = owned.format_font_definition(streams['StyleCluster'], label['format_ref'])
    assert definition['height'] == pytest.approx(.003175)
    geometry = owned.revision_text_geometry(label, streams['StyleCluster'], api)
    assert geometry[2] == pytest.approx(.003175*16800*1.4)
    assert geometry[1] < label['y']*16800
    assert geometry[3] == 'TemplateOwned-plot'


def test_plot_values_arcs_and_solid_dots(streams):
    records = owned.plot_text_records(streams['Sheet221'], streams['PSMcluster0'], streams['StyleCluster'], api)
    texts = {r['text'] for r in records}
    assert {'65', 'ERECTION MATERIALS', 'PN 0°', '23.57°', 'UP', 'DOWN'} <= texts
    elements, stats = owned.render_plot_content(streams['Sheet221'], streams['PSMcluster0'], streams['StyleCluster'], records, api)
    assert stats['arc_count'] > 0
    assert stats['weld_dot_count'] > 0
    assert all('fill="#000000"' in e for e in elements if 'sha-template-owned-weld-dot' in e)
    rotated = next(e for e in elements if '>23.57°</text>' in e)
    assert 'scale(0.650000 1)' in rotated
    assert 'rotate(' in rotated

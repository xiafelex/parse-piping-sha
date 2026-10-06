from types import SimpleNamespace
import pytest
import convert_chengda_weld_sha_to_pdf as converter


def setup_files(tmp_path, monkeypatch):
    sha = tmp_path / 'sha'
    pcf = tmp_path / 'pcf'
    sha.mkdir()
    pcf.mkdir()
    (sha / 'LINE-A^2FB.sha').touch()
    monkeypatch.setattr(converter, 'read_sha_streams', lambda p: {})
    monkeypatch.setattr(converter, 'sheet_streams', lambda s, p: [SimpleNamespace(page=1, stream_name='Sheet6')])
    return sha, pcf


def test_removed_separator_matches_internal_reference(tmp_path, monkeypatch):
    sha, pcf = setup_files(tmp_path, monkeypatch)
    target = pcf / 'LINE-AB.pcf'
    target.write_text('PIPELINE-REFERENCE LINE-A/B\n', encoding='utf-8')
    jobs = converter.discover_weld_jobs(sha, pcf, {'LINE-A/B'})
    assert len(jobs) == 1 and jobs[0].pcf_path == target
    assert jobs[0].sheet_id == 'LINE-A^2FB'


def test_duplicate_internal_reference_is_rejected(tmp_path, monkeypatch):
    sha, pcf = setup_files(tmp_path, monkeypatch)
    for name in ['one', 'two']:
        (pcf / (name + '.pcf')).write_text('PIPELINE-REFERENCE LINE-A/B\n', encoding='utf-8')
    with pytest.raises(ValueError, match='多个PCF'):
        converter.discover_weld_jobs(sha, pcf)


def test_no_fuzzy_separator_removal(tmp_path, monkeypatch):
    sha, pcf = setup_files(tmp_path, monkeypatch)
    (pcf / 'LINE-AB.pcf').write_text('PIPELINE-REFERENCE LINE-AB\n', encoding='utf-8')
    missing = []
    assert not converter.discover_weld_jobs(sha, pcf, missing_pcf_sheet_ids=missing)
    assert missing == ['LINE-A^2FB']

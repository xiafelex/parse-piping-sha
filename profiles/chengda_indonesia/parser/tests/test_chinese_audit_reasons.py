from chengda_audit_reasons import chinese_audit_reason


def test_support_reason_does_not_claim_coordinate_disagreement():
    assert chinese_audit_reason('support UCI unavailable and coordinate projection failed') == '本页无法通过支架UCI唯一定位，且坐标投影未满足匹配条件'


def test_other_known_english_reasons_and_chinese_preservation():
    assert chinese_audit_reason('UCI not visible on this SHA page') == '本页SHA未找到对应UCI的可见元件'
    assert '配对两侧' in chinese_audit_reason('paired UCI not visible on this SHA page')
    assert chinese_audit_reason('PCF焊口未找到唯一SHA位置') == 'PCF焊口未找到唯一SHA位置'
    assert chinese_audit_reason('unexpected diagnostic') == '处理未完成，具体诊断请查看页面审计记录'

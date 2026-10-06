"""Chinese-facing audit reasons; preserve original diagnostics in page JSON."""

_REASONS = {
    'support UCI unavailable and coordinate projection failed':
        '本页无法通过支架UCI唯一定位，且坐标投影未满足匹配条件',
    'paired UCI not visible on this SHA page':
        '本页SHA未找到配对两侧元件的完整UCI关联，无法唯一定位法兰接口',
    'UCI not visible on this SHA page':
        '本页SHA未找到对应UCI的可见元件',
    'review stronger visible-component topology':
        '需结合可见元件的拓扑连接关系进一步核对',
}


def chinese_audit_reason(reason: str) -> str:
    reason = str(reason).strip()
    if reason in _REASONS:
        return _REASONS[reason]
    if not reason:
        return '缺少诊断原因，需查看页面审计记录'
    if any('\u4e00' <= char <= '\u9fff' for char in reason):
        return reason
    return '处理未完成，具体诊断请查看页面审计记录'

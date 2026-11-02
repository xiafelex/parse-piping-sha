#!/usr/bin/env python3
"""Apply Chengda construction weld numbers to PCF/SHA topology mappings."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from _bootstrap import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR
import re
from typing import Iterable

from openpyxl import load_workbook
from chengda_sheet_ids import sheet_id_key


@dataclass(frozen=True)
class ConstructionWeldInput:
    excel_row: int
    sheet_id: str
    weld_number: str
    weld_type: str
    coordinate: tuple[Decimal, Decimal, Decimal]
    original_weld_number: str = ""
    material_uci_a: str = ""
    material_uci_b: str = ""
    material_code_a: str = ""
    material_code_b: str = ""
    quantity_a: Decimal | None = None
    quantity_b: Decimal | None = None
    unit_a: str = ""
    unit_b: str = ""
    joining_type: str = ""
    number_source: str = "施工焊口号"
    source_sheet_id: str = ""


@dataclass(frozen=True)
class SkippedConstructionWeld:
    excel_row: int
    sheet_id: str
    weld_number: str
    reason: str
    manual_review_required: bool = False


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _weld_type_column(headers: list[str]) -> str:
    """Prefer the checked type field used by later ISO-review workbooks."""

    for name in ("焊点类型", "焊点类型-人工核对"):
        if name in headers:
            return name
    return "焊点类型"


def _normalized_weld_type(value: object) -> str:
    """Normalize EP3D's legacy FS prefix to the S/F engineering category."""

    weld_type = _text(value).upper()
    return "S" if weld_type == "FS" else weld_type


def _coordinate(value: object, excel_row: int) -> tuple[Decimal, Decimal, Decimal]:
    raw = _text(value).replace("，", ",")
    parts = [part.strip() for part in raw.split(",")]
    if len(parts) != 3:
        raise ValueError(f"Excel第{excel_row}行焊点位置不是三个逗号分隔的数值：{raw}")
    try:
        return tuple(Decimal(part) for part in parts)  # type: ignore[return-value]
    except InvalidOperation as exc:
        raise ValueError(f"Excel第{excel_row}行焊点位置包含非数值内容：{raw}") from exc


def _optional_decimal(value: object) -> Decimal | None:
    text = _text(value)
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def read_construction_weld_inputs(
    path: Path,
    number_column: str = "施工焊口号",
) -> tuple[list[ConstructionWeldInput], list[SkippedConstructionWeld]]:
    """Select one numbering system for every sheet.

    Rows without an original weld number are ignored first.  Of the remaining
    rows, a sheet containing any construction number uses construction numbers
    exclusively; otherwise the whole sheet falls back to original weld numbers.
    """

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        worksheet = workbook.active
        rows = worksheet.iter_rows(values_only=True)
        try:
            headers = [_text(value) for value in next(rows)]
        except StopIteration as exc:
            raise ValueError(f"焊口表为空：{path}") from exc
        required = ("单页图号", "焊点编号", number_column, "焊点类型", "焊点位置")
        missing = [name for name in required if name not in headers]
        if missing:
            raise ValueError(f"焊口表缺少必需列：{', '.join(missing)}")
        index = {name: headers.index(name) for name in headers if name}

        def value(row: tuple[object, ...], name: str) -> str:
            column = index.get(name)
            return _text(row[column]) if column is not None and column < len(row) else ""

        candidate_rows: list[dict[str, object]] = []
        skipped: list[SkippedConstructionWeld] = []
        for excel_row, row in enumerate(rows, start=2):
            sheet_id = value(row, "单页图号")
            original_number = value(row, "焊点编号")
            construction_number = value(row, number_column)
            if any(
                "STUB END SP" in value(row, name).upper()
                for name in ("描述1", "描述2")
            ):
                skipped.append(
                    SkippedConstructionWeld(
                        excel_row,
                        sheet_id,
                        construction_number or original_number,
                        "描述1或描述2包含STUB END SP，按强制排除规则不落图",
                    )
                )
                continue
            if not original_number:
                if construction_number:
                    skipped.append(
                        SkippedConstructionWeld(
                            excel_row,
                            sheet_id,
                            construction_number,
                            "焊点编号为空，按规则忽略",
                        )
                    )
                continue
            weld_type = value(row, "焊点类型").upper()
            position = value(row, "焊点位置")
            if not sheet_id:
                raise ValueError(f"Excel第{excel_row}行焊点编号不为空但单页图号为空")
            if not position:
                raise ValueError(f"Excel第{excel_row}行焊点编号不为空但焊点位置为空")
            if weld_type not in {"S", "F"}:
                raise ValueError(f"Excel第{excel_row}行焊点类型不是S或F：{weld_type}")
            candidate_rows.append(
                {
                    "excel_row": excel_row,
                    "sheet_id": sheet_id,
                    "original_number": original_number,
                    "construction_number": construction_number,
                    "weld_type": weld_type,
                    "coordinate": _coordinate(position, excel_row),
                    "material_uci_a": value(row, "材料唯一码1"),
                    "material_uci_b": value(row, "材料唯一码2"),
                    "material_code_a": value(row, "焊接材料1"),
                    "material_code_b": value(row, "焊接材料2"),
                    "quantity_a": _optional_decimal(value(row, "数量1")),
                    "quantity_b": _optional_decimal(value(row, "数量2")),
                    "unit_a": value(row, "单位1"),
                    "unit_b": value(row, "单位2"),
                    "joining_type": value(row, "焊接类型"),
                }
            )
    finally:
        workbook.close()

    construction_sheets = {
        str(row["sheet_id"]).casefold()
        for row in candidate_rows
        if row["construction_number"]
    }
    records: list[ConstructionWeldInput] = []
    for row in candidate_rows:
        uses_construction = str(row["sheet_id"]).casefold() in construction_sheets
        if uses_construction and not row["construction_number"]:
            continue
        selected_number = (
            str(row["construction_number"])
            if uses_construction
            else str(row["original_number"])
        )
        records.append(
            ConstructionWeldInput(
                excel_row=int(row["excel_row"]),
                sheet_id=str(row["sheet_id"]),
                weld_number=selected_number,
                weld_type=str(row["weld_type"]),
                coordinate=row["coordinate"],  # type: ignore[arg-type]
                original_weld_number=str(row["original_number"]),
                material_uci_a=str(row["material_uci_a"]),
                material_uci_b=str(row["material_uci_b"]),
                material_code_a=str(row["material_code_a"]),
                material_code_b=str(row["material_code_b"]),
                quantity_a=row["quantity_a"],  # type: ignore[arg-type]
                quantity_b=row["quantity_b"],  # type: ignore[arg-type]
                unit_a=str(row["unit_a"]),
                unit_b=str(row["unit_b"]),
                joining_type=str(row["joining_type"]),
                number_source=(number_column if uses_construction else "焊点编号"),
            )
        )
    return records, skipped


def read_iso_review_weld_inputs(
    path: Path,
    missing_position_is_ep3d_missing: bool = False,
    remark_policy: str = "default",
) -> tuple[list[ConstructionWeldInput], list[SkippedConstructionWeld]]:
    """Read the 200301 ISO-audit numbering table.

    Rows outside the comparison scope retain the EP3D sheet/number.  Rows in
    scope use the ISO sheet/number, including a legitimate cross-sheet row.
    ISO-only rows have no PCF coordinate and are retained for manual review.
    In-scope EP3D-only rows without an ISO number intentionally create no
    callout because the selected ISO numbering system has no label for them.
    """

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        worksheet = workbook.active
        rows = worksheet.iter_rows(values_only=True)
        try:
            headers = [_text(value) for value in next(rows)]
        except StopIteration as exc:
            raise ValueError(f"焊口表为空：{path}") from exc
        weld_type_column = _weld_type_column(headers)
        sheet_column = "单页图号" if "单页图号" in headers else "管线号"
        original_number_column = "焊点编号" if "焊点编号" in headers else "EP3D"
        required = (
            sheet_column,
            original_number_column,
            weld_type_column,
            "焊点位置",
            "ISO图号",
            "ISO焊口号",
            "对应状态",
        )
        missing = [name for name in required if name not in headers]
        if missing:
            raise ValueError(f"ISO审核焊口表缺少必需列：{', '.join(missing)}")
        index = {name: headers.index(name) for name in headers if name}

        def value(row: tuple[object, ...], name: str) -> str:
            column = index.get(name)
            return _text(row[column]) if column is not None and column < len(row) else ""

        records: list[ConstructionWeldInput] = []
        skipped: list[SkippedConstructionWeld] = []
        for excel_row, row in enumerate(rows, start=2):
            if remark_policy in {"100600", "100203"} and not any(_text(cell) for cell in row):
                continue
            source_sheet = value(row, sheet_column)
            original_number = value(row, original_number_column)
            iso_sheet = value(row, "ISO图号")
            iso_number = value(row, "ISO焊口号")
            checked_number = value(row, "施工焊口号人工核对")
            review_number = checked_number or iso_number
            status = value(row, "对应状态")
            in_scope = status != "不在对照范围"
            if remark_policy in {'ws-iso-review', 'ws-iso-review-302100'}:
                # These WS deliveries explicitly select the reviewed ISO
                # column, not construction numbers or an EP3D fallback.
                review_number = iso_number
                checked_number = ''
                in_scope = True
                if (remark_policy == 'ws-iso-review-302100'
                        and status == '暂不对照' and not iso_number
                        and original_number and value(row, '焊点位置')):
                    # This unit's 37 no-reference rows were explicitly
                    # authorized to retain EP3D IDs. Other blanks stay manual.
                    in_scope = False
            position = value(row, "焊点位置")
            # Reviewers use several headings. The explicit EP-side finding
            # takes precedence over a blank coordinate, which is only a
            # fallback clue that the weld cannot be placed automatically.
            # Review exports can append a second identically named note
            # column. Read every note column, without changing numeric or
            # coordinate column selection for the rest of the workbook.
            note_parts = [
                _text(row[column])
                for name in ("审核备注", "人工核对备注", "核对备注")
                for column, header in enumerate(headers)
                if re.fullmatch(re.escape(name) + r"(?:\d+)?", header)
                and column < len(row) and _text(row[column])
            ]
            manual_note = "；".join(dict.fromkeys(note_parts))

            if remark_policy == "404043" and re.search(r"以\s*EP(?:3D)?(?:焊口)?\s*为准", manual_note, re.I):
                # Explicit reviewer instruction overrides the filled ISO ID.
                in_scope = False

            if remark_policy == "201600":
                # 0B review: no ISO drawing is an EP3D-only numbering scope.
                # ISO missing labels with supplied IDs are still drawable.
                if "ISO无图" in manual_note:
                    in_scope = False
                discard = "需删除" in manual_note
                ep_missing = bool(re.search(
                    r"EP(?:3D)?(?:图纸|图|侧)?(?:未标注|无图|无此点|无此焊点|无此支管|缺少焊点|焊点缺失)",
                    manual_note, re.I,
                ))
                if discard or ep_missing:
                    skipped.append(SkippedConstructionWeld(
                        excel_row, iso_sheet or source_sheet,
                        review_number or original_number,
                        f"审核备注：{manual_note}；" +
                        ("按审核意见排除，不需补标" if discard else "EP3D缺少对应图形或焊点，需人工补标"),
                        not discard,
                    ))
                    continue

            if remark_policy == "100203":
                # The numbered note is the human review; the unsuffixed note
                # describes automatically appended ISO-only rows. Retain both,
                # but put the human finding first in manual-review reasons.
                human_notes = [
                    _text(row[column]) for column, header in enumerate(headers)
                    if re.fullmatch(r"核对备注\d+", header)
                    and column < len(row) and _text(row[column])
                ]
                manual_note = "；".join(dict.fromkeys(human_notes + note_parts))
                ep_missing = bool(re.search(
                    r"EP(?:3D)?(?:图纸|图|侧)?[^，。；,;]{0,10}?"
                    r"(?:未标注|缺失|漏标|缺少(?:焊点|焊口|管件))",
                    manual_note, re.I,
                ))
                if ep_missing or (in_scope and not review_number):
                    reason = ("EP3D缺少焊点或管件，需人工补标" if ep_missing
                              else "ISO对照范围内无审核ISO焊口号，需人工核对补标")
                    if manual_note:
                        reason += f"（审核备注：{manual_note}）"
                    skipped.append(SkippedConstructionWeld(
                        excel_row, iso_sheet or source_sheet,
                        review_number or original_number, reason, True,
                    ))
                    continue

            if remark_policy == "301200" and "ISO需变更删除，可忽略" in manual_note:
                skipped.append(SkippedConstructionWeld(
                    excel_row, iso_sheet or source_sheet,
                    review_number or original_number,
                    f"核对备注：{manual_note}；按审核意见排除，不需补标",
                    False,
                ))
                continue

            # Explicit unit review contract; do not change other units' policy.
            if remark_policy in {"402001", "100600", "304100-WS", "ws-iso-review", "ws-iso-review-302100"}:
                discard = "需删除" in manual_note or "重复标注" in manual_note
                iso_missing = bool(re.search(r"ISO(?:图|侧)?(?:焊点|焊口)?(?:缺失|漏标|无此焊点)", manual_note, re.I))
                ep_missing = bool(re.search(r"EP(?:3D)?(?:图|侧)?(?:焊点|焊口)?(?:缺失|漏标|未标注|无此焊点)", manual_note, re.I))
                unresolved = "无法核对" in manual_note or ("需升版" in manual_note and not iso_missing)
                if discard or ep_missing or unresolved:
                    skipped.append(SkippedConstructionWeld(
                        excel_row, iso_sheet or source_sheet,
                        review_number or original_number,
                        f"核对备注：{manual_note}；" + ("按审核意见排除，不需补标" if discard else "需人工核对补标"),
                        not discard,
                    ))
                    continue

            if any(
                "STUB END SP" in value(row, name).upper()
                for name in ("描述1", "描述2")
            ):
                skipped.append(
                    SkippedConstructionWeld(
                        excel_row,
                        iso_sheet or source_sheet,
                        review_number or original_number,
                        "描述1或描述2包含STUB END SP，按强制排除规则不落图",
                    )
                )
                continue

            if remark_policy in {'ws-iso-review', 'ws-iso-review-302100'} and in_scope and not review_number:
                skipped.append(SkippedConstructionWeld(
                    excel_row, iso_sheet or source_sheet, original_number,
                    f'ISO焊口号为空（对应状态：{status or "空白"}），待人工确认审核编号，不退回EP3D编号',
                    True,
                ))
                continue

            # An ISO number may already have been supplied for a weld that is
            # completely absent from EP3D. Such a row has no trustworthy
            # coordinate/topology anchor and must remain for manual placement.
            ep_missing_note = next(
                (
                    note for note in note_parts
                    if re.search(
                        r"EP(?:3D)?(?:图|侧)?[^，。；,;]{0,10}?"
                        r"(?:未标注|缺失|漏标|遗漏(?:焊点|焊口)|缺少(?:焊点|焊口)|没有(?:焊点|焊口)?)",
                        note,
                        re.IGNORECASE,
                    )
                ),
                "",
            )
            if ep_missing_note:
                skipped.append(
                    SkippedConstructionWeld(
                        excel_row,
                        iso_sheet or source_sheet,
                        review_number or original_number,
                        f"EP3D缺少焊点，需人工补标（审核备注：{ep_missing_note}）",
                        True,
                    )
                )
                continue

            # 金童图在长直管上补出的施工ISO单侧焊口不是EP3D原口。
            # 即使审核表中暂存了EP3D编号、坐标或“已配对”状态，也不能据此
            # 自动吸附到PCF/SHA图元，否则会把增口错误标到邻近原焊口上。
            if "长直管增口" in manual_note:
                skipped.append(
                    SkippedConstructionWeld(
                        excel_row,
                        iso_sheet or source_sheet,
                        review_number or original_number,
                        f"{manual_note}，属于施工ISO单侧焊口，需人工补标",
                        True,
                    )
                )
                continue

            if missing_position_is_ep3d_missing and not position:
                skipped.append(
                    SkippedConstructionWeld(
                        excel_row,
                        (iso_sheet if in_scope else source_sheet) or source_sheet,
                        (review_number if in_scope else original_number)
                        or review_number or original_number,
                        "焊点位置为空，视为EP3D缺失焊口，需人工补标",
                        True,
                    )
                )
                continue

            if in_scope and (not original_number or not position):
                if not original_number:
                    reason = "仅ISO侧，无EP3D坐标和元件拓扑，需人工补标"
                else:
                    reason = "ISO审核编号已补充但无焊点坐标，需人工补标"
                skipped.append(
                    SkippedConstructionWeld(
                        excel_row,
                        iso_sheet or source_sheet,
                        review_number,
                        reason,
                        True,
                    )
                )
                continue
            if in_scope and not review_number:
                skipped.append(
                    SkippedConstructionWeld(
                        excel_row,
                        iso_sheet or source_sheet,
                        original_number,
                        "ISO对照范围内无ISO焊口号，按ISO编号规则不落图",
                    )
                )
                continue

            selected_sheet = (iso_sheet if in_scope else source_sheet).strip()
            selected_number = (review_number if in_scope else original_number).strip()
            if not selected_sheet or not selected_number:
                skipped.append(
                    SkippedConstructionWeld(
                        excel_row,
                        selected_sheet or source_sheet,
                        selected_number or original_number,
                        "缺少所选编号体系的图号或焊口号，按规则忽略",
                    )
                )
                continue
            if not position:
                raise ValueError(f"Excel第{excel_row}行所选焊口缺少焊点位置")
            weld_type = _normalized_weld_type(value(row, weld_type_column))
            if weld_type not in {"S", "F"}:
                raise ValueError(f"Excel第{excel_row}行焊点类型不是S或F：{weld_type}")
            records.append(
                ConstructionWeldInput(
                    excel_row=excel_row,
                    sheet_id=selected_sheet,
                    weld_number=selected_number,
                    weld_type=weld_type,
                    coordinate=_coordinate(position, excel_row),
                    original_weld_number=original_number,
                    material_uci_a=value(row, "材料唯一码1"),
                    material_uci_b=value(row, "材料唯一码2"),
                    material_code_a=value(row, "焊接材料1"),
                    material_code_b=value(row, "焊接材料2"),
                    quantity_a=_optional_decimal(value(row, "数量1")),
                    quantity_b=_optional_decimal(value(row, "数量2")),
                    unit_a=value(row, "单位1"),
                    unit_b=value(row, "单位2"),
                    joining_type=value(row, "焊接类型"),
                    number_source=(
                        "施工焊口号人工核对"
                        if in_scope and checked_number
                        else "ISO焊口号" if in_scope else "焊点编号"
                    ),
                    source_sheet_id=source_sheet,
                )
            )
    finally:
        workbook.close()
    return records, skipped


def remap_weld_sheet_aliases(records, aliases: list[str]):
    """Apply explicit weld-table aliases, never a global suffix-stripping rule.

    Keep original source sheet IDs and Excel row numbers for traceability.
    Management tables and SHA/PCF discovery are intentionally unaffected.
    """
    mapping = {}
    for entry in aliases:
        source, separator, target = entry.partition('=')
        if not separator or not source.strip() or not target.strip():
            raise ValueError(f'焊口图号别名格式应为原图号=目标图号：{entry}')
        key = sheet_id_key(source)
        target = target.strip()
        if key in mapping and sheet_id_key(mapping[key]) != sheet_id_key(target):
            raise ValueError(f'焊口图号别名存在冲突：{source}')
        mapping[key] = target
    for source, target in mapping.items():
        target_key = sheet_id_key(target)
        if target_key in mapping and target_key != source:
            raise ValueError('焊口图号别名不能形成链式或循环映射，请直接指定最终图号')
    result = []
    for record in records:
        target = mapping.get(sheet_id_key(record.sheet_id))
        if target is None:
            result.append(record)
            continue
        updates = {'sheet_id': target}
        if isinstance(record, ConstructionWeldInput):
            updates['source_sheet_id'] = record.source_sheet_id or record.sheet_id
        result.append(replace(record, **updates))
    return result


def group_construction_by_sheet(
    records: Iterable[ConstructionWeldInput],
) -> dict[str, list[ConstructionWeldInput]]:
    grouped: dict[str, list[ConstructionWeldInput]] = defaultdict(list)
    for record in records:
        grouped[sheet_id_key(record.sheet_id)].append(record)
    return dict(grouped)


def construction_records_for_sheet(
    grouped: dict[str, list[ConstructionWeldInput]], sheet_id: str
) -> list[ConstructionWeldInput]:
    """Accept either the full ISO number or the table's abbreviated suffix."""

    wanted = sheet_id_key(sheet_id)
    if wanted in grouped:
        return grouped[wanted]
    matches = [rows for key, rows in grouped.items() if wanted.endswith("-" + key)]
    if len(matches) > 1:
        raise ValueError(f"{sheet_id}匹配到多个焊口表单页图号")
    return matches[0] if matches else []


def apply_construction_welds(
    payload: dict[str, object],
    records: list[ConstructionWeldInput],
    tolerance: float = 0.10,
    coordinate_offset_tolerance: float = 1.0,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Clone mapped PCF/SHA rows and replace their number with construction IDs."""

    base_rows = list(payload.get("welds", []))
    numbered_rows: list[dict[str, object]] = []
    unresolved: list[dict[str, object]] = []
    # One visible ISO weld number can identify only one physical point on a
    # page.  Review tables can retain an EP3D-only row whose provisional ISO
    # number duplicates an already paired row.  Prefer the row whose original
    # EP3D number exactly matches the selected ISO number; otherwise retain the
    # earliest source row and send every discarded duplicate to manual review.
    selected_by_number: dict[str, ConstructionWeldInput] = {}
    for record in records:
        key = record.weld_number.strip().casefold()
        current = selected_by_number.get(key)
        if current is None:
            selected_by_number[key] = record
            continue
        record_exact = (
            record.original_weld_number.strip().casefold() == key
        )
        current_exact = (
            current.original_weld_number.strip().casefold() == key
        )
        if record_exact and not current_exact:
            selected_by_number[key] = record
    selected_ids = {id(record) for record in selected_by_number.values()}
    selected_records = [record for record in records if id(record) in selected_ids]
    for record in records:
        if id(record) in selected_ids:
            continue
        kept = selected_by_number[record.weld_number.strip().casefold()]
        exact_kept = (
            kept.original_weld_number.strip().casefold()
            == kept.weld_number.strip().casefold()
        )
        unresolved.append(
            {
                "excel_row": record.excel_row,
                "sheet_id": record.sheet_id,
                "weld_number": record.weld_number,
                "coordinate": [str(value) for value in record.coordinate],
                "reason": (
                    "同页ISO焊口号重复，已保留与EP3D焊点编号一致的记录，需人工核对"
                    if exact_kept
                    else "同页ISO焊口号重复，已保留首条记录，需人工核对"
                ),
            }
        )
    used_targets: dict[
        tuple[object, object, object], list[ConstructionWeldInput]
    ] = defaultdict(list)

    def allows_shared_root(
        existing: ConstructionWeldInput, incoming: ConstructionWeldInput
    ) -> bool:
        existing_type = existing.joining_type.strip().casefold()
        incoming_type = incoming.joining_type.strip().casefold()
        if existing_type == incoming_type == "olet":
            # Multiple OLET holes can genuinely occupy one plotted junction,
            # but a merely collapsed SHA graphic is not enough evidence.
            return max(
                abs(float(a - b))
                for a, b in zip(existing.coordinate, incoming.coordinate)
            ) <= coordinate_offset_tolerance
        if "pad" not in {existing_type, incoming_type}:
            return False
        existing_ucis = {
            uci.strip().casefold()
            for uci in (existing.material_uci_a, existing.material_uci_b)
            if uci.strip()
        }
        incoming_ucis = {
            uci.strip().casefold()
            for uci in (incoming.material_uci_a, incoming.material_uci_b)
            if uci.strip()
        }
        return bool(existing_ucis & incoming_ucis)
    for record in selected_records:
        candidates: list[tuple[float, dict[str, object]]] = []
        expected = tuple(float(value) for value in record.coordinate)
        for raw in base_rows:
            row = dict(raw)
            endpoint = row.get("pcf_endpoint")
            if not isinstance(endpoint, list) or len(endpoint) < 3:
                continue
            try:
                actual = tuple(float(endpoint[index]) for index in range(3))
            except (TypeError, ValueError):
                continue
            deltas = tuple(abs(actual[index] - expected[index]) for index in range(3))
            if max(deltas) <= tolerance:
                candidates.append((sum(delta * delta for delta in deltas), row))
        mapped = [item for item in candidates if item[1].get("graphic_ref") is not None]
        pool = mapped or candidates
        mapping_suffix = "construction-coordinate"
        if not pool:
            # EP3D exports occasionally differ from PCF coordinates by a
            # sub-millimetre rounding/precision offset.  Accept that only
            # when exactly one PCF WELD lies inside the wider envelope; never
            # pick the merely-nearest item from multiple engineering targets.
            offset_candidates: list[tuple[float, dict[str, object]]] = []
            for raw in base_rows:
                row = dict(raw)
                endpoint = row.get("pcf_endpoint")
                if not isinstance(endpoint, list) or len(endpoint) < 3:
                    continue
                try:
                    actual = tuple(float(endpoint[index]) for index in range(3))
                except (TypeError, ValueError):
                    continue
                deltas = tuple(abs(actual[index] - expected[index]) for index in range(3))
                if max(deltas) <= coordinate_offset_tolerance:
                    offset_candidates.append(
                        (sum(delta * delta for delta in deltas), row)
                    )
            unique_targets = {
                (row.get("uci"), tuple(row.get("pcf_endpoint", [])[:3]))
                for _, row in offset_candidates
            }
            if len(unique_targets) == 1:
                pool = offset_candidates
                mapping_suffix = "construction-coordinate-unique-offset<=1mm"
        if not pool:
            unresolved.append(
                {
                    "excel_row": record.excel_row,
                    "sheet_id": record.sheet_id,
                    "weld_number": record.weld_number,
                    "coordinate": [str(value) for value in record.coordinate],
                    "reason": "PCF焊口块中没有同坐标焊点",
                }
            )
            continue
        _, selected = min(pool, key=lambda item: item[0])
        # Synthetic topology points can have different graphic refs while
        # representing the same physical SHA root.  Coordinate identity is
        # therefore the authoritative uniqueness key whenever available.
        if selected.get("sha_x") is not None and selected.get("sha_y") is not None:
            target_key = (
                selected.get("page"),
                round(float(selected["sha_x"]), 1),
                round(float(selected["sha_y"]), 1),
            )
        else:
            target_key = (
                selected.get("page"),
                selected.get("graphic_ref"),
                selected.get("uci"),
            )
        occupants = used_targets[target_key]
        if occupants and not all(
            allows_shared_root(previous, record) for previous in occupants
        ):
            unresolved.append(
                {
                    "excel_row": record.excel_row,
                    "sheet_id": record.sheet_id,
                    "weld_number": record.weld_number,
                    "coordinate": [str(value) for value in record.coordinate],
                    "reason": "同一SHA焊点已分配其他焊口号，按唯一性规则忽略",
                }
            )
            continue
        occupants.append(record)
        selected.update(
            {
                "weld_number": record.weld_number,
                "weld_type": record.weld_type,
                "construction_excel_row": record.excel_row,
                "construction_sheet_id": record.sheet_id,
                "original_weld_number": record.original_weld_number,
                "number_source": record.number_source,
                "mapping_method": f'{selected.get("mapping_method", "")};{mapping_suffix}'.strip(";"),
                "confidence_status": "mapped" if selected.get("graphic_ref") is not None else "unmapped",
            }
        )
        numbered_rows.append(selected)

    callouts = [row for row in numbered_rows if row.get("confidence_status") == "mapped"]
    unmapped_count = len(numbered_rows) - len(callouts) + len(unresolved)
    output = dict(payload)
    output["number_source"] = "Chengda weld table 单页优先施工焊口号，否则焊点编号"
    # Retain the unnumbered topology rows for valve/flange/support recovery.
    # The weld injector filters on weld_number, so these do not create labels.
    output["welds"] = base_rows + numbered_rows
    output["construction_unresolved"] = unresolved
    output["summary"] = {
        "numbered_weld_count": len(records),
        "callout_target_count": len(callouts),
        "unmapped_numbered_count": unmapped_count,
        "ambiguous_count": 0,
    }
    return output, unresolved

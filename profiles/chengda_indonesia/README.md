# 成达印尼项目

本目录是成达正式代码的唯一维护位置。

- `parser/`：SHA 解析、PDF 绘制、拓扑匹配、联合标注布局、签名处理。
- `workflow/`：表格读取、PCF 反写、批量转换、审计汇总；诊断工具位于仓库 `tools/chengda/`。
- `units/`：已验证的单元入口，保留各单元编号列、排除规则和表格组合。
- `parser/tests/`：原回归测试，包含依赖本地项目图纸的集成用例。
- 仓库 `shared/`：原本已被两个项目共同使用的 PCF 工具。

## 数据与资源

默认项目目录是仓库同级的 `成达印尼项目`。可以通过环境变量改变，无需修改代码：

```powershell
$env:CHENGDA_PROJECT_DIR = 'D:\研发工作\Cursor\PCF解析\成达印尼项目'
```

单元输入、签名资源、PDF 与审计仍位于 `SHA文件转换/<单元>/`。
通用签名和特殊 SHA 结构字体存于 `本地资源/parser-assets/`，可通过
`CHENGDA_ASSET_DIR` 指定其他位置。这些本地资源不随代码上传；新机器需同时配置资源和系统字体，才能复现交付效果。

## 安装与运行

在仓库根目录执行：

```powershell
python -m pip install -r profiles/chengda_indonesia/requirements.txt
npm install
python profiles/chengda_indonesia/units/convert_000201_iso_review.py --plan-only
python profiles/chengda_indonesia/units/convert_000201_iso_review.py --workers 8 --force
```

Windows PDF 渲染沿用原来的 Microsoft Edge；其他系统需配置 Playwright 浏览器。
单元入口也接受 `--include`、`--output-dir`、`--audit-dir` 等原参数。
`workflow/convert_<单元>_*.py` 是旧路径的轻量兼容入口，不维护第二份规则。

通用入口为 `workflow/convert_chengda_weld_sha_to_pdf.py`；
无标注转换使用 `workflow/convert_chengda_sha_to_pdf.py`；
PCF 反写使用 `workflow/write_chengda_welds_to_pcf.py`。

### WS / FW 批次入口

批次入口使用 `--unit` 选择单元，不能按文件名推断编号规则：

```powershell
# WS 原始 EP3D 编号；部分单元从 EP3D tmp 批次目录读取表格
python profiles/chengda_indonesia/units/convert_ws_ep3d.py --unit 104100-WS --workers 8 --force
# WS 审核 ISO 编号；302100 无对照图范围按已确认规则回退 EP3D 编号
python profiles/chengda_indonesia/units/convert_ws_iso_review.py --unit 300500-WS --workers 8 --force
# FW 原始 EP3D 编号；PCF 使用通用目录中的分版文件夹
python profiles/chengda_indonesia/units/convert_fw_ep3d.py --unit 200500FW --workers 8 --force
```

以上入口不标管段号。原始/审核焊口表、三类管理表、单元签名目录均需本地提供；
正式输出和审计写回各单元目录。`--plan-only` 仅检查 SHA/PCF 任务发现，
`--audit-only` 执行编号匹配但不绘制 PDF，不能用它证明视觉布局已验证。
仅当输入未改动且 PDF 和逐页审计均完整时，可用 `--resume-log` 从原日志续跑。
`tools/refresh_completed_audit_summary.py` 可基于逐页证据重建完整审计；
`workflow/merge_repair_audit.py` 用于把定向修复页合并回整单元审计。

`tools/prepare_*signatures.py` 和 `tools/refresh_404049_zxe_signature.py`
保留签名准备、人工抠图复用及参考 PDF 提取方法，不包含签名图片本身。
这些资源脚本依赖对应本地参考图和已接受的签名资源，不能在空数据目录中直接复现。

## 验证

```powershell
python -m pytest profiles/chengda_indonesia/parser/tests -q
```

部分历史集成测试引用旧目录中的图纸；缺少这些本地图纸时不能以完整测试集全绿作为独立安装标准。
迁移验证结果和备份位于仓库 `.local/`，不进入 Git。

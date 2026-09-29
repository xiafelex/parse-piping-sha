# 成达印尼项目 SHA 转换程序

本目录集中保存成达印尼项目的 SHA 转换、编号标注、PCF 焊口反写及验证脚本。

## 正式入口

- `convert_chengda_sha_to_pdf.py`：不带编号的 SHA 转 PDF 通用入口。
- `convert_chengda_weld_sha_to_pdf.py`：水印、焊口及管理编号标注通用入口。
- `convert_<单元>_*.py`：已固化数据路径和编号规则的单元入口。
- `write_chengda_welds_to_pcf.py`：焊口号及 S/F 信息反写 PCF。

数据路径由 `../parser/chengda_paths.py` 管理；解析器位于 `../parser/`。
单元策略位于 `../units/`，本目录同名入口仅作兼容转发。

## 验证和诊断脚本

后续验证脚本统一放在仓库 `tools/chengda/`，建议按用途使用以下前缀：

- `analyze_`：只读分析；
- `diagnose_`：问题诊断；
- `evaluate_`：规则效果评估；
- `preflight_`：正式转换前检查。

脚本内项目根目录应使用：

```python
from _bootstrap import PROJECT_DIR, PARSER_DIR
```

不要把临时输出写入解析器目录；测试临时文件应使用系统临时目录或仓库 `.local/`。

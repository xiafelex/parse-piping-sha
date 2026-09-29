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

## 验证

```powershell
python -m pytest profiles/chengda_indonesia/parser/tests -q
```

部分历史集成测试引用旧目录中的图纸；缺少这些本地图纸时不能以完整测试集全绿作为独立安装标准。
迁移验证结果和备份位于仓库 `.local/`，不进入 Git。

# 蓝星项目（固化版本）

`parser/` 保存整合前仓库根目录的蓝星解析、焊口关联与绘图实现。
`workflow/` 保存原工作区根目录的批量 PDF/PNG 转换及成果整理入口。
`../../shared/` 保存两个项目已经共同使用的 PCF 反写工具。

本次仅调整目录与入口引用，未将成达规则合入蓝星算法。仓库根目录的旧入口保留为兼容转发，正式实现只在此处维护。

示例（从仓库根目录执行）：

```powershell
python profiles/lanxing/workflow/convert_bluestar_project_sha_to_pdf.py --help
```

蓝星输入输出默认仍位于仓库同级的 `蓝星项目`。可使用各入口的路径参数覆盖。

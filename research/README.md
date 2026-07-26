# 协作研究档案

本目录保存 `parse-piping-sha` 课题实际使用的输入样本和可复核的过程工件，
用于同事复现分析、验证假设和继续开发。所有内容均按“源文件、派生工件、
研究结论”分层，避免把视觉对比或启发式关联误写成文件事实。

## 目录

```text
research/
  samples/                    # 原始 PCF/SHA/PDF 的只读副本
  artifacts/                  # 仅由 SHA 解析器产生的 SVG/PNG/Trace/PSM 诊断
  RESEARCH_MANIFEST.sha256    # 本目录所有已归档文件的完整性清单
  OUT_OF_SCOPE_CORPUS.md      # 未纳入 Git 的大批量来源目录
```

### 核心样本

| 样本 | 用途 | 文件 |
|---|---|---|
| `100P3A-CHW-279001-01` | UCI、图上文字、方框标注与 PCF/SHA 对照的起点 | PCF + SHA + PDF |
| `N400P3A-HOSO-N419009-01` | 两页同一管线拆图与内部接口定位 | PCF + SHA + PDF |
| `N400P3A-AMSS2-N444201-01` | 五页 SHA、多页 SVG 重建、PSM 层级诊断及 App 端到端验证 | PCF + SHA + PDF |

## 证据规则

- `samples/*.sha`、`samples/*.pcf`、`samples/*.pdf` 是原始副本，禁止原地修改。
- PDF 仅用于视觉 QA；不能用 PDF 的文字、像素、路径或坐标反向定位 SVG。
- `artifacts/` 全部应可由 SHA 和仓库脚本重新生成；其文件名和 Trace JSON
  保留页面、Sheet、UCI、PSM 或图元来源。
- UCI 是强模型对象实例键，但不自动等同于物料编码、资产码或“一物一码”。
- `direct` 仅表示 PCF UCI 与 SHA dynamic attribute 的直接引用链；由 PSM
  包围盒重叠得到的关联必须保留为 `candidate`。

## 快速复现

```bash
python3 -m pip install -r requirements.txt

# 五页 SHA 重建与 Trace
python3 run_sha_iso_render.py \
  research/samples/N400P3A-AMSS2-N444201-01/N400P3A-AMSS2-N444201-01-0.sha \
  --all-pages --out-dir output/reproduction

# PCF/SHA UCI、Sheet 与同线分图候选
python3 analyze_iso_split.py \
  research/samples/N400P3A-AMSS2-N444201-01/N400P3A-AMSS2-N444201-01.pcf \
  research/samples/N400P3A-AMSS2-N444201-01/N400P3A-AMSS2-N444201-01-0.sha

# 验证归档文件未被更改
shasum -a 256 -c research/RESEARCH_MANIFEST.sha256
```

研究经过、假设变化、已解决问题与待解码问题见
[`docs/RESEARCH_JOURNEY_CN.md`](../docs/RESEARCH_JOURNEY_CN.md)。

# 十个 SHA/PCF 样本的左侧文字试验

## 范围

- 样本：`10` 个有同名 SHA、PCF、PDF 的 N400 管线文件。
- 目标文字：左侧单线图的 `INSUL`、`CLASS`、`TRACE`。
- SHA/PCF：PCF 只确认同一管线文件配对，不提供图纸坐标、文字或几何。
- PDF：只用于目视验收，不参与任何渲染或规则计算。

## 样本量

| 项目 | 数量 |
| --- | ---: |
| SHA/PCF 成对文件 | 10 |
| 含目标文字的物理 Sheet | 38 |
| `INSUL` 文字 | 270 |
| `CLASS` 文字 | 47 |
| `TRACE` 文字 | 144 |
| 总目标文字 | 461 |
| 满足“自由文字锚点”严格条件的文字 | 397 |

样本包含：`N504601`、`N494401`、`N434201`、`N423001`、`N433202`、
`N434281`、`N424201`、`N441001`、`N481201`、`N493020`。

## 试验结果

对 `397` 个候选对象仅启用 `--anchor-left-free-text`，并限定前缀为
`INSUL`、`CLASS`、`TRACE`。规则只读取 SHA 的 Sheet 文字锚点与 PSM 字框；
默认渲染没有改变。

结果：**不接受为正式修复，确认批量修复数量为 0。**

跨文件抽取 `29` 个代表对象进行 PDF 目视验收后发现，候选对象虽然具有重复的
“PSM 字框相对 Sheet 锚点”偏移，但其中许多是成组方框、引线或多行说明的一部分。
将文字直接移动到 Sheet 锚点会使其偏离原框体，或与相邻行重叠。原始 PSM 位置在
这些样本上整体更接近 PDF。

这证明“重复偏移”本身不能被理解为“需要平移的错误”。对该三类文字，下一步应
优先解析其方框/引线/多行组的 PSM 父子关系；只有证明某个文字不属于该类布局组，
才可以单独使用 Sheet 锚点。

## 可复跑命令

```bash
python3 build_left_text_sample.py \
  output/n400_corpus_audit/audit_manifest.json \
  --limit 10 --output output/left_text_ten_sample/selection.json

python3 render_left_text_trial.py \
  output/left_text_ten_sample/selection.json \
  --out-dir output/left_text_ten_sample/trial

NODE_PATH=<playwright-node_modules> node render_svg_tree_png.cjs \
  output/left_text_ten_sample/trial output/left_text_ten_sample/trial 1600

python3 build_left_text_trial_qa.py \
  output/n400_corpus_audit/audit_manifest.json \
  output/left_text_ten_sample/trial/trial_manifest.json \
  --out-dir output/left_text_ten_sample/qa
```

对照板中的左侧均为 SHA-only 试验渲染，右侧为 PDF 视觉参考；PDF 不得反向输入
任何 SHA 或 SVG 坐标计算。

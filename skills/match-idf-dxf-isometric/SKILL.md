---
name: match-idf-dxf-isometric
description: Match one single-page piping IDF to its already classified DXF isometric drawing by numbering IDF 100 straight pipes and 120 welds, reconstructing both typed graphs, and producing confidence-scored visual correspondences. Use when an IDF and one corresponding DXF page need weld/straight-pipe reconciliation, topology verification, or numbered comparison images. Do not use for multi-page IDF line splits until the single-page result is verified.
---

# IDF–DXF 单页拓扑匹配

仅处理“一个 IDF 管线范围 ↔ 一张 DXF 轴测图”的第一阶段。构件、焊缝、支架、箭头、弯头和最终直管必须先由 `parse-piping-dwg-dxf` 完成矢量识别；本技能不重新从像素或文字推断它们。

读取 [single-page-contract.md](references/single-page-contract.md) 后开始。

## 输入门槛

- 保留原始 IDF、原始 DXF 与其构件识别 JSON；所有派生文件另存。
- 确认 IDF 范围只对应一页 DXF，且图纸没有 `CONT. ON DRG`、跨页连接或未展开的同线续页。若不满足，输出 `multi_page_not_eligible`，不得强行匹配。
- DXF 必须已有带 source filename、DXF handle、端点锚点、构件类别和管段端点角色的审计记录。
- `CUT PIPE LENGTH`、材料表、尺寸文字仅可用于最终审计，不可作为建立对应关系的依据。

## 单页流程

1. 解析 IDF，保留原始文件顺序、行号、原始文本与坐标。对每条 `100` 直管分配 `I001…`，对每条 `120` 焊缝分配 `W001…`；编号稳定且不可因后续匹配重排。
2. 构建 IDF 图：`100` 是直管边，`120` 是焊缝节点；保留相邻非 `100`/`120` 构件为类型节点和连接约束。
3. 从 DXF 构件识别审计构建图：最终直管是边，已确认焊缝是节点，支架/法兰/变径/阀门/直管台/三通/弯头是类型节点。箭头只收缩为边内部连续性，支架必须切断边。
4. 分别规范化图形：允许轴测旋转、镜像与整体比例变化；不得把 IDF 三维绝对坐标投影到 DXF 图纸坐标。
5. 先匹配稀有锚点，再扩展相邻边：构件类型与度数 → 焊缝/端点签名 → 分支顺序 → 转向序列 → 管径变化 → 相对长度。长度只用于消除拓扑同分候选。
6. 对每个 `I###` 和 `W###` 输出最佳 DXF 候选、备选、得分、得分差与置信度。结构冲突、并列最高或缺少证据时标为 `unresolved`，不得强配。
7. 生成复核图：完整 IDF 拓扑编号图、完整 DXF 编号叠加图、以及每个非平凡匹配的成对局部图。

## 匹配约束

- `I###` 只能匹配一条已识别的最终 DXF 直管；不可匹配弯头内部向量、箭头图元、注释线或未分类 raw pipe。
- `W###` 只能匹配 DXF 的矢量焊缝锚点；不可匹配引出圆、文字编号或管段中点。
- 支架是 DXF 物理切断，但不自动等价于 IDF 焊缝。支架两侧必须作为不同 DXF 边参与匹配。
- 一个 IDF 100 可以由多个仅为 CAD 分解的 DXF 原始向量组成；不可跨越确认的焊缝、支架或构件来合并。
- 若 IDF 没有显式的第三支管边，DXF 的支管台/三通不得凭二维外形增补一条 `100`。

## 置信度与验收

- `high`：唯一的局部拓扑映射，端点类型/构件顺序一致，且分数差明确。
- `medium`：结构一致但存在一个非关键属性缺失或长度接近的替代候选。
- `low`：仅有局部形态支持；保留候选，不计为已对应。
- `unresolved`：冲突、并列或 IDF/DXF 图不等价。

验收时分别报告：IDF `100` 总数、已匹配/未匹配 `100`；IDF `120` 总数、已匹配/未匹配 `120`；DXF 可匹配直管/焊缝中未使用的数量。总数相同不能替代逐条对应验证。

## 输出合同

为每次运行写出：

- `idf-numbered.json`：`I###` / `W###`、原始行号、属性、IDF 邻接关系。
- `dxf-typed-graph.json`：DXF source+handle、锚点、节点/边类型与邻接关系。
- `match-audit.json` 或 CSV：每个 IDF 编号的候选、得分分解、置信度与拒绝理由。
- `idf-numbered.png`、`dxf-numbered-overlay.png`、`paired-local-*.png`。
- `summary.md`：范围资格、数量、置信度分布、未解冲突与人工复核问题。

在用户确认局部编号后，再把该案例作为回归证据记录；不要把文件名、DXF handle 或具体编号顺序提升为通用规则。

---
name: match-idf-dxf-isometric
description: Reconcile IDF piping straight-pipe records with already classified DXF isometric pages: inventory IDF↔DXF page membership, audit IDF 100 counts, identify continuation/support/terminal causes of count differences, and produce confidence-scored numbered topology correspondences. Use after DXF semantic recognition is complete; keep this skill separate from DXF element recognition. Do not match IDF 120 welds unless the user explicitly expands the scope beyond 100.
---

# IDF–DXF `100` 拓扑核对

## 强制边界

- 本 skill 只消费已生成的 DXF 图元语义记录；不修改、不补充、也不重新定义 `dxf-piping-isometric` 的焊缝、支架、弯头、法兰或直管识别规则。
- 本 skill 的工作对象是 **IDF `100` 与 DXF 最终直管图的对应**：文件/页面归属、续页关系、数量差异归因和逐条拓扑匹配。
- 当前阶段只处理 `100`。`120` 焊缝编号和匹配是独立的后续授权，不得因本 skill 被触发而自动开始。
- 所有匹配输出写入独立目录；不得向原始 DXF 或图元识别审计文件回写匹配结论。

单页是可先验证的子流程；多页先做页面归属和差异归因。构件、焊缝、支架、箭头、弯头和最终直管必须先由 `dxf-piping-isometric` 完成矢量识别；本技能不重新从像素或文字推断它们。

读取 [single-page-contract.md](references/single-page-contract.md) 后开始。

## 输入门槛

- 保留原始 IDF、原始 DXF 与其构件识别 JSON；所有派生文件另存。
- 确认 IDF 范围只对应一页 DXF，且图纸没有 `CONT. ON DRG`、跨页连接或未展开的同线续页。必须直接扫描 DXF 的 `TEXT` 与 `MTEXT`，不能仅以目录中同名 DXF 文件数量判定；若不满足，输出 `multi_page_not_eligible`，不得强行匹配。
- DXF 必须已有带 source filename、DXF handle、端点锚点、构件类别和管段端点角色的审计记录。
- `CUT PIPE LENGTH`、材料表、尺寸文字仅可用于最终审计，不可作为建立对应关系的依据。

## 全量范围盘点（先于逐页匹配）

当用户要求“所有 IDF 与 DXF 对应关系”时，先运行
`scripts/inventory_idf_dxf_pages.py <idf-root> <dxf-root> --output-dir <dir>`。

- 该脚本以源文件名中的管线键建立 **页面候选归属**，输出每条 IDF 的所有 DXF 页码、IDF 重复文件、无图候选和单页 `CONT. ON` 风险。
- 这是范围盘点，不是 `100` 的对应结论；生成的 `matching_basis` 必须保持为 `source filename line key only`。
- 单页资格仍须扫描 DXF 的 `TEXT`/`MTEXT` 是否含 `CONT. ON`；一张同名文件不等于闭合单页。
- 只有 `single_closed_candidate` 可直接进入 `CHAIN_100_V1` / `SUPPORT_CONTRACTION_CHAIN_V1`。`multi_page_candidate` 必须先完成 IDF `100` 到 DXF 页的分区，`no_dxf_candidate` 必须报告缺图，不能虚构匹配。

### 多页 `100` 数量预审

在多页分区前，运行
`scripts/summarize_multi_page_100_counts.py <page-inventory.json> <page-pipe-counts.jsonl> <idf-root> --output <summary.json>`。

- 输出每条多页线的 IDF 有效 `100` 数、各页已识别最终直管片段总数、支架相关片段数、零直管页和缺失统计页。
- **不得**把这两个总数直接视为同一指标：DXF 总数会受页面切分和 `SUPPORT_*` 片段影响。结果相等仅意味着该线可优先作为“页面分区算法”的验证样本，不能直接宣称已逐条对应。
- 若存在缺失统计页或未解决直管，停止在数量审计并先补齐 DXF 识别；若所有页已统计，下一步才是为每条 IDF `100` 估计候选页集合。

### 多页差异归因（不得直接扣减）

运行 `scripts/attribute_multi_page_100_difference.py`，将数量差异拆为证据账本：

- 跨页 `CONT. ON` → `CONT. FROM` 中同类端头的高/中置信重复候选；
- 箭头连续片段数量；
- 支架相关片段数量；
- 空端片段数量；
- 未解决直管数量。

`EMPTY`、`SUPPORT_*`、`ARROW_PIPE` 的出现只表示需要复核，**不得**直接从 DXF 总数扣除或默认合并。只有同一明确续页边上、端头语义一致、几何长度一致，且最好保留相同 source handle 的候选，才可标为高置信“跨页重复候选”；即使如此也先记录，待图形复核后才能调整 `100` 统计。

## 单页流程

1. 解析 IDF，保留原始文件顺序、行号、原始文本与坐标。对每条 `100` 直管分配 `I001…`，对每条 `120` 焊缝分配 `W001…`；编号稳定且不可因后续匹配重排。
2. 构建 IDF 图：`100` 是直管边，`120` 是焊缝节点；保留相邻非 `100`/`120` 构件为类型节点和连接约束。
3. 从 DXF 构件识别审计构建图：最终直管是边，已确认焊缝是节点，支架/法兰/变径/阀门/直管台/三通/弯头是类型节点。箭头只收缩为边内部连续性，支架必须切断边。
4. 分别规范化图形：允许轴测旋转、镜像与整体比例变化；不得把 IDF 三维绝对坐标投影到 DXF 图纸坐标。
5. 先运行 `CHAIN_100_V1` 的确定性单链算法；若仅因已确认支架造成 DXF 片段数偏多，再运行下述 `SUPPORT_CONTRACTION_CHAIN_V1` 验证模型。不得手工指定 `I### → DXF handle` 作为算法结果。
6. 先匹配稀有锚点，再扩展相邻边：构件类型与度数 → 焊缝/端点签名 → 分支顺序 → 转向序列 → 管径变化 → 相对长度。长度只用于消除拓扑同分候选。
7. 对每个 `I###` 和 `W###` 输出最佳 DXF 候选、备选、得分、得分差与置信度。结构冲突、并列最高或缺少证据时标为 `unresolved`，不得强配。
8. 生成复核图：完整 IDF 拓扑编号图、完整 DXF 编号叠加图、以及每个非平凡匹配的成对局部图。

## `CHAIN_100_V1`：首个可复跑算法

只在两侧均能归约为一条无分支路径、且 `100` 数量等于可匹配 DXF 最终直管数量时运行。

1. 从 IDF 的有效几何记录序列构建路径：`100` 是候选边，夹在相邻 `100` 之间的几何记录归为连接器上下文；如果某个 `100` 有超过两个邻接方向或存在未展开支路，拒绝该页。
2. 从 DXF typed-graph 读取最终直管及其源向量端点。连接最近的一对端点时，要求间隙由已识别的弯头/构件/焊缝边界解释；不以文字、尺寸或表格建立邻接。若不能形成唯一链，拒绝该页。
3. 仅枚举两种链方向（正向、反向）。对每个方向按位置逐一配对；禁止搜索任意排列来凑长度。
4. 对每个候选对累加词典序分量：端点连接器类别、直/弯转向上下文、管径变化、DXF 管段角色；仅在这些相等时，比较归一化相对长度误差。
5. 选择总分最高的方向；输出另一方向的分数和差值。方向差不足或任一局部结构冲突时，标记 `unresolved`。

若 DXF 最终直管数高于 IDF `100` 数，先报告 `count_mismatch_support_segmentation`，并列出 DXF 的 `SUPPORT_*` 管段数；不得为凑数量跨越支架自动合并。是否允许“一个 IDF 100 对多个由支架切开的 DXF 管段”是下一版本的单独模型选择，必须先经人工验证。

## `SUPPORT_CONTRACTION_CHAIN_V1`：支架切分验证模型

这是在两张已验证的单页图（`VT200001`：8 个 DXF 片段→4 个 IDF `100`；`VT200002`：9→5）中得到的窄范围模型；它解释的是 **DXF 的支架绘法把一条 IDF 直管拆成多个图形片段**，不改变 DXF 中“支架是物理切点”的构件语义。

1. 先按 `CHAIN_100_V1` 的端点图建立未收缩的 DXF 单链。只在相邻两个最终管段有同一矢量端点（≤0.15 DXF 单位）时考虑收缩。
2. 共享端点必须在 **两侧** `endpoint_annotations` 中都被确认为 `SUPPORT`。只看颜色、文字、最近引线、管段中点或“靠近支架”均不合格。
3. 不得跨焊缝、弯头、法兰、变径、阀门、直管台、三通、空端或任何未分类节点。特别地，`SUPPORT_EMPTY_PIPE` 永不与相邻片段收缩：它的空端是拓扑证据。
4. 将可收缩的连续片段收成 `G###`；每组仍保留全部 `C###`、handle、端点及每一处支架收缩证据。只有收缩后组数恰等于 IDF `100` 数，才比较正向/反向两种链方向。
5. 得分仍先看箭头/端点/顺序，再看归一化相对长度；输出 `I### → G### → [C###...]`，另一方向得分、差值和整图叠加。不能满足任一硬条件时必须保留 `not_aggregate_eligible`。

运行 `scripts/aggregate_support_chain_v1.py` 产生审计 JSON；运行 `scripts/render_support_contraction_overlay.py` 产生源矢量叠加图。该模型目前是人工已验证的回归规则，但必须继续以新单页案例复核，不能据此把所有支架都当作可忽略断点。

`CHAIN_100_V1` 的输出是算法候选，不是人工确认。只有用户确认整图与局部图后才升级为回归样本。

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

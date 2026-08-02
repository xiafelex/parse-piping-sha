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
- 若同一文件名管线键命中多个 IDF，输出全部 `idf_100_candidates` 并将
  `idf_100_count` 置为 `null`；**禁止取最大值、最小值或目录顺序第一份**作为匹配基准。先用规格和后续拓扑锚点选定 IDF，再进入数量比较。
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

### 多页拓扑锚点（数量相等后仍必须执行）

多页线即使 IDF `100` 数和 DXF 最终直管数相等，也**不能**按 IDF 文件顺序与 DXF 页序强行配对。先运行：

- `scripts/build_idf_100_topology.py <idf> --output <idf-topology.json>`；
- `scripts/summarize_dxf_semantic_components.py <page.dxf> --adapter <dxf-semantic-adapter.py> --output <page-topology.json>`。

后者只序列化已有的 DXF 语义分类器输出；它绝不包含或替代图元识别规则。

1. 在当前已验证 IDF 样本中，记录码 `41` 的两端构成分支/支管锚点：三度端为 `junction`，另一端为 `outlet_leg`。这是一条**待继续跨项目验证的 IDF 观察规则**，不是对所有 IDF 代码的无条件解释。
2. 码 `55` 虽也可出现在普通两端内联几何中，不能单独用于判为分支；以它为锚点会产生已知假阳性。
   同理，不得把所有非 `100` 记录自动收缩为一个拓扑节点：当前样本中泛用码
   `35`/`36`/`150` 会错误连通无关路由。当前仅允许已验证的 `41` 作为分支连接器收缩。
3. 先比较 IDF 的 `junction` 数与 DXF 已确认 `branch`/`tee` 数。数量不一致时，输出 `branch_anchor_mismatch`，保留候选，禁止以直管总数相等为由强配。
4. 数量一致时，从稀有分支锚点向各 leg 扩展，比较相邻 `100` 的端点构件签名、每页的已确认构件序列和局部管段数；长度仅作为最后的消歧特征。页面边界和 IDF 源行顺序都不是拓扑顺序。
5. `match_multipage_chain_100_v1.py` 仅适用于无分支的显式页序链。只要出现 `junction`，必须返回 `not_chain_eligible` 并转入本节的锚点分区，不得降级为文件顺序串接。
6. 运行 `extract_idf_branch_legs.py` 输出每个 `41` 三度节点直接相邻的稳定 `I###`。这是
   “IDF 编号在 DXF 分支局部出现”的候选集合；尚未有唯一 DXF 证据时，不得将其中任何
   一条强行写为最终 `I### → C###`。
7. `match_branch_outlet_candidates_v1.py` 只允许匹配唯一的 `outlet_leg`：DXF 已确认
   `branch/tee` body 必须与一条 typed pipe 的端点直接矢量接触（默认 ≤1.1 DXF 单位）。
   同时唯一时可输出 `medium` 候选；两条 main leg 不可按距离或页序猜测，必须保留
   `unresolved`，直到完整的构件—焊缝—管段邻接图已建立。
   已回归验证的正例为 CWR (`I014 → 1A6CB`) 与 CWS (`I008 → 1A85F`)，两者均为
   `SUPPORT_WELD_PIPE` 且 branch-body 接触距离为 0。DR200001 同时有 5 个分支，
   此 v1 必须拒绝（而不是任意选一个）并等待多锚点全局分配。
8. 先运行 `build_dxf_semantic_adjacency.py`，由 typed pipe 的**源矢量端点**到已确认
   构件 outline/weld boundary 的距离建立邻接边（默认 ≤1.1）。邻近文字、尺寸、图框、
   或仅在同一局部截图中出现的图元一律不能建边。该图是多分支全局分配的输入；没有它，
   不得从“离 branch 最近”推断 main leg。
   当 IDF/DXF 分支数量一致但仅部分 DXF branch 具有直接管段接触时，状态为
   `branch_anchor_partial_observability`：仅输出有直接证据的候选，其余不进入二分图
   强制分配，须等待跨页连接或相邻构件证据。

### 多页全局图与页内回写（`GLOBAL_PAGE_PARTITION_100_V1`）

多页 ISO 必须先成为 **一个 IDF 全局坐标拓扑图** 与 **一个多页 DXF 语义图**；每张 DXF
仍是自己的图框坐标，禁止将第 001 页坐标直接平移或缩放到第 002 页。

1. `build_idf_100_topology.py` 输出稳定 `I###`、`100` 图和 `raw_geometry_graph`。后者保留
   `35/36/41/55/105/110/130/150` 等原始码，供局部序列对比；不得泛化收缩它们。
2. `summarize_dxf_semantic_components.py`（全量刷新时用
   `batch_summarize_dxf_semantic_components.py`）必须保留每条已分类管段的
   `source_vector_segments`。`ARROW_PIPE` 取两端最外侧源矢量端点，箭头仍是透明连续性，
   不是 IDF 构件或端点。
3. 首先用 `fit_idf_dxf_page_geometry_v1.py` 与页内图特征找候选子图：IDF 轴测投影的
   三轴方向/相对位置、分支度数、变径/法兰/弯头序列、端点类型、管径变化和相对长度。
   它不读 `CONT.`；仅输出待构件拓扑复核的几何假设，不能直接写最终编号。
4. `build_multipage_dxf_global_graph.py --line <KEY> ...` 建页内端点/构件接触、续页端口和
   terminal 候选。`CONT. ON/FROM` 只用于**验证或消歧**已经由图拓扑提出的跨页拼接，
   不能作为主匹配特征，也不可自动去重或合并。
5. `partition_idf_100_across_dxf_pages_v1.py` 仅在“已由图拓扑支持”的实际有图形页形成唯一
   有向路径、且全局 typed-pipe 总数等于 IDF `100` 数时，输出连续 `I###` 的**页范围候选**。
   零图形续页必须保留为上下文，不可分配 `I###`。
6. 页范围不是 `I### → handle`。只有已验证的 branch-body—pipe 端点直接接触才可在范围内
   写 `medium_anchor_candidate`；其余保持 `unresolved`，等待从锚点按构件顺序、转向和端点
   签名做全局传播。
7. 最终得到 `I### → source page + DXF handle(s)` 后，才逐页调用
   `render_idf_dxf_match_overlay.py` 回写原图；绝不可在跨页拼贴坐标中定位原始 DXF。

### 构件骨架优先（`COMPONENT_FIRST_FRAME_GRAPH_V1`）

初始阶段不要求每条 DXF pipe 与一个 IDF `100` 一一对应。先运行：

- `build_idf_pipe_component_topology.py`：以每个非 `100` 连通记录簇为连接器超边，保留
  原始 record code；`41` 的三度连接器是 `junction_3`，两条非平行 `100` 臂的连接器是
  `turn_2`，但不把任一 IDF code 自动命名为弯头/法兰。
- `build_dxf_pipe_topology_graph.py`：从已确认的 DXF 构件接触和精确端点得到
  pipe—component—pipe 超边；焊缝接触需保留为边界证据，不能无条件把两边 pipe 合并。
- `build_component_frame_graphs.py`：把 IDF 的 junction/turn/bore-change 框架与 DXF 的
  branch/tee/elbow/reducer/flange/valve 框架分别输出。

匹配层序列化 DXF topology 时，`pipe.endpoints` 必须优先保留原字段；若该字段为空而已有图元识别层
给出的 `endpoint_annotations[].point`，可原样恢复这两个 source-vector 端点。这是已确认语义证据
的保真，不是重新按图像/文字识别。941 全量刷新后，17 条线的 `1031` 个 pipe 中有 `833` 个保留
可用于精确连续性审计的端点；DR201010 从 `0/69` 恢复为 `50/69`。未有任意注释端点的 pipe 必须
仍保持无端点，不能由图框位置或最近文字补造。

匹配顺序：唯一三通/支管 → 稀有变径或阀门—法兰组合 → 弯头转向序列 → 两框架之间的
pipe 相对长度/方向。先得到“本 DXF 页是 IDF 全图的哪个子图”的候选；再从已确认框架
沿边扩展 `I###`。一开始允许漏/多 pipe、支架分段和跨页显示重叠；这些属于边的证据，
不能推翻已经唯一的构件骨架。`CONT.` 只能在骨架候选产生后验证拼接。

页内候选用 `score_dxf_page_idf_frame_windows_v1.py` 生成后，运行
`solve_global_frame_window_cover_v1.py` 将各页候选作为一个全局组合求解：优先选择覆盖完整
`I001…I###`、不重叠的组合，然后才比较局部构件分数。该选择仍只是“DXF 页对应 IDF 全图
哪个子图”的候选，不是逐 `I### → DXF handle`。若缺覆盖或重叠，必须输出
`topology_global_partial_cover_candidate`，不可为了凑覆盖改用 CONT 或文件顺序。

若 global cover 仍有多个完整候选，可从**原始 IDF 重建且保留 centre 的 frame graph**后运行
`score_frame_geometry_page_ranges_v1.py <frame-graph> <global-cover> --axis-transform <independently-calibrated-D4>`。
它只比较同页唯一 `junction_3` 到两个及以上 `turn_2` 与 DXF `branch/tee` 到 `elbow` 的相对方向，
并同时报告每页 `100` 数和 DXF P 数的偏差；输出仍只能选择/拒绝**页范围 cover**，不得写 I→P。
由于支架会把一个 IDF `100` 分成多个 DXF P，P 数偏差只能给几何同分候选排**人工复核优先级**；
不得把它提升成唯一 cover，状态必须为 `geometry_tied_pipe_count_preference_only`。
DR201010 回归中它以 `flip_y` 明确拒绝 p2 的 `I026…I037`（均值 `-.56274`），支持 `I013…I024`
（`.99364`）；但 p3/p4 边界的两种 cover 仍同分，必须保持 `geometry_non_discriminating`，不能借
总数、页序或箭头强配。

当全局 cover 未解、但**某一页**对所有不同范围有 `≥.10` 的独立几何分差时，运行
`derive_local_geometry_page_cover_v1.py <global-cover> <geometry-audit> --page N`。其输出为
`local_geometry_page_range_validated`，只能传给 `propagate_page_frame_anchors_v1.py --page N`；
propagator必须拒绝任何其它页，且输出 policy 明示不对整条线或相邻页作结论。DR201010 p2 回归：
范围 `I013…I024` 的几何分差 `1.55638`，唯一 frame/outlet 传播得到 `I020→P008`；原始 DXF
全页 overlay 已复核，仍有 `11/12` 管段保持 unresolved。这是局部编号增量，绝非五页全局闭合。

若最高分的多个范围只在首/末边界不同，但有连续的共同内部，并且该高分簇相对下一几何簇仍有
`≥.10` 分差，可附加 `--allow-boundary-intersection`。输出必须为
`local_geometry_page_interior_validated`，只包含共同 `I###` 区间；不允许声明边界 pipe 属于该页。
DR201010 p3 回归：`I025…I037` 与 `I025…I036` 并列，安全内部为 `I025…I036`，相对低分范围
`I014…I025` 的分差 `.32435`；唯一 outlet 传播得到 `I028→P005`，`I037` 保持未解。

当且仅当得到 `topology_global_unique_exact_cover_candidate`，才可运行
`propagate_page_frame_anchors_v1.py` 做第二层的逐段候选传播：唯一 `reducer ↔ bore_change`、
`elbow ↔ turn_2`、`branch/tee ↔ junction_3` 为起点；若某个已匹配的二度构件另一侧管段
唯一，则扩展一条候选。分支的三条 leg 不得凭页序排列，只有已审计的 source-vector outlet
直接接触可播种一条 `medium` pipe 候选。每次传播必须保留未解项，不得将“页范围已定位”
误报为“全部 I### 已定位”。

传播 frame 必须两侧均有已定义且相同的语义类（当前为 `junction`、`elbow`、`reducer`）。
`terminal_1`、焊缝或任何未命名 frame 的类别值都可能为空；空值相等不是构件匹配，严禁
以 `None == None` 创建传播 seed，否则会把 IDF 终端伪配为 DXF 焊缝并污染后续直管编号。

`propagate_page_frame_anchors_v1.py` 的三度臂方向匹配默认**关闭**；必须显式提供
`--axis-transform <identity|flip_x|flip_y|...>`，且该变换只能来自同一项目的独立审计锚点。
不可用“本次由三通方向推得的 I###→pipe”反向证明该镜像/旋转；那是循环证据。未获得两个
以上方向独立、且最佳变换相对次优变换有明确差值的样本时，三度臂只能输出条件候选或
`unresolved`。

同类重复的 `junction`、`elbow`、`reducer` 也不能默认按 DXF 页面坐标的相对方向配对；
`positional_seed_pairs` 只有在显式传入已审计的 `--axis-transform` 后才可使用。未校准时，
唯一构件类别和已锁定的二度链仍可传播，但重复 frame 必须保留为未解候选，不能以默认
`identity` 方向误建 IDF frame→DXF frame 对应。

`IDF41_OUTLET_TO_UNIQUE_SUPPORT_WELD_V1` 是三度臂的另一条、**不依赖投影方向**的窄规则：
原始 IDF `41` 拓扑必须明确给出且仅给出一个 `outlet_leg`；已配对 DXF `branch/tee` 的三条
incident pipe 中必须恰有一条 `SUPPORT_WELD_PIPE`，才可输出该 `I### → DXF pipe` 的 `medium`
候选。全箭头支路、零条/多条 `SUPPORT_WELD_PIPE`、无 `41` outlet 或未配对 frame 一律
`unresolved`。当前正例为 CWR `I014→P004`、CWS `I008→P008`，并在 DR200001 得到待人工
复核的 `I014→P014`；尚未积累反例，故不得将此规则泛化为“所有支管必然接支架—焊缝直管”。

`RAW41_BRANCH_CONTINUATION_HYPOTHESIS_V1` 只生成复核假设：若页范围内唯一的 IDF
`junction_3` 有且仅有一条臂通向 degree-2、raw code 恰为 `[41]` 的 connector，且唯一的 DXF
`branch/tee` 有且仅有一条臂通向 degree-2 DXF `branch/tee`，则报告这条 `I###→DXF pipe` 与
frame 对作为 `low / candidate_requires_visual_review`。它利用的是“已展开/未展开的支管连续性”
的局部拓扑，完全不使用页坐标、页序或 `CONT.`；不得直接写入 `propagate` 的最终匹配，更不能
把所有 degree-2 code `41` 命名为通用三通。当前仅在 DR200001 p1 产生 `I003→P002` 候选，
CWR/CWS 回归为零触发。

运行 `score_page_pipe_correspondence_candidates_v1.py` 生成每个 `I###` 的候选矩阵：匹配的
component-frame 邻接、八种轴向变换下的方向余弦、现有独立锚点、候选分数与分差。该脚本
只把已审计 outlet/唯一构件链计入“校准证据”；`degree3_projected_arm_direction` 只标为
`conditional_medium_candidate`。没有唯一轴向校准时，方向余弦仅供复核，不能增加候选的
匹配分数或产生最终编号。

该矩阵还必须对已匹配构件 centre 的两两**相对位移**计算八种投影变换的 RANSAC 式内点数
（方向余弦 ≥ 0.9）。构件 outline centre 的定义或局部简化会形成离群点；因此看内点数和
内点均值，不以所有位移的平均值硬拟合。至少 3 个独立 frame，且最佳变换比次优变换多
2 个内点，才可标为 `unique_project_axis_candidate`；否则只能保留轴向假设，不能喂回编号。

项目级轴向校准必须使用**已经由非方向证据完成的页面**，运行
`calibrate_project_axis_from_verified_frame_maps_v1.py --sample <frame-graph-A.json> <verified-A.json>
--sample <frame-graph-B.json> <verified-B.json> --output <calibration.json>`。它比较 frame centre
的两两相对位移，而非绝对图框位置；至少两张独立页面有 inlier，最佳 D4 变换要比第二名多
至少两个 `cos ≥ 0.9` 观测，才输出 `project_axis_calibrated`。候选页不得给自己校准。当前
941 项目以 CWR200001 p1 和 CWS200001 p3 的独立闭合编号链得到 `flip_y`（16 个高一致性
观测，对次优 identity 多 3 个）；它可作为 DR200001 p1 三通臂方向的独立输入。

这是**带属性的几何误差容忍子图匹配**，而非按文件顺序的链匹配：IDF/DXF 的节点属性包括
构件类、度数、管径变化和端点角色；边属性包括连接关系、三轴方向、相对长度和页内坐标。
先收缩已证明只是 CAD 分割的低重要度节点，再保留 weld/support/branch 为硬边界；这相当于
homeomorphic/graph-edit matching 的受限工程版本。重复的三通或弯头不以编号顺序消歧：将
IDF connector centre 作标准轴测投影，并在 `identity/flip/swap` 的离散轴向变换中比较同类
构件之间的相对方向；仅当变换本身已有独立审计证据、且臂排列最佳方案与第二名有明确分差
时播种 frame/pipe。该校准是项目级经验，须以已审计的 reducer/elbow 链复核，不能跨项目盲用。

在 frame 全局覆盖已经唯一后，可先运行
`enumerate_attributed_skeleton_candidates_v1.py <frame-graph.json> <global-cover.json>
--page N --output <json>`。它实现的是依赖最小的 maximum-common-subgraph 候选层：只把
`junction`、`elbow`、`reducer` 作为 landmark，穿过未命名的 IDF connector、DXF weld 与
DXF 的图纸拆分节点；以 landmark 类型、度数、可达 landmark 邻域和空端臂为属性计分。
对于每一个结构同分的候选，它枚举八种轴测 D4 方向变换，以**已映射 frame 之间的相对位移
余弦**作有界排序项。该变换只是 `geometry_axis_hypothesis`，不是项目校准，不能直接触发
frame/pipe 的最终写入。

这是处理“支架切分、焊缝/connector 中间节点差异、局部缺框架”的 error-tolerant edit 模型：
遗漏 landmark 有显式负分，而非让整页 exact-isomorphism 失败。若第一、二名 score 差小，或
候选只依赖一对 landmark 的相对向量，则输出仍是 `review-only`；必须结合 vector-anchored
局部图、独立 outlet/二度链证据，才可将其中任一 frame map 作为后续 `I### → P###` 传播 seed。
即使项目级 D4 轴向已独立校准，亦不得仅因“该轴向下只剩一个 skeleton candidate”提升 frame
map：页界漏画、支架切分和 IDF/DXF 对同一转折的不同粒度展开，会让重复 elbow 在几何方向上
唯一却在实际 pipe 连通性上错误。CWR200001 p2 / CWS200001 p1 是回归负例；每个提升的 frame
仍须至少有一个已锚定 incident pipe、唯一 raw continuation，或可审计的 source-vector 接触。

`EXACT_RAW_PIPE_CONTINUATION_V1` 是在既有独立 frame/pipe anchor 之后才允许的窄传播规则。
运行 `propagate_exact_raw_pipe_continuations_v1.py <idf-topology.json> <dxf-pipe-topology.json>
<propagation.json> --page N --output <json>`。前提是：已匹配 `I### → P###` 的相邻下一条
IDF `100` 与它在**原始三维端点**精确相接，且该 DXF `P###` 在**原始 source-vector 端点**处
只有一条未匹配 DXF pipe 相接。此时可把下一对输出为 `medium_continuation`。

该规则只传播编号，**不会合并**两侧 DXF pipe，也不会忽略支架：若接点恰为支架，仍产出两个
独立 `I### → P###` 映射。若出现两个候选、编号不连续、IDF 端点不精确重合，或需要跨越焊缝、
弯头、法兰、变径、阀门、三通、空端，必须停止，不得以图纸顺序或长度补全。当前正向回归为
`CWR200001` p1 从 `I005→P004` 传播 `I006→P005、I007→P006、I008→P007`，以及
`CWS200001` p3 从 `I015→P003` 反向传播 `I014→P002、I013→P001`；两者都必须保留原图
vector overlay 审计。

`CROSSPAGE_VECTOR_PORT_TURN_V1` 是跨页时比“CONT 文字位置”更窄的一条补充规则。运行
`seed_crosspage_vector_port_turn_v1.py` 前，前一页的 pipe 必须已经由**非续页证据**独立匹配；
IDF 上它必须经唯一 `turn_2` 连向本页唯一未匹配 `100`。此时可读取 `CONT. ON/FROM` 仅用于找到
其附着的 source-vector 注释引线，并按以下条件播种当前 pipe：

1. 从文字端口追踪短的零宽 `LINE/LWPOLYLINE/POLYLINE` 引线；**排除 0.6 宽管道骨架**，否则
   一个箭头触到管线会沿真实管线泄漏到相邻 pipe；
2. 前页已匹配 pipe 必须与其引线包精确端点接触（≤0.15 DXF 单位）；
3. 当前页在尚未占用的 pipe 中，唯一最近 source-vector 端点须距离引线包 ≤1.0，且与第二近
   候选的距离差 ≥2.0；这是容忍 CAD 箭头尖端短 0.653 单位的导出误差，而不是文字邻近；
4. 满足后才写入 `medium_crosspage_vector_port`，并仅可把该 pipe 另一端唯一、未占用的
   `elbow ↔ turn_2` 作为下一次二度传播 frame。后续 pipe 仍须分别经过 exact raw、二度构件或
   其他独立证据，绝不跨支架或把页内路径自动补齐。

反事实/回归：CWR200001 `p1 I010→P009` 的零宽引线精确接触前页端点；其 `p2 CONT.FROM`
引线到 P002 的距离为 0.6527、到第二近 P003/P004 为 4.36，因此只允许
`I011→P002`，再由唯一弯头得到 `I012→P003`。若把 0.6 骨架纳入追踪会错误触及 P008；若以
文字距离取候选会把 P000/P001/P003/P004 混入。CWS200001 `p3→p1` 回归必须是 0 新增。
`CONT` 仍不是页面归属、编号顺序或全局路径的主证据。

`REVERSE_CROSSPAGE_TURN_V1` 是上述规则的反向形式：当前页的 `CONT.FROM` 零宽引线先唯一
命中一个**已经匹配**的 pipe；IDF 中仅保留该 pipe 经 `turn_2` 所连、且不属于当前页或前页既有
映射的另一条 pipe。前页 `CONT.ON` 引线可有多个精确接触候选，但其中必须只有一条属于所需
`elbow`，才可写入前页 pipe；**不可**跨页写入 `turn_2↔elbow` frame 对，也不可据此进行
二度传播：前页可见 elbow 的另一臂可能属于本页另一条 IDF pipe，而 IDF turn 本身在页界未画出。
CWS200001 回归为
`I012→p3:P000` 加前页 P010（而 P001 同引线触及但属于 branch，必须拒绝），得到
`I011→p1:P010`；不得误写 `K006→p1:C000`。若当前 IDF pipe 的另一条 turn 邻居已在当前页映射，必须
排除，不能误判为前页 arm。

`CALIBRATED_BRANCH_ARM_DIRECTION_V1` 仅解决一个**已配对三通**中剩余的两条主管臂。运行
`resolve_branch_arms_by_calibrated_direction_v1.py` 前必须同时满足：项目级 D4 轴向已经由其他
页面的非方向证据独立校准；三通的三条 IDF/DXF 臂完整；其中一条已由 outlet、vector-contact
等非方向证据锚定；剩余两条构成唯一的一对一排列。把 frame centre 到每条 pipe 最远端的向量
应用已校准变换后，两个余弦都必须 `>=0.80`，且该排列总分比交换排列高 `>=0.50`，才输出
`medium_calibrated_branch_direction`。不满足任一条件时返回零新增；不可用单臂方向、页序、长度
或候选页自己的坐标去校准该变换。

CWR200001 p2 的回归为：`K009↔C013` 先以 `I014→P004` 的 raw-41 outlet 锚定，独立项目轴向
为 `flip_y`；于是仅可得到 `I013→P001 (cos=.83727)` 与 `I015→P000 (cos=.99626)`，全排列分差
为 3.65829。CWS200001 p1 与 DR200001 p1 在相同脚本下均为 0 新增。这个规则只定位三通臂，
不允许由此穿过支架/箭头去补齐后续长链。

`FLOW_WEDGE_TIP_V1` 将已由 DXF 图元识别层确认的 `arrow_pipe` 变为**有向边证据**，而非新构件。
运行 `extract_flow_arrow_vectors_v1.py <page.dxf> <semantic.json> --dxf-topology <line.json>`：只取
已验证 26/27 顶点箭头多段线的**源顺序前三个外轮廓点**，三角形最小内角顶点为尖端，另两点中点为
尾部；其向量和同一 `arrow_pipe` 的端点向量点积确定 `flow_endpoint_order`。严禁遍历 26/27 个填充
顶点找最小角，也不得从箭头旁文字、引线或 PNG 推断方向。CWR200001 p2 回归：`1A786→P000`
为 `(223.0,340.2)→(223.0,365.0)`，`1A8D6→P001` 为 `(223.0,336.3)→(187.2,274.2)`；全量 83 页
扫描得到 19 页/55 个箭头，尖端角均为 10.2°–22.7°。

流向只能用于已经存在的 IDF↔DXF 候选的**定向消歧**：先以已独立锚定的 frame/pipe 或项目级轴向
校准确定该局部 IDF 坐标边，再检查其从连接器向外的方向是否和 `flow_endpoint_order` 一致。它不能
建立页面归属、不能穿过支架/焊缝/构件、不能替代一对一拓扑证据，也不能假定 IDF `100` 记录的 `a→b`
书写顺序就是工艺流向。图框左上 `N` 的指北矢量可作为页内方位基准复核同一方向变换；在其箭头尖端
未由 source vector 独立验证前，不得用 `N` 文字位置替代北向或写入匹配。

`IDF149_FLOW_MARKER_PAGE_RANGE_AUDIT_V1` 只在多页 global-cover 的多个**完整、无重号**方案之间
做页面范围复核：`149 ... FLOW` 只绑定到其后第一条 `100`，然后比较每个 IDF 范围内的标记数与
对应 DXF 页 `arrow_pipe` 数，运行 `score_idf_flow_marker_page_ranges_v1.py <idf> <dxf-topology> <cover>`。
它必须忽略任何漏 `I###` 或重复 `I###` 的 cover，即使其计数更好；只有最小总差严格唯一才输出
`unique_flow_marker_range_candidate`，仍不能直接编号。DR201010、DR201014、SCR2200001、DR201015
的回归都为 `flow_marker_non_discriminating`：箭头计数可复核局部范围，但不足以取代构件/邻接证据。

`NORTH_REFERENCE_SOURCE_VECTOR_V1` 是对指北符号的独立审计：运行
`extract_north_reference_v1.py <page.dxf>`，从精确文字 `N` 附近唯一且明显更近的 6–12 顶点闭合
源多段线取候选；图框边界即使闭合也必须因距离劣势被排除。当前项目 15 张有图框 `N` 的页面均取得
同一候选形态：CWR p2 为 `1A4D0`，候选尖端 `(389.4,400.0)`、向量 `(-5.829,3.314)`。该脚本输出
`candidate_requires_visual_confirmation`；只有人工确认其确为北向尖端，才可用作 `FLOW_WEDGE_TIP_V1`
和项目轴向校准的页方位复核，不能单独产生 `I###→P###`。

确认指北源矢量后，运行 `classify_flow_arrow_bearings_v1.py <flow-arrow-audit.json> <north-audit.json>`；
它输出每个已确认箭头相对北向的顺时针角和八方位。若只允许使用已看过的候选符号，可显式传
`--allow-north-candidate`，输出必须保留 `bearing_observations_candidate_north`，仅可用于人工复核，
不得喂回自动编号。CWR200001 p2 的候选北向下，`P000` 为 NE（54.193°）、`P001` 为 W（277.425°）；
两个箭头均先由三角形最小内角尖端和 exact-source-handle pipe join 得出。任何“箭头角度”都先以
DXF 的 source 三点 wedge 计算，不能从屏幕像素、箭头旁注释或 IDF 记录顺序猜测。

`149 ... FLOW` 含有坐标，且当前样本中会落在随后 `100` 的几何线上；但这**不足以**默认把该
`100 a→b` 当作流向。先运行 `audit_idf149_dxf_arrow_direction_v1.py <idf> <flow-audit> <dxf-topology>
<verified-matches> --axis-transform <calibrated-D4>`，它只检查已经由非流向证据确认的 I→P。
回归：CWR p2 的 `I015→P000` 余弦 `.994183`；但 DR200001 p1 的 4 个已确认箭头对仅 1 个
`≥.9`（其余 `.136`、`-.132`、`-.132`）。因此当前状态是 `149` 可形成**待验证的局部有向观察**，
而不是可从 IDF 记录顺序自动匹配的规则。只有至少 3 个独立已确认对全部 `cos≥.9`，才可将其作为
同项目的后续候选消歧；即使如此也只能排序已有拓扑候选，不能建立 I→P 或页归属。

在任何 `CALIBRATED_BRANCH_ARM_DIRECTION_V1` 之前运行
`audit_branch_arm_geometry_v1.py <frame-graph> <dxf-topology> <matches> --page N`。它比较一个已锚定
三通余下两条 IDF 臂与两条 DXF 臂的**无向夹角关系**；若 IDF 两臂近共线（`|cos|≥.98`）但 DXF
两臂不平行（`|cos|<.90`），输出 `branch_geometry_nonisomorphic_reject_direction`，禁止使用任何
流向、指北、页坐标或长度为这对臂排序。CWS200001 p1 回归：`I007/I009 |cos|=.99983`，而
`P001/P002 |cos|=.71511`；它解释了为什么两条候选不能由轴向消歧，而不是箭头识别遗漏。

`ORIENT_UNIQUE_CORRIDOR_BY_BOUNDARY_V1` 只接受已由 `UNIQUE_CORRIDOR_SIGNATURE_V1` 找到的唯一
IDF/DXF 路径对（仅差正反方向）。检查每个方向的路径端点外一跳：IDF 与 DXF 必须在同一端都
到达同一种已定义构件（当前仅 `junction/branch/tee`）；只有一个方向至少命中一个边界语义且
严格优于反向时，才能给整条走廊编号。CWS200001 p1 回归：唯一 `elbow,direct,direct,direct`
走廊 `I002…I006` 与 `P003…P007`，末端均外接三通，故按正向映射。没有唯一路径、两向同分、
或边界仅为 weld/support/文字时必须保持未定向。

`DEGREE2_FRAME_PROPAGATION_FROM_PIPE_MATCHES_V1` 紧跟在上述规则之后：运行
`propagate_degree2_frames_from_pipe_matches_v1.py <frame-graph.json> <current-matches.json>
--page N --output <json>`。已匹配 pipe 的两侧各只能有**唯一一个**尚未配对、且同类别的
semantic frame；如果该 frame 在 IDF 和 DXF 都为 degree 2，且已经有一条相同的 incident pipe
映射，才可匹配另一条 opposite pipe。它对 elbow/reducer 有效，绝不对三通按臂顺序猜测。
筛选“唯一未配对 frame”时只计入可语义匹配的 `elbow`/`reducer`/`junction`；DXF weld、普通
绘图拆分和 IDF 未命名 connector 仍是边界证据，但不得把一个本已唯一的二度构件误报为多候选。
DR200001 p2 的 `I024→P001` 同时邻接 weld `C006` 与 elbow `C004` 是此 guard 的正向回归。

推荐闭环顺序是：独立 frame/outlet anchor → exact raw continuation → degree-2 opposite arm →
再次 exact raw continuation，直至没有新增项。每一步仍要执行 one-to-one 检查、源端点检查和
整图 overlay 审计。正向回归：CWR200001 p1 在 `I008→P007` 后唯一得到
`K006(elbow)→C000(elbow)`、`I009→P008`，再由精确端点得到 `I010→P009`；CWS200001 p3
在 `I013…I019` 后唯一得到 `K007(elbow)→C001(elbow)`、`I012→P000`。

`BRANCH_ARM_OTHER_FRAME_SIGNATURE_V1` 处理已独立配对的三通剩余臂：运行
`propagate_branch_arms_by_other_frame_signature_v1.py <frame-graph.json> <matches.json> --page N
--output <json>`。三通的臂序和二维方位均不可作为事实；对每条臂只读取离开该三通后的第一个
**已命名** frame 类别（`elbow`/`reducer`/`junction`，否则为 `open`）。只有所有三条臂的
injective 完整排列唯一、且与既有独立 arm mapping 相容时才能写入。DR200001 p2 的
`K012↔C018` 以 `I023→P007` 为独立 outlet anchor，唯一得到 `I022→P000(open)` 与
`I024→P001(elbow)`；不使用臂的图上排序、长度或 CONT。

`UNIQUE_CORRIDOR_SIGNATURE_V1` 是尚未锚定的跨页走廊的最后保守规则：运行
`propagate_unique_corridor_signature_v1.py <idf-topology.json> <dxf-pipe-topology.json>
<matches.json> --page N --length K --output <json>`。仅在剩余 IDF 与该 DXF 页各存在唯一长度 `K`
的简单路径，并且按序的转移标签完全相同才提出候选：IDF 的精确 pipe-to-pipe 连接与 DXF 的
同一支架切口都归为 `direct`，但每个 `35+36` 弯头必须与 DXF `elbow` 一一对齐。它绝不把
正反方向的两个候选自动写入。

若已有**独立匹配**的上一页 pipe，可额外传入 `--prior-dxf --prior-pipe --prior-page --current-dxf`。
这只在走廊拓扑已经唯一之后验证：上一 pipe 靠近 `CONT. ON DRG N` 端口、当前候选仅一端靠近
`CONT. FROM DRG previous` 端口，且反向端明显更远。CONT 在这里仅是方向的 corroboration，不能
产生走廊候选。DR200001 p2 的唯一签名 `elbow,direct,elbow,elbow` 因已匹配的 p1
`I016→P015` 与页间端口复核，定向为 `I017→P002…I021→P006`；生成整页 overlay 后才可报告。

`UNIQUE_REMAINING_COMPONENT_ARM_V1` 是最后一条无排序的闭合规则。运行
`propagate_unique_remaining_frame_arm_v1.py <frame-graph.json> <current-matches.json> --page N
--output <json>`：只有 frame pair 已经被独立锚定，且其所有 incident pipe 中恰好差一条、
其余臂已 injective 地一一匹配时，才匹配剩余一臂。它适用于三通或二度构件，但不能从零条或
一条已匹配臂推测三通顺序。DR200001 p1 中 `K006↔C023` 已有
`I014→P014、I015→P006`，所以唯一闭合为 `I013→P005`。

对传播结果调用 `render_idf_dxf_match_overlay.py <source.dxf> <propagation.json>
--dxf-pipe-topology <topology.json> --output <png>`。一个 semantic pipe 可包含多个 source
vector（特别是 arrow-transparent pipe）；图中要高亮全部向量，但同一个 `I###` 只标一次，
避免把一个 IDF `100` 误读为多个编号。

同一 renderer 也可读取 `RAW41_BRANCH_CONTINUATION_HYPOTHESIS_V1` 的 `hypotheses`，使用
`--crop-to-selected` 生成原始 DXF 局部源矢量复核图。它仍只高亮假设中已经给出的 source
handle，不会从 PNG 识别、移动或补画图元；标题和审计 JSON 必须保留 `low` 状态。

对需要比较两侧连续关系的低置信假设，运行
`render_paired_hypothesis_review.py <idf-topology.json> <frame-graph.json> <source.dxf>
<dxf-pipe-topology.json> <hypotheses.json> --output <png>`。该图左栏只显示候选 `I###`
及其 IDF connector 邻域的标准轴测投影，右栏只显示同一候选 `P###` 的原始 DXF 局部矢量和
两端 DXF frame；黄色是候选直管、粉色是 frame。裁切范围必须由候选 pipe 与这些 frame 的
位置共同确定，不能按整页坐标或仅按短 pipe 长度留白。此图是人工复核证据，绝不升级
`low` 候选为最终匹配。

全量验证用 `batch_component_frame_matching_v1.py`：它从页面清单取唯一 IDF 文件，始终从
原始 IDF 重建 `raw_geometry_graph` 后再计算框架，并把无 IDF 或多 IDF 候选的管线显式跳过。
批量结果中的 `topology_global_unique_exact_cover_candidate` 才有资格进入逐页锚点传播。
完整覆盖但存在同分/近分页范围方案时必须标为 `ambiguous_exact`；构件分数过弱时标为
`weak_exact`。两者与 `partial_cover` 一样都只能作为跨页重叠、漏图或构件识别不足的诊断，
不得直接编号。

即使某一 DXF 页没有可确认的弯头/三通/变径，也必须作为 zero-signature 页留在全局覆盖中；
它只能得到弱候选，绝不能因缺少构件而被静默删除并把剩余页面误判为闭合。

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

当单页 `CHAIN_100_V1` 同时满足 `confidence=high`、覆盖全部 IDF `100`、且每条审计 handle 集合在
该页 `global-dxf-pipe-topology` 中恰好命中一个 `P###` 时，运行
`promote_chain_match_audit_v1.py <match-audit> <dxf-topology> --line-key <KEY> --page N` 写入统一
`I###→P###` 结果。它只作 stable-ID 转换，不重算、补齐或提升原算法：任一缺失、重复或多命中即拒绝。
回归样本：DR200008 `3/3`、MP2201003 `13/13`，两者均以完整高置信链和精确 source-handle 集合
提升，并生成原始 DXF 全页编号叠加图。

当 `SUPPORT_CONTRACTION_CHAIN_V1` 完整且 `high` 时，运行
`promote_support_contraction_audit_v1.py <support-audit> <dxf-topology> --line-key <KEY> --page N`。输出
`dxf_pipes: [P###...]` 表示**一个 IDF `100` 对应的支架切分段组**；必须保留每个 `P###` 和支架切点，
不能把它们重写成一根 DXF 线。要求每一个 audit handle 恰好归属一个同页 `P###`，各组不得复用
`P###`，否则拒绝。renderer 可高亮 `dxf_pipes` 的全部 source vector，但 `I###` 只标一次。
回归样本：VT200001 `4/4`（8 个 DXF fragments）、VT200002 `5/5`（9 个 fragments）。

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

使用 `render_idf_dxf_match_overlay.py <source.dxf> <match.json> --output <png>` 生成 DXF
源矢量复核图。它只高亮匹配 JSON 中已有的 handle，并将 `I###` 放在该源向量中点；
它不会从 PNG 推断或移动任何图元。`medium` 候选与 `high` 已匹配项必须在标题或审计 JSON
中保留其置信度，不能在图上伪装成已确认事实。

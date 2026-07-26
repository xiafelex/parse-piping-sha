# PCF + SHA 研究过程与协作路线图

本文记录本课题从“SHA 是什么附件”到“可运行的 PCF + SHA 本地 App”的推理
过程。它不是格式规范；每条结论均应由归档样本、脚本、Trace JSON 或 SHA
内部记录复核。

## 研究边界

目标是复原和演进管道 ISO 出图能力，而不是把 PDF 当成数据源。工程信息与二维
出图信息必须分开：PCF 提供构件、连接、UCI 与 E/N/EL 等工程坐标；SHA 提供
Sheet、PSM、StyleCluster、文本、图元和图纸二维布局。PDF 只用来发现视觉差异。

## 演进时间线

| 阶段 | 主要问题 | 关键结论与沉淀 |
|---|---|---|
| 1. 文件识别 | `.sha` 是否只是校验文件？ | 它是 Shape2D/PDMS 的 OLE 绘图容器，包含 Sheet、PSM、动态属性、样式和模板资源。 |
| 2. 身份与编码 | `{000138AA-...}` 是否一物一码？ | 它是 UCI，最适合做模型实例关联；同一物料在不同位置通常有不同实例 UCI，但 UCI 不是物料码、采购码或绝对资产码。 |
| 3. PCF/SHA 联系 | 只有 PCF 还原什么，SHA 又增加什么？ | PCF 能重建工程拓扑；SHA 才给出页面、文字、方框、引线、尺寸、模板和二维布局。身份链为 `UCI -> graphic_ref -> Sheet -> PSM -> primitive`。 |
| 4. 分图点 | 一条管线被拆成几张 ISO，分界在哪里？ | SHA 能给出纸面 `SEE ISO/SHT` 标记；PCF 与 SHA 联合后，才能把跨页共享端点映射回工程坐标。不要把外部 `CONNECT TO` 误判为同线拆图。 |
| 5. SVG 复原 | 仅从 SHA 能否生成接近原图的矢量图？ | 可以分层解出线、复合线、弧、椭圆、文本、模板与 PSM 包围盒；每个 SVG 元素必须保留来源和置信度。 |
| 6. 视觉偏差 | 为什么标题栏、中文、尺寸、仪表和标注会偏移？ | 先修 Sheet 可见视口；文本常需 Sheet anchor 定位、PSM envelope 定尺寸；不同 StyleCluster/文本家族不能用同一套补偿。 |
| 7. 复杂图元 | 为什么法兰、变径、焊点、双线和箭头会丢失？ | 不能只读取简单线段；还要解析复合 type-5/type-6 图元、椭圆锚点和 SHA 派生的兼容图层。 |
| 8. PSM 深挖 | PSM 是否已完全解码？ | 未完全。已验证若干 node framing、bbox run 和 Sheet namespace 关系；relation code、完整父子语义仍保持 `unresolved`。 |
| 9. 工程产品化 | 如何让同事直接使用？ | 建立本地项目工作台：不可变导入、哈希、PCF/SHA 配对、五页 SVG、Trace、分图候选和对象检查器。 |
| 10. macOS App | 如何在没有 Python 环境时运行？ | PyInstaller 内嵌解析引擎 + Electron 壳；启动等待和本地 `engine-startup.log` 解决首次解包启动慢的问题。 |

## 已解决的关键问题

### 1. 多页 SHA 不能只读 `Sheet6`

逻辑 ISO 页必须从所有非空 `Sheet*` 流中读取标题块 `SHEET n OF total`。当前
AMSS2 样本有 5 页，过程件在 `research/artifacts/.../five-pages/`。

### 2. 页面缩放与标题栏裁切

Shape2D 把 ISO 页放在方形工作区；实际可见区域的 y 起点不一定为零。若直接用
`16800 x 11880` 视口，会整体压缩 x 或裁掉标题栏。必须以 Sheet header 的
完整 `(x, y, width, height)` 作为 SVG viewBox 来源。

### 3. 文字、方框与引线不是 PCF 属性

PCF 通常可告诉我们构件类型、端点、UCI 和属性；注释字体、字号、方框、引线、
尺寸线、标题栏与材料表位置来自 SHA 的 Sheet、PSM、StyleCluster、模板流与
TaggedTxtData。PDF 只能指出“偏了”，不能给出修复坐标。

### 4. 纸面分图点与工程坐标必须区分

SHA-only 可识别“第几页到第几页”的纸面标记位置；PCF-only 有工程坐标却没有
页边界。只有 UCI/graphic/Sheet 与 PCF 端点联合时，才可给出同线分图候选的
E/N/EL。候选点仍需显示证据链和置信度。

### 5. PSM 包围盒不总是图元锚点

仪表圆、焊点等应优先使用原始 Shape2D ellipse anchor；PSM 只用于显示范围。
一般文本的 Sheet anchor 常是基线而 PSM 给出字形尺寸。错误地一律取 PSM 中心
会导致圆、尺寸、标题栏和文字整体偏移。

## 已知未解项与禁止推断

- `PSMspacemap` relation code 的完整语义尚未证实，不能命名为具体构件类型。
- `JSite` 缺少 `CONTENTS` 时只可列为未解析的外部依赖，不能臆造图框。
- UCI 在多 Sheet 或多 graphic context 下可能复用；实验性写回的唯一目标必须是
  `(page, graphic_ref, uci)`，不能只凭 UCI。
- 现有 SVG 证明的是 SHA 解析器级别的复原，不等于原厂 Shape2D/PDMS 一定接受
  修改后的二进制 SHA。

## 思路修正记录

这部分保留讨论中的关键转折，避免后续协作者重新走回已排除的路径。

| 初始理解 | 后续证据 | 采用后的做法 |
|---|---|---|
| SHA 可能只是 PCF 的附属索引或文字层。 | Sheet 中可解出线、弧、椭圆、文本、模板边框；PSM 和 dynamic attribute 还能连接 UCI。 | SHA 被视为二维出图容器，而不是“补充注释文件”。 |
| UCI 可以直接等同于每件物料的唯一二维码。 | 同一物理/模型对象可能存在多 graphic 或跨页上下文；物料码、item code 与 UCI 职责不同。 | 用 UCI 做实例关联；物料、采购、资产语义另建字段。 |
| `SEE ISO`/`SHT` 就是所有分图点。 | 有些 `CONNECT TO` 是设备或另一条管线；SHA 只有纸面位置。 | 先识别同线跨页接口，再用 PCF 共享端点验证工程坐标。 |
| PSM bbox 中心就是构件/仪表的图上中心。 | 仪表圆和焊点的原始 ellipse anchor 与 bbox 中心存在偏差。 | 圆和点优先用 primitive anchor，PSM 只管尺寸范围。 |
| 可以根据 PDF 把 SVG 文本拖到正确位置。 | PDF 只显示结果，无法证明对应 SHA 字段；这种做法不可复现。 | PDF 只用于发现偏差，修复必须回溯 Sheet/PSM/StyleCluster/TaggedTxtData。 |
| 先做完整 PDMS 同等出图引擎。 | 多个 PSM 语义仍未解码，但导入、Trace、多页浏览和分图候选已有直接证据路径。 | 先交付分析型 App，再逐步增加分图编辑、焊缝和模板规则。 |

这个修正过程也是后续审阅代码和规则的原则：若一个视觉改进无法指出 SHA 的
stream、对象引用、锚点、bbox 或 PCF 端点来源，它只能列为待解项，不能作为
稳定算法进入出图引擎。

## 面向下一位协作者的工作顺序

1. 先运行 `shasum -a 256 -c research/RESEARCH_MANIFEST.sha256`。
2. 阅读 `research/README.md` 与 `SHA_ISO_AI_HANDOFF.md`，理解 PDF 和置信度边界。
3. 用 AMSS2 样本运行全页 SVG、Trace 和 PSM 诊断，再改解析器。
4. 每一项新规则都至少保留：原始 SHA stream、graphic_ref/UCI、锚点或 bbox、
   样本截图/输出和 `direct/derived/candidate/unresolved` 状态。
5. 新增分图、焊缝或模板能力时，把用户覆盖层或派生 PCF/SHA 放入新的实验目录；
   不覆盖 `research/samples/` 原始件。

## 当前产品路线

已完成：导入与哈希、PCF/SHA 推荐配对、多页 SHA SVG、Trace、基础分图候选、
macOS App 封装。

下一步：分图点编辑及局部重算、直管插入焊缝及焊缝标记、项目模板规则、元件
规则库、PCF + SHA 联合布局和受控的 SHA 写回实验。

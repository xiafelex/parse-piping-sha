# 阿联酋项目独立解析分支

本 profile 是阿联酋 TXT、POD、SHA 代码唯一维护位置，结构与蓝星、成达 profile 一致。
这里的“分支”是项目 profile，不是切换整个工作区的 Git 分支。

```text
profiles/uae/
  parser/
    parse_uae_isogen_neutral.py  固定列TXT解析、PCF核验、Excel导出
    pod_binary.py               POD对象边界、长度字符串、无损树
    pod_decode.py               POD属性、构件、端口、图元
    pod_render.py               仅使用POD的矢量预览
    sha_native.py               阿联酋SHA原生记录与元数据
    cross_format.py             PCF/POD/SHA交叉核验
    analyze_iso_split.py        独立复制的SHA基础解析
    analyze_sha_pages.py        独立复制的SHA基础解析
    sha_to_svg_prototype.py      旧SHA渲染基线的阿联酋副本
    run_sha_iso_render.py       旧渲染基线入口，仅研究使用
    tests/                     单元测试及本地四文件集成验证
  workflow/
    decode_pod.py               POD批次解码、无损回写验证、证据导出
    inspect_sha.py              SHA原生诊断预览
  docs/
    POD_FORMAT.md              已验证结构和未解决项
    SOFTWARE_PROVENANCE.md     软件来源、官网与社区资料
    sha_baseline_manifest.json  独立SHA代码的复制来源与初始哈希
tools/uae/probe_pod.py           只读对象/十六进制检查工具
run_uae.py                      独立统一入口
```

## 隔离原则

- 不导入 `profiles/lanxing` 或 `profiles/chengda_indonesia`，不改变它们的默认入口和规则。
- 4个SHA基础模块从蓝星当前版本独立复制，以后只在uae内修改。
- 工作区根目录旧 `parse_uae_isogen_neutral.py` 与测试文件是兼容转发，避免重复维护。
- 输入、Excel、SVG及二进制解码结果留在仓库同级 `阿联酋项目`，不放入代码目录。
- 不假定其他项目也使用同样的TXT布局或POD序列化版本。

## 运行

从 `PCF解析` 工作区执行，路径可自行替换：

```powershell
$src = '阿联酋项目\30201_50150_LT_HE-H4-000-13-61-023_NATIVE\3. NativeFiles'
python parse-piping-sha\run_uae.py txt $src --out '阿联酋项目\TXT解析结果' --xlsx
python parse-piping-sha\run_uae.py pod $src --out '阿联酋项目\POD解析结果'
python parse-piping-sha\run_uae.py sha $src --out '阿联酋项目\SHA解析结果'
python parse-piping-sha\run_uae.py pdf --input-dir $src --output-dir '阿联酋项目\SHA转PDF核对' --workers 4
python -m unittest discover -s parse-piping-sha\profiles\uae\parser\tests -v
```

每条子命令也接受单个文件。TXT目录递归需要`--recursive`；POD/SHA批次默认仅当前目录。
运行依赖见`requirements.txt`。不需要启动或安装Smart 3D来解码当前四个样本。

## 本次验证结果（2026-10-02）

| 管线末段 | POD节点 | 构件对象 | 端口 | POD直线与SHA精确匹配 |
|---|---:|---:|---:|---:|
| 0212-HCX-PAA | 3581 | 49 | 100 | 416/416 |
| 0937-HCX-PNC | 1581 | 17 | 33 | 152/152 |
| 0939-HCX-PNC | 1856 | 21 | 39 | 159/159 |
| 0511-FGM-PNC | 2535 | 30 | 60 | 247/247 |

4个POD的对象树重新编码后，逻辑流完全相同；使用原OLE容器布局回填后，整个文件SHA256相同。
未知字段保留原始字节，所以这证明无损保留，不证明完整语义解码或具备安全编辑能力。
POD的117个构件对象中，0511存在两段相同UCI管子，不得按UCI直接丢弃几何。
PCF的全部UCI在POD中均有对象；308个可对照的构件属性值全部一致。
TXT迁移后仍为70行精确匹配。15项测试通过（含本地四文件集成用例）。
另两个项目的118个既有文件已逐一核对哈希，均未改变。

## 输出与准确性边界

- `pod-object-tree.lossless.json`：完整嵌套边界，未知直接数据段以base64保留。
- `pod-decoded.json`：已解码字段、偏移、图元、连接记录、明确的未解项。
- `构件清单.csv`、`端口坐标.csv`、`构件属性.csv`：便于表格检查的构件和属性明细。
- `cross-format-audit.json`：同UCI工程坐标核对、属性核对、POD/SHA直线对应。
- `sha-evidence.json`：AppObject安装路径、版本历史、图号信息、SHA线段记录。
- `roundtrip.pod`：原容器布局下重建的验证副本，不覆盖源文件。
- `pod-preview.svg`：POD独立预览；不是正式交付图，未借用PDF/SHA几何。
- `Sheet*-native-preview.svg`：SHA自身的线和文字诊断预览；模板和主图保持各自Sheet，不猜测合成变换。

4条圆弧的起角、带符号扫角已与SHA独立核验一致，本批2058个图元均已进入POD预览；文字对齐、字体宽度和部分线型尚未完成精确恢复。
SHA旧基线假定的单位、图框、PSM大小与本项目不完全一致，初次运行确认存在布局偏差；因此正式`sha`入口使用坐标一致的原生诊断预览，旧基线仅供后续逐项适配。
目前`complete_semantic_decode`、`complete_visual_reconstruction`均为`false`，不能将本轮称为完整POD还原。

## SHA并发输出PDF（核对版）

`workflow/convert_uae_sha_to_pdf.py`使用ThreadPoolExecutor按文件并发，每个任务独立调用浏览器打印。
`parser/render_svg_pdf.mjs`独立复制自成达印尼项目，沿用SVG实际纸张尺寸、无边距、浏览器PDF输出方式，不运行时导入其他profile。
依赖Python的pypdf、PyMuPDF（字体度量）、仓库共享的Playwright，以及Windows上的Microsoft Edge。

当前支持本批A3图框的单页及多页结构：解析各绘图区图签中的SHEET/OF，按逻辑页码排序，每页独立叠加共用Sheet39817图框。Sheet编号大小不代表出图顺序。页码缺失、重复、总页数不符或图框缺失明确报错，避免漏页；含独立备注Sheet或多套页序列的复杂容器仍需专门处理。
默认递归查找主SHA，排除-template、-usergraphics和-titleblock附件；不读取原PDF、PCF或POD来补图。
转换目录仅保留`_SHA.pdf`；SVG只在临时目录内使用并自动清理，批次状态输出到控制台。已有PDF需显式`--force`覆盖。
发布与成达当前逻辑一致：单页保留浏览器PDF原始字节，多页在同一浏览器中分别打印后用pypdf合并；校验页数及所有页纸张大小，原子替换后执行`icacls /inheritance:e`，恢复输出目录的继承权限，避免沙箱临时文件的受保护ACL导致用户无法打开。
四份输出已通过PDF读取、页数、纸张尺寸和文本提取检查；源SHA哈希前后核对一致。

这是现有SHA原生解析器的核对版：已补入圆弧、圆、主图填充轮廓、动态修订栏、图框多行文字、ADNOC图文、Wood嵌入矢量Logo及线宽颜色。图框0x84折线、WBS框线和材料表标题文字边框亦已恢复。签字资源尚未确定，仍有未解码的图元类型及文字装饰；字体替代、段落高度及标题栏对齐仍有差异，不能作为完整还原版交付图。

同版证据、第一轮修正及后续差异见[SHA与PDF版本核验](docs/SHA_PDF_VERSION_AUDIT.md)。四组图号、修订信息和提取时间均一致，可用原PDF校验解析。

2026-10-29增加复杂图多页验证：0331-HCX-PAD（5页）、0125-N2-PNB（7页）、0186-VE-PAA（2页）、0445-HCX-PAC（8页）、0208-STL-PNB（7页），共29页。五份均通过严格PDF读取、页数、A3尺寸和逐页图号检查。
新增`parser/sha_document.py`集中负责页序，`render_svg_pdf.mjs --manifest`在单浏览器中逐页打印，`--report-file`可将页序和图元诊断写到PDF输出目录之外。
嵌入资源0xCE末尾的两个uint32分别指定对象组与JSite资源，119/127字节实例共用+43矩阵。现在按owner树筛选所引用符号，避免把同一JSite里的Wood标志、修订三角及其他符号全部重复绘制；同时恢复所引用组内的原生文字。直线和文本解析按完整cluster记录边界读取。
`tools/uae/audit_multipage_pdf.py`输出逐页分区墨迹核验、逐次文字匹配和五张原图/转换图对照。200dpi、0.254mm方形邻域容差下，29页管道区参考墨迹匹配比例最低0.9974、材料表最低0.9922；白底不计分，不衡量颜色、工程语义或严格形状一致性，不能称为整体还原率。
本轮纯SHA输出没有补入原PDF签字。0331-HCX-PAD第2页原PDF的FC尚无对应SHA文本证据；标题字体仍存在替换及驱动量化差异。结果属于已核对的高接近度转换，尚未达到全图完全一致。
809份整编SHA的结构预检中774份符合当前完整页序规则（2273页），35份被明确拒绝，其中包括仅备注片段、多套页序列和独立备注Sheet。预检通过不等于这774份都完成图面还原验收；本轮逐页对照范围为上述5份29页。

## 真实焊口及管理编号标注

`workflow/annotate_received_tables.py --input-dir <选图资料目录> --workers 2`读取选图`分析结果.json`和四张CSV，默认将原图转换PDF、编号标注PDF、五张选定图页合册和两张sheet的编号清单都放在选图资料目录下。可用`--output-dir`指定其他目录。当前按新版焊口表的正式管段号标注，不产生虚拟管段号；跨页管段各页显示同一编号。每次计算保留输入CSV快照、SHA256，并在输出前核对源表是否在计算期间改变。

`parser/uae_annotation_mapping.py`保留焊口CSV的重复字段名及额外单元列，按工程坐标匹配PCF焊点、UCI及SHA焊点。管内新增分段口在同UCI的直管图元上插值，并由真实端部焊点确定方向；管理编号优先用UCI，支架同SP编号多材料行合并。缺少图元UCI的支架可通过工程坐标投影至主管线或重合构件中心定位，定位方法列入清单。无法唯一定位则报错，不按CSV的-1页码猜页。

管段以“图号＋表内管段号”分组，汇集其预制焊口及两侧元件编号。优先使用组成直管在焊口表中实际两端焊点的中点定位；未能核实直管中点时使用该管段的预制焊口图元，不猜测新的几何位置。管段采用成达红色双矩形框，1.5倍字高为2.4mm；保持透明无衬底、单段直线引线。同管段跨页时分页面标注，清单同时列唯一管段数量及图面标注处数。

`parser/dense_layout_adapter.py`仅在阿联酋隔离进程中为密集页调整成达精确求解器的候选访问顺序，优先保留相邻目标的剩余候选；保持原兼容性规则和最终零新增标注冲突要求，不改写其他项目源文件。适配入口检查共享求解器结构，结构改变时明确报错。

`parser/solve_real_callouts.py`独立进程调用成达候选位置和全页求解函数，明确换算线段的归一化sheet单位与标注毫米单位。当前按用户要求全部采用单段直线引线，焊口红色圆圈，支架/阀门/法兰管理统一蓝色直角矩形。字体为成达配置的SimHei/Microsoft YaHei/Arial，字号在成达基准2.2mm上扩大1.5倍至3.3mm；长焊口号采用同一字长缩放规则。边框宽高同比扩大1.5倍，焊口圆半径从3mm变为4.5mm，管理框高度从2.8mm变为4.2mm，布局使用同一实际尺寸。所有边框fill=none，已取消白色引线衬底；字号和边框放大后重新求解布局。焊点类型SF/FF保留在清单，圆圈内只显示真实焊口编号。管理线宽0.2mm、焊口线宽0.25mm。标注框避让原线及文字；全页联合求解新增标注间冲突，原图文字框及线段相交另计，不与新增标注零冲突混用。折线版及原字号直线版已归档在选图目录的SHA解析验证下。

新版焊口表的五条管线共363道焊口编号、166个支架编号、20个阀门编号、40处法兰管理编号，以及141个正式管段号。两个管段跨页，管段共标注143处，全套29页共732处标注；最密集页57处。五张演示页另汇成5页合册。原图文字包围框和主体/尺寸线的相交单独保留记录，包围框相交不能直接等同于字符轮廓遮挡或整图无碰撞。前期未含管段的589处标注版本及初版折线记录均已归档，不作为当前版数量。

## 独立签字补充核对版

`workflow/create_signature_review.py`用于制作可审阅的外部资源补充版，不改变纯SHA转换入口。它从同图号、同版原PDF提取3处修订栏和2处专业栏签字，保留软蒙版及实际纸面位置；不是SHA签字解码，也不表示重新签署或批准。

```powershell
python parse-piping-sha/profiles/uae/workflow/create_signature_review.py --reference-dir '<原始3. NativeFiles目录>' --native-dir '<纯SHA核对PDF目录>' --asset-dir '<签字资源目录>' --output-dir '<独立签字补充核对目录>'
```

签字图像、来源PDF/原生PDF/SHA的SHA256、逐图坐标、输出哈希保存在资源目录的`manifest.json`。输出目录只含`_SHA_签字补充核对.pdf`，标题和PDF元数据标明补充资源属性。已有结果需`--force`；源PDF和纯SHA结果不会覆盖。当前仅验证本批A3布局与5个签字图像，未知旋转、镜像和不同签字数量明确报错。生成器变更后需重新生成补充版，避免与新的纯SHA结果脱节。
